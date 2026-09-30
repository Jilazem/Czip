# Archived Super-Context Test: Searchable History Beyond the Model Window

**TL;DR:** We dumped a **748,800-token** corpus into a compressed, searchable session
archive (Czip / HKP1) and quizzed a local model (**GLM-5.3-Flash-EXL3**, 262K window,
2× NVIDIA GB10) over it.

| Arm | Accuracy | Avg prompt |
|---|---|---|
| **czip RAG (single-hop retrieval)** | **100% (80/80)** | **112 tokens** |
| Blind control (same questions, no czip) | **0% (0/40)** | — |
| 2-hop chain (cross-record relation) | 30% (6/20) | ~2× hops |

The model answered from a corpus **6,685× larger** than the average single-hop
prompt. The blind control's zero correct answers is consistent with the
needed facts being retrieved from the archive rather than guessed.

## The claim

> For this synthetic single-hop task, a searchable archive lets a finite-window
> model answer from a history larger than its prompt window.

## Setup

- **Corpus:** 240 synthetic "memorandum" packets (~12K chars each, dense Turkish
  filler text) with hidden needle records — 180 single needles (`IGNE-SB-NNNN` →
  value), 60 two-stage records (value → final counterpart). Total added raw context:
  **2,995,200 characters ≈ 748,800 tokens**, packed on top of a pre-existing
  638-packet archive.
- **Archive:** [Czip](../README.md) — compresses agent sessions into HKP1 packs with
  a full-text + vector-ish block index (`czip arsivara`, `czip ara`, `czip aralik`).
- **Model:** GLM-5.3-Flash-EXL3 served locally (tensorfold, 2× GB10, TP2), 262K
  window. Extraction calls ran thinking-off for speed.
- **Method:** 160 questions across three arms:
  1. **Single-hop RAG:** `czip arsivara <code>` → ~280-char snippet → model extracts
     the record value.
  2. **Blind control:** same questions with *no* retrieval.
  3. **2-hop chain:** retrieve record A → extract intermediate token → retrieve
     record B → answer from the second snippet.

![Super-Context Proof — 100% / 0% / 30%](superbaglam-chart.png)

## Results

- **100% (80/80)** single-hop recall at **112 average prompt tokens**. Perfect recall
  over ~750K tokens of raw text while paying for roughly a paragraph.
- **0% (40/40 missed)** without the archive. The model cannot guess 240 random
  codes — so every "yes" above is genuinely retrieval, not parametric memory.
- **30% (6/20)** on 2-hop chains — honest weak spot: second-hop snippet selection
  under a short output budget. Known issue, being tightened (block-range reads).

### What the numbers mean

| Comparison | Raw-context route | czip route |
|---|---|---|
| Access a 748,800-token corpus | full-context input needs ≥ 750K tokens | **112-token** average single-hop prompts in this run |
| Input cost | grows with supplied history | depends on retrieved snippets and tool calls |
| Retained history | limited by the supplied prompt | stored in a searchable archive on disk |

A 262K window cannot hold this corpus raw. Czip stores it outside the prompt
and retrieves selected evidence. Retrieval quality and per-question cost can
change as the archive grows; this run does not establish a constant-cost bound.

## Reproduction status

This page is an archived run report. Its original harness
(`03-SCRIPTS/czip_superbaglam_provasi.py`) and raw per-question outputs are
not included in this repository, so this result cannot currently be
independently reproduced from this page alone. The new
[Continuity Gauntlet](https://github.com/Jilazem/czip-continuity-gauntlet)
publishes its generator, runner, scoring rules, and raw-run report format.

## Limitations (kept in)

- Synthetic needles measure *retrieval fidelity*, not reasoning depth.
- 2-hop chaining at 30% — retrieval glue, not window failure.
- Fill text is Turkish; code/value tokens are ASCII — language-neutral for the
  needle task.

## Takeaway

**Keep the source history and measure retrieval.** This run shows strong
single-hop recall and a weak two-hop result. The model window remains finite.

---

*Run: 2026-09-29 · Harness: `czip_superbaglam_provasi.py` · Report:
`czip-superbaglam-20260929-195412.md`*
