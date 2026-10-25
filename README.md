<h1 align="center">Kenzan (検算)</h1>

<p align="center"><em>検算: checking a calculation by working it again</em></p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Tests: 165 passed, 7 skipped" src="https://img.shields.io/badge/tests-165%20passed%2C%207%20skipped-brightgreen">
</p>

Kenzan turns pages of Japanese financial statements into verified tables, and uses accounting
identities to decide which pages can be trusted without human review.

- **Method:** [docs/METHOD.md](docs/METHOD.md)
- **Results:** [docs/RESULTS.md](docs/RESULTS.md)
- **Benchmark:** [docs/BENCHMARK.md](docs/BENCHMARK.md)
- **Ground truth:** [docs/METHOD.md#3-ground-truth](docs/METHOD.md#3-ground-truth)
- **Reproducing:** [docs/RESULTS.md#reproducing](docs/RESULTS.md#reproducing)
- **Integrity (AI involvement, ground-truth coupling, chat log):** [docs/INTEGRITY.md](docs/INTEGRITY.md)
- **VLM run (Google Colab):** [notebooks/vlm_heldout_v3.ipynb](notebooks/vlm_heldout_v3.ipynb), [open in Colab](https://colab.research.google.com/github/vedantjhunjhunwal/Kenzan/blob/main/notebooks/vlm_heldout_v3.ipynb)

It provides:

- a parser for balance-sheet pages, born-digital or scanned:
  - born-digital pages go through the text layer;
  - scanned pages are deskewed and read with Japanese OCR;
  - optionally, an open VLM (Qwen3-VL-8B) reads scans instead, and its read goes through the
    same checks; it is implemented and tested with a fake model, not yet run on a real one;
  - either way the output is cells, presentation and calculation trees, and per-cell correction
    marks for 訂正 (amended) filings;
  - labels printed over several lines are joined (row spans), and header cells over several columns
    are recorded as spans; a layout the rules can't decide goes to review (rule A11);
- a definition of "correct" with separate dimensions:
  - printed values, labels, paths, structure, unit, period assignment and scaled (yen) values, each
    scored on its own, and `page_correct` only when all of them match;
  - the historical cell F1 and structure metrics, kept for comparison (cell F1 can't see the unit);
  - label-free consistency for pages with no ground truth;
- rounding-aware identity checks: a sign-aware truncation window, plus *coverage*, so a check that
  can't be evaluated never counts as a pass;
- an acceptance rule that sends any page with a failed, missing or solver-derived check to review.
  On correct born-digital pages it accepts 8 of 9. On corrupted reads of those pages it accepts
  1128 of 2960: identity checks catch arithmetic errors, not a wrong unit, swapped columns,
  consistent value changes, line-item labels or indentation. It accepted no degraded scan, but no
  degraded scan was fully correct either, so the scan path has no accept-side evidence;
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
| VLM on the same 8 scans | **not run**: the smoke test on the clean page failed the gate (20/74 path-keyed, 74/74 value-correct; wrong indentation) | `stress/out_vlm_smoke/` |
| Real filing (Nintendo FY2026): benchmark XBRL extractor + ground-truth builder only | 88 values, 0 mismatches vs tagged facts; 28 identity checks evaluated, 0 failed (26 template + 2 dynamic). PDF parser not yet run on this filing | `bench/results/S100Y9NX/` |
| New metric on the stored scan outputs: unit missing or wrong (scoring only) | unseen v2: 4 of 9 pages; held-out v3: 2 of 8. Page-correct degraded pages: 0 in every run | `stress/dimensions_all_runs.json` |
| Acceptance, correct born-digital pages (reference, geometry, synthetic layouts) | 8 of 9 accepted, 1 sent to review | `stress/out_acceptance_eval/` |
| Acceptance, corrupted reads of the 8 accepted pages (unit, period, sign, digit, consistent-value, missing, label, hierarchy) | 1128 of 2960 incorrect reads accepted (38.1%) | `stress/out_acceptance_eval/` |
| Wrapped labels / spanning headers, 6 synthetic fixtures | page-correct 0 of 6 → 5 of 6; accepted 0 → 4; ambiguous layouts go to review | [RESULTS.md §8](docs/RESULTS.md#8-wrapped-labels-and-spanning-headers-synthetic-layout-fixtures) |
| New real documents through the PDF parser | **not run**: downloads blocked by the session's network policy; nothing claimed | [RESULTS.md §9](docs/RESULTS.md#9-real-documents-through-the-pdf-parser-not-run) |

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

Expected:

| Environment | Result |
|---|---|
| with the Japanese OCR model (`bash scripts/setup_tesseract.sh`) | **165 passed, 7 skipped** |
| without the `jpn` model | **151 passed, 21 skipped** (the OCR and VLM second-reading tests skip) |
| with the model and the Nintendo XBRL zip | not run in this revision (it was 141 passed before the 31 new tests) |

The seven tests that always skip without it need a real EDINET filing (Nintendo, docID S100Y9NX),
which isn't redistributed here: download its XBRL zip from EDINET to
`tests/fixtures/edinet/S100Y9NX_xbrl.zip`. `bash scripts/run_all.sh` reproduces every result,
including the stress sets.

## Limitations

1. **One reference page.** "Unseen" means unseen *degradations* of one page. One real filing has been
   run through the XBRL extractor and ground-truth builder, but not yet through the PDF parser.
   Generalization across filings is the purpose of JP-BS-Bench, and it is untested. The runner for it
   (`bench/eval_real.py`) is ready; the filings couldn't be downloaded in this session.
2. **The scan path is weak.** OCR reads △ as other characters, and misread header labels break the
   calculation-tree rules. Best held-out cell F1 is about 85%. The checks catch these pages, but they
   don't repair them.
3. **The "0 false accepts" results first used an older rule.** Acceptance rule A1–A5 could not
   detect swapped value columns, a wrong unit, a wrong hierarchy, or a last-digit error when the
   values are consistent (found on the VLM smoke page and with a fake VLM). Rules A6–A10 fix this.
   The held-out sets, re-scored once under A1–A10, keep the same decisions (still reject-side only).
4. **Per-cell agreement is strict.** On dev scans, a perfect VLM read survives A9 on 1 page of 10,
   because Tesseract misreads at least one number on the others.
5. **Accept-side untested on scans.** No degraded page was fully correct, so it isn't shown yet that
   the rule accepts a *good* scan. Ground truth and pipeline also share label-normalization code
   ([INTEGRITY.md](docs/INTEGRITY.md)).
6. **Acceptance relies on arithmetic.** On born-digital pages an accept means "the identities hold
   and the layout was certain". A wrong unit, swapped columns, consistent value changes, line-item
   label typos and indentation errors are accepted ([RESULTS.md §7](docs/RESULTS.md#7-acceptance-on-correct-and-corrupted-pages)).
7. **Layout scope.** Single-table statement pages. Wrapped labels and spanning header cells are
   handled for the patterns in RESULTS §8, tested on synthetic pages only. There is no cross-page
   stitching and no general PDF table support.

## AI assistance

Kenzan was built with Claude (Anthropic) as a coding and research assistant; every commit is authored
by it. Which annotations were AI-made, and how the ground truth is coupled to the pipeline, is in
[docs/INTEGRITY.md](docs/INTEGRITY.md). **The full original chat log is not in this repository. It is a
required external attachment to the submission** and has not been reconstructed here.
