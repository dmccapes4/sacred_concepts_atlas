# EMOJI DICTIONARY — Sacred Concepts Atlas

Reference for the emoji prefixes used in orchestration logs (`atlas_query.py`,
`agent_conceptor.py`). Each emoji has one fixed meaning; scripts and docs must
use them consistently.

## Traditions (used wherever a page/section's origin matters)

| Emoji | Meaning |
|-------|---------|
| 🕎 | Judaism / Tanakh |
| ✝️ | Christianity / Bible |
| ☪️ | Islam / Quran |

## Agents & calls

| Emoji | Meaning |
|-------|---------|
| ⚡ | Agent call in fast (non-thinking) mode — probe, report, router |
| 🧠 | Agent call in thinking mode — gap, evidence, classifier |
| 🔭 | PROBE agent: designing the retrieval plan |
| 🔬 | GAP agent: examining results for holes and irrelevance |
| 📜 | REPORT agent: writing the structured report |
| 🗺️ | EVIDENCE agent: building the evidence map |
| 🧩 | ROUTER agent (ingestion): screening the concept registry |
| 🎓 | CLASSIFIER agent (ingestion): final concept assignment |

## Retrieval

| Emoji | Meaning |
|-------|---------|
| 🔑 | Term query (BM25 / exact keywords, language-siloed) |
| 🧭 | Semantic query (embedding search, cross-lingual) |
| 🕸️ | Fusion: merging ranked lists (reciprocal-rank fusion) |
| 📄 | Page (retrieval unit) / context assembly |
| ✂️ | Truncation to char budget by fusion score |
| ⚖️ | Relevance scoring (gap stage) |
| 🗑️ | Page dropped by the relevance gate |
| 🕳️ | Gap identified: missing coverage in retrieved material |
| ➕ | Follow-up queries added (second retrieval pass) |
| 🏷️ | Concept signature attached to a page/section |

## Concept space (ingestion)

| Emoji | Meaning |
|-------|---------|
| 📖 | Section being processed |
| 🌱 | New concept created in the registry |
| ♻️ | Existing concept reused |
| 🚧 | New-concept proposal gated by rising threshold τ(n) |
| 🧮 | Registry size / τ state |
| ⚓ | Anchor verse (verse-anchored evidence retrieval) / parallel-text match |

## Graph (Phase 4)

| Emoji | Meaning |
|-------|---------|
| 🔗 | Edge set materialized (kind + count) |
| 💎 | Discovery-queue candidate (conceptual link, no structural explanation) |
| 🌍 | Cross-tradition pair |

## System

| Emoji | Meaning |
|-------|---------|
| ❓ | User query (query pipeline header) |
| 🚀 | Run started (parameters echoed) |
| 📚 | Corpus / source dictionary |
| 💡 | New-concept proposal (pre-gate; may become 🌱 or 🚧) |
| § | Report section title |
| ⏱️ | Stage timing |
| ⏳ | Progress + ETA (avg s/section, projected finish) |
| 💾 | Artifact saved to disk |
| ✅ | Check passed / stage complete |
| ⚠️ | Warning: retry, empty result, degraded path |
| 📡 | Cloud API unreachable — transport backoff before retry |
| ❌ | Error / stage failed |
| ⏸️ | Run paused (partial pass; resumable) |
