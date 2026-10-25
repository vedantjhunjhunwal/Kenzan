<h1 align="center">Kenzan (検算)</h1>

<p align="center"><em>検算: checking a calculation by working it again</em></p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Tests: 106 passed, 6 skipped" src="https://img.shields.io/badge/tests-106%20passed%2C%206%20skipped-brightgreen">
</p>

Kenzan turns pages of Japanese financial statements into verified tables, and uses accounting
identities to decide which pages can be trusted without human review.

- **Method:** [docs/METHOD.md](docs/METHOD.md)
- **Results:** [docs/RESULTS.md](docs/RESULTS.md)
- **Benchmark:** [docs/BENCHMARK.md](docs/BENCHMARK.md)
- **Ground truth:** [docs/METHOD.md#3-ground-truth](docs/METHOD.md#3-ground-truth)
- **Reproducing:** [docs/RESULTS.md#reproducing](docs/RESULTS.md#reproducing)

It provides:

- a parser for balance-sheet pages, born-digital or scanned:
  - born-digital pages go through the text layer;
  - scanned pages are deskewed and read with Japanese OCR;
  - optionally, an open VLM (Qwen3-VL-8B) reads scans instead, and its read goes through the
    same checks; it is implemented and tested with a fake model, not yet run on a real one;
  - either way the output is cells, presentation and calculation trees, and per-cell correction
    marks for 訂正 (amended) filings;
- a three-level definition of "correct":
  - exact cell values at page precision;
  - structure (presentation and calculation parents);
  - label-free consistency for pages with no ground truth;
- rounding-aware identity checks: a sign-aware truncation window, plus *coverage*, so a check that
  can't be evaluated never counts as a pass;
- an acceptance rule that sends any page with a failed, missing or solver-derived check to review.
  On the stress test it accepted none of the 19 degraded pages and accepted the born-digital page.
  So it made 0 false accepts, but only because it accepted nothing degraded: whether it accepts a
  good scan is untested;
- EDINET / XBRL tooling for ground truth at scale ("JP-BS-Bench"):
  - an extractor for hand-downloaded filings, with an optional API sampler;
  - a ground-truth builder from inline XBRL;
  - a company-level dev/test split that was locked in advance.

## Results at a glance

| | Result | Source |
|---|---|---|
| Reference page, born-digital | 74/74 cells, F1 100.0%; calc parent 100.0%; corrections 100.0%; accepted | `stress/out_geometry/` |
| Same page scaled / shifted / on A3 | all 100%, all accepted | `stress/out_geometry/` |
| Scanned variants, **unseen** (held-out, 9) | mean cell F1 37.8% → **60.9%** (round 1 → current) | `stress/summary_all_runs.json` |
| Scanned variants, seen (10; motivated the fixes, not evidence) | 31.7% → 72.7% | `stress/summary_all_runs.json` |
| Acceptance on degraded pages | 0 of 19 degraded pages accepted, hence 0 false accepts (not an accept-side result) | `stress/summary_all_runs.json` |
| Held-out v3: 8 new degradations of the same page, not new documents (frozen before any VLM code), Tesseract | mean cell F1 70.7%; 0 of 8 accepted, 0 false accepts | `stress/out_heldout_v3/results.json` |
| VLM on the same 8 scans | **not run yet** (code, model choice and protocol ready: [docs/VLM.md](docs/VLM.md)) | — |
| Real filing (Nintendo FY2026): benchmark XBRL extractor + ground-truth builder only | 88 values, 0 mismatches vs tagged facts; 28 identity checks evaluated, 0 failed (26 template + 2 dynamic). PDF parser not yet run on this filing | `bench/results/S100Y9NX/` |

Each number comes from the file named next to it; the full tables are in [docs/RESULTS.md](docs/RESULTS.md).
The held-out set was hashed before the code it tests was written, and code fingerprints are logged
before every evaluation (`stress/FREEZE_LOG.txt`).

## Quick start

```bash
git clone https://github.com/vedantjhunjhunwal/Kenzan.git && cd Kenzan
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# parse a page -> JSON + wide CSV + long CSV, with an accept/review decision
.venv/bin/python -m docparse.pipeline tests/fixtures/balance_sheet_after_correction.pdf out/

# score it against the hand-verified ground truth
.venv/bin/python -m docparse.evaluate tests/fixtures/balance_sheet_after_correction.pdf \
    gt/out/ground_truth out/eval
```

The scan path needs Tesseract with the Japanese model: `apt-get install tesseract-ocr tesseract-ocr-jpn`,
or `bash scripts/setup_tesseract.sh`.

## Testing

```bash
.venv/bin/python -m pytest -q tests
```

Expected: **106 passed, 6 skipped**. The six skipped tests need a real EDINET filing (Nintendo,
docID S100Y9NX), which isn't redistributed here. Download its XBRL zip from EDINET to
`tests/fixtures/edinet/S100Y9NX_xbrl.zip` and all 112 run. `bash scripts/run_all.sh` reproduces
every result, including the stress sets.

## Limitations

1. **One reference page.** "Unseen" means unseen *degradations* of one page. One real filing has been
   run through the XBRL extractor and ground-truth builder, but not yet through the PDF parser. Generalization across filings is the purpose of JP-BS-Bench, and it is
   untested.
2. **The scan path is weak.** OCR reads △ as other characters, and misread header labels break the
   calculation-tree rules. Best held-out cell F1 is about 85%. The checks catch these pages, but they
   don't repair them.
3. **Accept-side untested on scans.** No degraded page was fully correct, so it isn't shown yet that
   the rule accepts a *good* scan. Ground truth and pipeline also share label-normalization code.

## AI assistance

Kenzan was built with Claude (Anthropic) as a coding and research assistant. The full chat logs were
submitted alongside this repository.
