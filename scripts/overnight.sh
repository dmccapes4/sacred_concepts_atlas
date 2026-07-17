#!/usr/bin/env bash
# Overnight pipeline: eval harness -> gate -> cold-start full ingestion,
# babysat by the watchdog (auto-kill on abort criteria).
#
# Usage (from anywhere, in a terminal that can stay open):
#   bash scripts/overnight.sh              # proceed on harness PASS or WARN
#   STRICT=1 bash scripts/overnight.sh     # require a clean PASS
#
# Gates:
#   harness exit 0 (pass)     -> ingest
#   harness exit 1 (warnings) -> ingest, unless STRICT=1
#   harness exit 2 (failures) -> stop, nothing touched
#
# Safety:
#   - refuses to auto-wipe concept state if > 50 sections are already signed
#     (protects a real half-finished run; smoke-test debris is always < 50)
#   - watchdog runs every 10 min; on ABORT (exit 2) ingestion is killed
#   - everything (harness, ingestion, watchdog) tees to one timestamped log

set -u
cd "$(dirname "$0")/.."
PY=venv/bin/python
SQL="$PY scripts/sql.py db/atlas.db"
TS=$(date +%Y%m%d_%H%M%S)
LOG=runs/overnight_${TS}.log
mkdir -p runs
exec > >(tee -a "$LOG") 2>&1

echo "🚀 overnight pipeline started $(date)  (log: $LOG)"

# ---- 1. eval harness (pre-flight gate) --------------------------------------
$PY scripts/eval_harness.py
HARNESS_RC=$?
case $HARNESS_RC in
  0) echo "✅ harness: clean pass — proceeding" ;;
  1) if [ "${STRICT:-0}" = "1" ]; then
       echo "❌ harness: warnings and STRICT=1 — stopping"; exit 1
     fi
     echo "⚠️ harness: warnings only — proceeding (see summary above)" ;;
  *) echo "❌ harness: failures (exit $HARNESS_RC) — stopping, nothing touched"
     exit 2 ;;
esac

# ---- 2. cold-start reset (guarded) ------------------------------------------
SIGNED=$($SQL "SELECT COUNT(DISTINCT section_id) FROM section_concepts")
if [ "$SIGNED" -gt 50 ]; then
  echo "❌ $SIGNED sections already signed — refusing to auto-wipe a run this"
  echo "   large. Wipe manually (make agents-reset CONFIRM=1) or resume it."
  exit 2
fi
$SQL "DELETE FROM section_concepts;"
$SQL "DELETE FROM concepts;"
$SQL "DELETE FROM runs;"
echo "✅ concept state wiped ($SIGNED leftover signed sections discarded) — cold registry"

# ---- 3. full ingestion pass --------------------------------------------------
$PY scripts/agent_conceptor.py --db db/atlas.db \
  --model atlas-conceptor --embed-model bge-m3 \
  --tau0 0.55 --tau-max 0.92 --tau-k 150 --order interleaved &
ING_PID=$!
echo "🚀 ingestion launched (pid $ING_PID) — watchdog checks every 10 min"

# ---- 4. watchdog loop (abort automation) -------------------------------------
sleep 600   # warm-up before the first check
while kill -0 "$ING_PID" 2>/dev/null; do
  $PY scripts/ingestion_watchdog.py
  WD_RC=$?
  if [ $WD_RC -eq 2 ]; then
    echo "❌ watchdog ABORT — killing ingestion (pid $ING_PID)"
    kill "$ING_PID" 2>/dev/null
    wait "$ING_PID" 2>/dev/null
    echo "❌ overnight pipeline aborted $(date) — diagnose via the watchdog"
    echo "   output above and runs/<run_id>/decisions.jsonl, then resume with:"
    echo "   make agent-resume"
    exit 2
  fi
  # sleep 10 min, but wake early if ingestion ends
  for _ in $(seq 60); do
    kill -0 "$ING_PID" 2>/dev/null || break
    sleep 10
  done
done

wait "$ING_PID"
ING_RC=$?
if [ $ING_RC -eq 0 ]; then
  echo "✅ overnight pipeline complete $(date)"
  $PY scripts/ingestion_watchdog.py --run-id \
    "$($SQL "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1")" || true
else
  echo "❌ ingestion exited $ING_RC $(date) — resume with: make agent-resume"
  exit $ING_RC
fi
