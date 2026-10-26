# Kenzan: submission summary

Kenzan parses Japanese balance-sheet pages (連結貸借対照表) into tables. It uses accounting identities
to decide which pages can skip human review. Every number below is recomputed from the named file by
`scripts/audit_claims.py` ([docs/AUDIT.md](docs/AUDIT.md)).

## What "correct" means

1. **Cells.** Precision, recall and F1 on (label path, period) → (value, nil, sign), exact at page
   precision.
2. **Structure.** Presentation parent and level, calculation parent, row type, and 訂正 marks.
3. **Label-free consistency.** Identity checks within a sign-aware truncation window, plus
   *coverage*: a check that can't be evaluated never counts as a pass.
4. **Per dimension and per page (added 2026-10-25).** Printed values, labels, paths, structure,
   unit, period assignment and scaled values are each scored separately. `page_correct` requires all
   of them. Levels 1–2 are kept for comparison; level 1 can't see the unit.

The acceptance rule (A1–A11) accepts a page only if every required check is evaluable and passes, the
unit and the column periods were read from the page, no subtotal is left outside the calculation tree,
the layout reconstruction was certain (A11), and (VLM route) a second OCR reading agrees on the unit,
columns, indentation and every cell it read.

## Headline results

| Result | Source |
|---|---|
| Born-digital reference page: 74/74 cells, F1 100.0%, accepted | `stress/out_geometry/geometry_results.json` |
| Unseen scan degradations (9): mean cell F1 37.8% → 60.9% (round 1 → v2) | `stress/summary_all_runs.json` |
| Held-out v3: 8 new degradations of the same page (not new documents), frozen before any VLM code. Tesseract: mean cell F1 70.7% | `stress/out_heldout_v3/results.json` |
| Acceptance: 0 of 27 degraded pages accepted, hence 0 false accepts. This tests only the reject side. It was first obtained with rule A1–A5, which could not detect column swaps or unit errors; re-scored once under A1–A10, the decisions are unchanged and cell F1 is identical | the two files above; `stress/out_*_rescore_A10/` |
| Dev check of the VLM-route rule (fake VLM, 10 seen scans, 3816 single injected errors each): errors accepted 1341/38160 under A1–A8, 21/38160 under A1–A10; a perfect read is accepted on 7/10 → 1/10 pages | `stress/out_vlm_dev_seen/` |
| VLM smoke test (clean reference page, not held-out): 20/74 cells path-keyed, 74/74 value-correct; accepted by A1–A5 despite wrong indentation on 31 of 43 rows. Gate failed, so v3 was not run | `stress/out_vlm_smoke/` |
| Real filing (Nintendo): XBRL extractor and ground-truth builder only. 88 values, 0 mismatches; 28 checks, 0 failed | `bench/results/S100Y9NX/ground_truth_xbrl.json` |
| Per-dimension metric on the stored scan outputs (scoring only): unit missing or wrong on 4 of 9 unseen v2 pages and 2 of 8 held-out v3 pages, which cell F1 never showed. 0 degraded pages page-correct | `stress/dimensions_all_runs.json` |
| Acceptance on correct born-digital pages (reference, 3 geometry variants, 5 correct synthetic layouts): 8 of 9 accepted, 1 sent to review. Corrupted reads of the 8 accepted pages: 1128 of 2960 incorrect reads accepted (38.1%); identity checks miss unit, column-order, consistent-value, line-item label and indentation errors | `stress/out_acceptance_eval/` |
| Wrapped labels and spanning headers, 6 synthetic layout fixtures: page-correct 0 → 5, accepted 0 → 4; the two undecidable layouts go to review (A11). Seen scans unchanged | RESULTS.md §8 |

## What is NOT proven

- **Generalization across documents.** There is one reference page, and the PDF parser has not been
  run on any real filing yet. The real-PDF evaluation (dev/sealed-test split, [bench/DOWNLOADS.md](bench/DOWNLOADS.md))
  is waiting for the filings to be downloaded. On 2026-10-25 the session's network policy blocked
  EDINET, TDnet and the company sites, so nothing was downloaded and no result is claimed. The
  runner (`bench/eval_real.py`) is ready and tested on a synthetic filing only. The same goes for
  v3b, the planned scans of real-filing pages.
- **That an accept means a correct page.** On born-digital pages, acceptance catches arithmetic
  errors only. A wrong unit, swapped columns, consistent value changes, line-item label typos and
  indentation errors were accepted in the corruption study (RESULTS.md §7).
- **Wrapped-label handling on real filings.** It was tested on synthetic pages and checked for
  regressions on the seen scans only.
- **The accept side.** No degraded page was fully correct, so it isn't shown that a good scan gets
  accepted. "0 false accepts" comes from 0 accepts; it carries no false-accept-rate bound.
- **The VLM.** Run once, on the smoke page only: the gate failed (indentation), so no held-out
  page has a VLM result ([docs/VLM.md](docs/VLM.md)).
- **The old acceptance rule had holes.** A1–A5 could not detect swapped columns, a wrong unit, a
  wrong hierarchy, or a last-digit error with consistent values. A6–A10 fix these on fake-VLM cases.
  The price: on dev scans a perfect VLM read is accepted on only 1 page of 10, because Tesseract
  misreads at least one number on the others.
- **"VLM + checks" accepts.** If that arm accepts a page, nothing but the checks stands behind it:
  no OCR agreement. On this page the checks catch only 70.7% of single ones-digit errors. The arm
  was frozen after Tesseract-vs-ground-truth on v3 had been seen, but before any VLM output.
- **The OCR-agreement rule.** On held-out v3 it can't accept any page, even with a perfect read,
  because Tesseract never reads all the values right (`stress/out_heldout_v3/ocr_agreement_ceiling.json`).
- **Independence of ground truth.** The ground truth and the pipeline share label-normalization,
  path, value-parsing and check code ([docs/INTEGRITY.md](docs/INTEGRITY.md)). The reference
  transcription is recorded as human-verified, but the repository doesn't record whether a person or
  the AI typed it.

## AI assistance and chat log

Built with Claude (Anthropic). Commits name the repository owner as author since a history rewrite
on 2026-10-26, with a `Co-Authored-By: Claude` trailer on each; the original Claude-authored history is
on the `archive/claude-authored-*` branches. AI-made annotations and shared code
are listed in [docs/INTEGRITY.md](docs/INTEGRITY.md). **The full original chat log is not in this
repository. It is a required external attachment** and has not been reconstructed.

## Reproduce

```bash
git clone https://github.com/vedantjhunjhunwal/Kenzan.git && cd Kenzan && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHON=.venv/bin/python bash scripts/run_all.sh
```

Some steps depend on the environment:

- Without the Nintendo XBRL zip, 7 tests skip.
- With the Japanese OCR model: 165 passed, 7 skipped. Without it, 14 more tests skip (OCR and the
  VLM second reading): 151 passed, 21 skipped. The stress steps need `bash scripts/setup_tesseract.sh`.
- The VLM step needs `vlm_cache/`.

## More

- [docs/METHOD.md](docs/METHOD.md)
- [docs/RESULTS.md](docs/RESULTS.md)
- [docs/BENCHMARK.md](docs/BENCHMARK.md)
- [docs/VLM.md](docs/VLM.md)
- [docs/INTEGRITY.md](docs/INTEGRITY.md)
- Freeze logs: `stress/FREEZE_LOG.txt` and `bench/FREEZE_LOG.txt`. They record code fingerprints,
  spec hashes, and every aborted run and correction.
