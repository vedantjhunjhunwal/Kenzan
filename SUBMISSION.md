# Kenzan: submission summary

Kenzan parses Japanese balance-sheet pages (連結貸借対照表) into tables. It uses accounting identities
to decide which pages can skip human review. Every number below is recomputed from the named file by
`scripts/audit_claims.py` ([docs/AUDIT.md](docs/AUDIT.md)).

## What "correct" means: three levels

1. **Cells.** Precision, recall and F1 on (label path, period) → (value, nil, sign), exact at page
   precision.
2. **Structure.** Presentation parent and level, calculation parent, row type, and 訂正 marks.
3. **Label-free consistency.** Identity checks within a sign-aware truncation window, plus
   *coverage*: a check that can't be evaluated never counts as a pass.

The acceptance rule (A1–A5) accepts a page only if every required check is evaluable and passes.

## Headline results

| Result | Source |
|---|---|
| Born-digital reference page: 74/74 cells, F1 100.0%, accepted | `stress/out_geometry/geometry_results.json` |
| Unseen scan degradations (9): mean cell F1 37.8% → 60.9% (round 1 → v2) | `stress/summary_all_runs.json` |
| Held-out v3: 8 new degradations of the same page (not new documents), frozen before any VLM code. Tesseract: mean cell F1 70.7% | `stress/out_heldout_v3/results.json` |
| Acceptance: 0 of 27 degraded pages accepted, hence 0 false accepts. This tests only the reject side. | the two files above |
| Real filing (Nintendo): XBRL extractor and ground-truth builder only. 88 values, 0 mismatches; 28 checks, 0 failed | `bench/results/S100Y9NX/ground_truth_xbrl.json` |

## What is NOT proven

- **Generalization across documents.** There is one reference page, and the PDF parser has not been
  run on any real filing yet. The real-PDF evaluation (dev/sealed-test split, [bench/DOWNLOADS.md](bench/DOWNLOADS.md))
  is waiting for the filings to be downloaded. The same goes for v3b, the planned scans of
  real-filing pages.
- **The accept side.** No degraded page was fully correct, so it isn't shown that a good scan gets
  accepted. "0 false accepts" comes from 0 accepts; it carries no false-accept-rate bound.
- **The VLM.** The backend, the model choice (Qwen3-VL-8B) and the frozen protocol are ready
  ([docs/VLM.md](docs/VLM.md)), but no real model has been run (it needs a GPU).
- **"VLM + checks" accepts.** If that arm accepts a page, nothing but the checks stands behind it:
  no OCR agreement. On this page the checks catch only 70.7% of single ones-digit errors. The arm
  was frozen after Tesseract-vs-ground-truth on v3 had been seen, but before any VLM output.
- **The OCR-agreement rule.** On held-out v3 it can't accept any page, even with a perfect read,
  because Tesseract never reads all the values right (`stress/out_heldout_v3/ocr_agreement_ceiling.json`).
- **Independence of ground truth.** The ground truth and the pipeline share label-normalization code.

## Reproduce

```bash
git clone https://github.com/vedantjhunjhunwal/Kenzan.git && cd Kenzan && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHON=.venv/bin/python bash scripts/run_all.sh
```

Some steps depend on the environment:

- Without the Nintendo XBRL zip, 7 tests skip.
- Without the `tesseract` binary, 2 OCR tests skip. With the binary but no `jpn` model, 1 skips.
  The stress steps need `bash scripts/setup_tesseract.sh`.
- The VLM step needs `vlm_cache/`.

## More

- [docs/METHOD.md](docs/METHOD.md)
- [docs/RESULTS.md](docs/RESULTS.md)
- [docs/BENCHMARK.md](docs/BENCHMARK.md)
- [docs/VLM.md](docs/VLM.md)
- Freeze logs: `stress/FREEZE_LOG.txt` and `bench/FREEZE_LOG.txt`. They record code fingerprints,
  spec hashes, and every aborted run and correction.
