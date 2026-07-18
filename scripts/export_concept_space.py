#!/usr/bin/env python3
"""Export a concept-space snapshot as JSON + standalone HTML (shareable outside Cursor).

    python scripts/export_concept_space.py --db db/atlas.db
    python scripts/export_concept_space.py --run-id run_20260717_105303 --out-dir reviews
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tau(n: int, tau0=0.55, tau_max=0.92, k=150.0) -> float:
    return tau_max - (tau_max - tau0) * k / (k + n)


def snapshot(con: sqlite3.Connection, run: str) -> dict:
    n_signed = con.execute(
        "SELECT COUNT(DISTINCT section_id) FROM section_concepts WHERE run_id=?",
        (run,)).fetchone()[0]
    n_concepts = con.execute(
        "SELECT COUNT(*) FROM concepts WHERE status='active'").fetchone()[0]
    n_sections = con.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    model, started = con.execute(
        "SELECT model, started_at FROM runs WHERE run_id=?", (run,)).fetchone()

    by_trad = list(con.execute("""
        SELECT s.tradition, COUNT(DISTINCT sc.section_id)
        FROM section_concepts sc
        JOIN sections sec ON sec.section_id = sc.section_id
        JOIN sources s ON s.source_id = sec.source_id
        WHERE sc.run_id=? GROUP BY s.tradition ORDER BY s.tradition
    """, (run,)))
    src_tot = dict(con.execute("""
        SELECT s.tradition, COUNT(*) FROM sections sec
        JOIN sources s ON s.source_id = sec.source_id GROUP BY s.tradition
    """))

    sig_sizes = [r[0] for r in con.execute(
        "SELECT COUNT(*) FROM section_concepts WHERE run_id=? GROUP BY section_id",
        (run,))]
    max_w = [r[0] for r in con.execute(
        "SELECT MAX(weight) FROM section_concepts WHERE run_id=? GROUP BY section_id",
        (run,))]

    top = [{"name": n, "sections": ns, "influence": float(i), "avg": float(a)}
           for n, ns, i, a in con.execute("""
        SELECT c.name, COUNT(*), ROUND(SUM(sc.weight),2), ROUND(AVG(sc.weight),3)
        FROM section_concepts sc JOIN concepts c ON c.concept_id=sc.concept_id
        WHERE sc.run_id=? GROUP BY c.concept_id ORDER BY SUM(sc.weight) DESC LIMIT 15
    """, (run,))]

    cross3 = [{"name": n, "sections": ns, "influence": float(i)}
              for n, ns, i in con.execute("""
        SELECT c.name, COUNT(*), ROUND(SUM(sc.weight),2)
        FROM section_concepts sc
        JOIN concepts c ON c.concept_id=sc.concept_id
        JOIN sections sec ON sec.section_id=sc.section_id
        JOIN sources s ON s.source_id=sec.source_id
        WHERE sc.run_id=?
        GROUP BY c.concept_id HAVING COUNT(DISTINCT s.tradition)=3
        ORDER BY SUM(sc.weight) DESC LIMIT 12
    """, (run,))]

    def span(n: int) -> int:
        return con.execute(f"""
            SELECT COUNT(*) FROM (
              SELECT c.concept_id FROM section_concepts sc
              JOIN concepts c ON c.concept_id=sc.concept_id
              JOIN sections sec ON sec.section_id=sc.section_id
              JOIN sources s ON s.source_id=sec.source_id
              WHERE sc.run_id=? GROUP BY c.concept_id
              HAVING COUNT(DISTINCT s.tradition)={n})
        """, (run,)).fetchone()[0]

    reuse = dict(con.execute("""
        SELECT CASE WHEN n=1 THEN 'once' WHEN n BETWEEN 2 AND 5 THEN '2-5'
          WHEN n BETWEEN 6 AND 20 THEN '6-20' ELSE '21+' END, COUNT(*)
        FROM (SELECT concept_id, COUNT(*) n FROM section_concepts
              WHERE run_id=? GROUP BY concept_id) GROUP BY 1
    """, (run,)))

    newest = [{"name": n, "definition": d or ""} for n, d in con.execute(
        "SELECT name, substr(definition,1,280) FROM concepts WHERE status='active' "
        "ORDER BY created_at DESC LIMIT 8")]
    oldest = [{"name": n, "definition": d or ""} for n, d in con.execute(
        "SELECT name, substr(definition,1,240) FROM concepts WHERE status='active' "
        "ORDER BY created_at ASC LIMIT 6")]

    samples = {}
    for trad in ("Judaism", "Christianity", "Islam"):
        rows = con.execute("""
            SELECT sec.ref,
                   GROUP_CONCAT(c.name || ' ' || printf('%.2f', sc.weight), ' · ')
            FROM section_concepts sc
            JOIN sections sec ON sec.section_id=sc.section_id
            JOIN sources s ON s.source_id=sec.source_id
            JOIN concepts c ON c.concept_id=sc.concept_id
            WHERE sc.run_id=? AND s.tradition=?
            GROUP BY sc.section_id ORDER BY sec.seq DESC LIMIT 3
        """, (run, trad)).fetchall()
        samples[trad] = [{"ref": r, "sig": (sig or "")[:320]} for r, sig in rows]

    created = rejected = 0
    last_sec = None
    dec = ROOT / "runs" / run / "decisions.jsonl"
    if dec.exists():
        with open(dec) as f:
            for line in f:
                e = json.loads(line)
                if e.get("event") == "section_done":
                    created += len(e.get("new_created") or [])
                    rejected += len(e.get("rejected") or [])
                    last_sec = e.get("section")
    last_ref = None
    if last_sec:
        row = con.execute(
            "SELECT ref, source_id FROM sections WHERE section_id=?",
            (last_sec,)).fetchone()
        if row:
            last_ref = f"{row[0]} ({row[1]})"

    size_hist = Counter(sig_sizes)
    return {
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": run, "model": model, "started_at": started, "order": "interleaved",
        "signed": n_signed, "total": n_sections,
        "pct": round(100 * n_signed / n_sections, 1) if n_sections else 0,
        "registry": n_concepts, "tau": round(tau(n_concepts), 3),
        "avgSigSize": round(sum(sig_sizes) / len(sig_sizes), 2) if sig_sizes else 0,
        "avgMaxWeight": round(sum(max_w) / len(max_w), 3) if max_w else 0,
        "gate": {"created": created, "rejected": rejected},
        "traditionSpan": {"mono": span(1), "cross2": span(2), "cross3": span(3)},
        "reuse": reuse,
        "byTradition": [
            {"tradition": t, "signed": n, "total": src_tot[t],
             "pct": round(100 * n / src_tot[t], 1)}
            for t, n in by_trad],
        "sigSizeHist": [{"label": str(k), "value": v}
                        for k, v in sorted(size_hist.items())],
        "top": top, "cross3": cross3, "oldest": oldest, "newest": newest,
        "samples": samples, "lastRef": last_ref,
    }


def render_html(s: dict) -> str:
    def bar(segments, total=None):
        total = total or sum(v for _, v in segments) or 1
        parts = []
        colors = ["#c9a95c", "#6b8f71", "#7a8fa8", "#8a6b5c", "#9a917f"]
        for i, (lab, v) in enumerate(segments):
            w = 100 * v / total
            parts.append(
                f'<div class="seg" style="width:{w:.1f}%;background:{colors[i % len(colors)]}" '
                f'title="{escape(lab)}: {v}"></div>')
        return '<div class="bar">' + "".join(parts) + "</div>"

    def table(headers, rows):
        th = "".join(f"<th>{escape(h)}</th>" for h in headers)
        trs = []
        for row in rows:
            trs.append("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>")
        return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"

    bt = {b["tradition"]: b for b in s["byTradition"]}
    order = ["Judaism", "Christianity", "Islam"]
    prog_rows = [
        [t, bt[t]["signed"], bt[t]["total"], f"{bt[t]['pct']}%"]
        for t in order if t in bt]

    reject_pct = 0
    g = s["gate"]
    if g["created"] + g["rejected"]:
        reject_pct = round(100 * g["rejected"] / (g["created"] + g["rejected"]))

    top_rows = [[c["name"], c["sections"], f"{c['influence']:.1f}", f"{c['avg']:.3f}"]
                for c in s["top"]]
    cross_rows = [[c["name"], c["sections"], f"{c['influence']:.1f}"]
                  for c in s["cross3"]]

    sample_blocks = []
    for trad in order:
        items = "".join(
            f"<div class='sample'><strong>{escape(x['ref'])}</strong>"
            f"<div class='dim'>{escape(x['sig'])}</div></div>"
            for x in s["samples"].get(trad, []))
        pct = bt.get(trad, {}).get("pct", "?")
        sample_blocks.append(
            f"<section class='card'><h3>{escape(trad)} "
            f"<span class='pill'>{pct}% done</span></h3>{items}</section>")

    def cards(items, key="definition"):
        return "".join(
            f"<div class='card sm'><strong>{escape(c['name'])}</strong>"
            f"<div class='dim'>{escape(c.get(key) or c.get('def',''))}</div></div>"
            for c in items)

    hist_segs = [(h["label"], h["value"]) for h in s["sigSizeHist"]]
    reuse_order = ["once", "2-5", "6-20", "21+"]
    reuse_segs = [(k, s["reuse"].get(k, 0)) for k in reuse_order]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Concept space — {escape(s['run'])}</title>
<style>
  :root {{ --bg:#12100e; --panel:#1c1917; --ink:#e8e2d9; --dim:#9a917f;
           --line:#38322c; --gold:#c9a95c; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font: 15px/1.55 Georgia, 'Times New Roman', serif; padding: 28px 22px 60px; }}
  main {{ max-width: 920px; margin: 0 auto; }}
  h1 {{ font-size: 26px; font-weight: normal; color: var(--gold); margin: 0 0 6px; }}
  h2 {{ font-size: 18px; color: var(--gold); margin: 28px 0 10px;
        border-bottom: 1px solid var(--line); padding-bottom: 4px; }}
  h3 {{ font-size: 15px; margin: 0 0 10px; }}
  .dim {{ color: var(--dim); font-size: 13px; }}
  .stats {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin: 18px 0; }}
  .stat {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
           padding: 12px 14px; }}
  .stat .v {{ font-size: 22px; color: var(--gold); }}
  .stat .l {{ font-size: 12px; color: var(--dim); margin-top: 2px; }}
  .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
           padding: 14px 16px; margin: 10px 0; }}
  .card.sm {{ padding: 10px 12px; }}
  .callout {{ border-left: 3px solid var(--gold); background: var(--panel);
              padding: 12px 16px; margin: 16px 0; }}
  .bar {{ display:flex; height: 14px; background:#262220; border-radius: 4px; overflow:hidden; }}
  .seg {{ height:100%; }}
  table {{ width:100%; border-collapse: collapse; font-size: 13.5px; margin: 8px 0 16px; }}
  th, td {{ text-align:left; padding: 6px 8px; border-bottom: 1px solid var(--line); }}
  th {{ color: var(--dim); font-weight: normal; font-size: 12px; }}
  td:nth-child(n+2) {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .pill {{ display:inline-block; font-size:11px; color:var(--dim); border:1px solid var(--line);
           border-radius:999px; padding:1px 8px; margin-left:8px; }}
  .sample {{ margin-bottom: 10px; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
  @media (max-width:720px) {{ .stats, .grid2 {{ grid-template-columns:1fr 1fr; }} }}
  @media (max-width:480px) {{ .stats, .grid2 {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body><main>
  <h1>Concept space — GPT-4.1 run</h1>
  <p class="dim">{escape(s['run'])} · {escape(str(s['model']))} · order={escape(s.get('order',''))}
     · snapshot at ~{escape(str(s.get('lastRef') or '?'))}
     · exported {escape(s['exported_at'])}</p>

  <div class="stats">
    <div class="stat"><div class="v">{s['pct']}%</div><div class="l">Corpus signed</div></div>
    <div class="stat"><div class="v">{s['registry']}</div><div class="l">Active concepts</div></div>
    <div class="stat"><div class="v">{s['tau']}</div><div class="l">Novelty gate τ(n)</div></div>
    <div class="stat"><div class="v">{s['avgSigSize']}</div><div class="l">Avg concepts / signature</div></div>
  </div>

  <div class="callout">
    <strong>{s['signed']:,} / {s['total']:,} sections signed ({s['pct']}%).</strong>
    Quran {bt.get('Islam',{}).get('pct','?')}% · Tanakh {bt.get('Judaism',{}).get('pct','?')}% ·
    Bible {bt.get('Christianity',{}).get('pct','?')}%.
    Cross-tradition spine: {s['traditionSpan']['cross3']} concepts in all 3 ·
    gate rejects {g['rejected']:,} / creates {g['created']:,} ({reject_pct}% reject rate among novelty proposals).
  </div>

  <h2>Tradition coverage</h2>
  {table(['Tradition','Signed','Total','Share'], prog_rows)}

  <h2>Signature size</h2>
  <div class="card">
    {bar(hist_segs)}
    <p class="dim" style="margin:8px 0 0">Counts by concepts-per-section:
    {' · '.join(f"{h['label']}: {h['value']}" for h in s['sigSizeHist'])}
    · avg max weight {s['avgMaxWeight']}</p>
  </div>

  <h2>Concept reuse</h2>
  <div class="card">
    {bar(reuse_segs)}
    <p class="dim" style="margin:8px 0 0">
    {' · '.join(f"{k}: {s['reuse'].get(k,0)}" for k in reuse_order)}</p>
  </div>

  <h2>Top concepts by total influence</h2>
  <p class="dim">Influence = Σ weight across signed sections.</p>
  {table(['Concept','Sections','Σ weight','Avg weight'], top_rows)}

  <h2>Cross-tradition spine
    <span class="pill">{s['traditionSpan']['cross3']} in all 3</span>
    <span class="pill">{s['traditionSpan']['cross2']} in 2</span>
    <span class="pill">{s['traditionSpan']['mono']} mono</span>
  </h2>
  {table(['Concept (3 traditions)','Sections','Σ weight'], cross_rows)}

  <h2>Novelty gate</h2>
  <div class="stats">
    <div class="stat"><div class="v">{g['created']}</div><div class="l">Concepts minted</div></div>
    <div class="stat"><div class="v">{g['rejected']}</div><div class="l">Proposals gated</div></div>
    <div class="stat"><div class="v">{reject_pct}%</div><div class="l">Reject rate</div></div>
    <div class="stat"><div class="v">{s['tau']}</div><div class="l">Current τ</div></div>
  </div>

  <div class="grid2">
    <div>
      <h2>Founding concepts</h2>
      {cards(s['oldest'])}
    </div>
    <div>
      <h2>Newest concepts</h2>
      {cards(s['newest'])}
    </div>
  </div>

  <h2>Recent signatures</h2>
  {''.join(sample_blocks)}

  <p class="dim" style="margin-top:28px">Sacred Concepts Atlas · generated by scripts/export_concept_space.py</p>
</main></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "db/atlas.db"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out-dir", default=str(ROOT / "artifacts"))
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    run = args.run_id
    if not run:
        row = con.execute(
            "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row:
            print("no runs in db"); return 1
        run = row[0]

    s = snapshot(con, run)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = f"concept_space_{run}_{stamp}"
    json_path = out / f"{base}.json"
    html_path = out / f"{base}.html"
    # also stable "latest" names for easy linking
    latest_json = out / f"concept_space_{run}_latest.json"
    latest_html = out / f"concept_space_{run}_latest.html"

    json_path.write_text(json.dumps(s, indent=2, ensure_ascii=False))
    html = render_html(s)
    html_path.write_text(html)
    latest_json.write_text(json_path.read_text())
    latest_html.write_text(html)

    print(f"wrote {json_path}")
    print(f"wrote {html_path}")
    print(f"wrote {latest_html}")
    print(f"{s['pct']}% signed · {s['registry']} concepts · τ={s['tau']} · last={s['lastRef']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
