# Results

Every number here is copied from a result file in this repository. The file is named under each
table, and [Reproducing](#reproducing) regenerates them all. `scripts/audit_claims.py` re-checks every
number against its file; the latest audit is in [AUDIT.md](AUDIT.md). The definitions are in [METHOD.md](METHOD.md).

## 1. Reference page (born-digital) and geometry variants

The reference page is the 連結貸借対照表 page of an amended 決算短信 (`tests/fixtures/`). The ground
truth has 43 rows and 74 cells. Each geometry variant re-places the page's vector content: scaled to
80%, shifted 40 pt right, or put on an A3 page.

| Variant | Cells | F1 | Level | Pres. parent | Calc parent | Row type | is_corrected | Checks failed | Coverage | Decision |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| original | 74/74 | 100% | 100% | 100% | 100% | 100% | 100% | 0 | 46.2% | accept |
| 80% scale | 74/74 | 100% | 100% | 100% | 100% | 100% | 100% | 0 | 46.2% | accept |
| shifted 40 pt right | 74/74 | 100% | 100% | 100% | 100% | 100% | 100% | 0 | 46.2% | accept |
| A4 on an A3 page | 74/74 | 100% | 100% | 100% | 100% | 100% | 100% | 0 | 46.2% | accept |

Source: `stress/out_geometry/geometry_results.json`. Coverage is 12/26 because the page holds only
資産の部, so the liability and net-asset checks can't be evaluated.

This 100% is a regression baseline: the code was written while looking at this page. The geometry
variants show that the layout is learned per page, not memorised.

## 2. Stress test (scan path)

### Protocol

Recorded in `stress/FREEZE_LOG.txt`:

1. **Round 1.** The pipeline was frozen (fingerprint `9e905d908f7ad771`) and run on the *seen* set
   (`stress/specs/seen.json`). The results motivated three fixes: deskew, robust line height, and a
   content-based statement type.
2. **Held-out set locked.** The *unseen* set (`stress/specs/unseen.json`, sha256 `54e6b44c663d9567`,
   seed 20261024) was written and hashed **before** any round-2 code.
3. **Round 2.** The code was developed against unit tests, checked once on the seen set, then frozen
   at `888416e5df3fbc7c`. The unseen set was run once. The round-1 code (`baselines/round1/`) was
   then run on the same unseen PDFs as a baseline.

The fingerprint covers `docparse/*.py` except the scoring harness. One overlap with the seen set:
σ=10, q=50 and 1.5° occurred *together* in seen `scan_combo`, never alone. 0.5°, 120 dpi, 200 dpi
and `scan_200` are new on every axis.

### Unseen set: the evidence

| Variant | F1 round 1 | **F1 v2** | Value-correct cells¹ (r1 → v2) | Calc parent v2 | Checks failed | Coverage | Decision (reasons) | Main remaining causes |
|---|--:|--:|--:|--:|--:|--:|---|---|
| raster 200 dpi | 81.9% | **83.3%** | 59 → 60 | 90.7% | 8 | 38.5% | review (A2, A3) | sign lost ×9 |
| raster 120 dpi | 18.2% | **18.2%** | 23 → 23 | 51.2% | 9 | 15.4% | review (A2, A3) | digits misread ×29 |
| rotation +0.5° | 69.9% | **85.5%** | 56 → 62 | 90.7% | 8 | 38.5% | review (A2, A3) | sign lost ×8 |
| rotation −0.5° | 52.8% | **27.8%** | 53 → 60 | 86.0% | 8 | 34.6% | review (A2, A3) | header 「固定資産」 read as 「固定 資産 ーー」 → 40 path mismatches |
| rotation +1.5° | 0.0% | **83.0%** | 0 → 61 | 90.7% | 12 | 42.3% | review (A2, A3) | sign lost ×10 |
| rotation −1.5° | 0.0% | **84.1%** | 0 → 61 | 90.7% | 10 | 34.6% | review (A2, A3) | sign lost ×8 |
| noise σ=10 | 43.8% | **45.3%** | 51 → 52 | 67.4% | 6 | 26.9% | review (A2, A3, A5) | 「有形同定資産」 header → 21 path mismatches; cells missed ×11 |
| JPEG q=50 | 68.1% | **69.4%** | 55 → 56 | 86.0% | 11 | 38.5% | review (A2, A3, A5) | sign lost ×8, digits ×6 |
| 200 dpi + −0.75° + σ=8 + q=60 | 5.6% | **51.9%** | 4 → 52 | 69.8% | 6 | 15.4% | review (A2, A3) | header misread → 17 path mismatches; cells missed ×13 |
| **mean (9 degraded)** | **37.8%** | **60.9%** | | | | | **0 accepted** | |

Sources: `stress/out_v2_unseen/results.json`, `stress/out_v1_unseen/results.json`,
`stress/summary_all_runs.json`.

¹ A diagnostic, not the headline metric: cells whose value is right at a positionally aligned row,
even if a misread ancestor label changed the path.

Deskew recovered every angle to within 0.02°. The −0.5° drop in path-keyed F1 comes from one misread
header label, and the values improved there too (53 → 60).

### Seen set: not evidence

| Variant | F1 round 1 | F1 v2 | Decision v2 |
|---|--:|--:|---|
| raster 300 / 150 / 100 dpi | 83.9 / 82.8 / 0.0% | 86.7 / 85.5 / 0.0% | review |
| rotation +1 / −1 / +2 / −2° | 1.4 / 0.0 / 0.0 / 0.0% | 83.6 / 84.4 / 83.8 / 83.6% | review |
| noise σ=15 · JPEG q=30 · combined scan | 63.8 · 85.5 · 0.0% | 65.2 · 85.5 · 69.1% | review |
| **mean (10 degraded)** | **31.7%** | **72.7%** | 0 accepted |

Sources: `stress/out_v{1,2}_seen/results.json`. The fixes were motivated by these variants, so these
numbers overstate v2. Full tables with every metric: `stress/out_*/results.md`. Every wrong cell with
its cause: `stress/out_*/runs/<variant>/cell_diagnosis.json`.

## 3. Acceptance

| Run | Pages | Accepted | False accepts | Degraded pages fully correct |
|---|--:|--:|--:|--:|
| v2 seen | 11 | 1 (born-digital) | 0 | 0 / 10 |
| v2 unseen | 10 | 1 (born-digital) | 0 | 0 / 9 |
| geometry variants | 4 | 4 | 0 | n/a |

Source: `stress/summary_all_runs.json`, `stress/out_geometry/geometry_results.json`.

Every degraded page failed at least one identity (A2), except seen raster 100 dpi. That page was
caught by A3: its required checks couldn't be evaluated.

**0 false accepts here means 0 of 19 degraded pages accepted, hence 0 false accepts.** No degraded page was
fully correct, so this is a reject-side result only. It doesn't show that the rule *accepts* a good
scan, and with nothing degraded accepted, there is no false-accept *rate* to estimate.

**These runs used acceptance rule A1–A5, which had holes.** Identity checks are invariant to the unit and to swapping whole value columns, and template checks match totals by label, not by tree position. So A1–A5 would accept a page with a wrong unit, swapped columns, or a wrong hierarchy if its values were consistent. Fake-VLM tables and the VLM smoke page (§5) showed this. Rules A6–A8 (unit read from the page; column periods parsed in order; on the VLM route, a second OCR reading that confirms unit, columns and indentation) fix it, and are tested in `tests/test_vlm.py` and `tests/test_round2.py`. Rules A9 and A10 followed (see [VLM.md](VLM.md)).

**Re-score of the frozen held-out sets under the final rule (A1–A10).** This was done once, with
your approval, into new folders; the originals are kept. Code: commit edf7418, pushed before the
run.

| Set | Extraction metrics vs original | Accepted (old → new) | False accepts | New review reasons |
|---|---|--:|--:|---|
| unseen (`stress/out_v2_unseen_rescore_A10/`) | identical on all 10 pages | 1 → 1 (born-digital) | 0 | A10 on 5 pages, A6 on 4 |
| held-out v3 (`stress/out_heldout_v3_rescore_A10/`) | identical on all 9 pages | 1 → 1 (born-digital) | 0 | A10 on 7 pages, A6 on 2 |

No decision changed: every degraded page was already in review. Per-page tables:
`rescore_vs_original.md` in each folder.

**Dev measurement of the VLM-route rule (seen scans, fake VLM).** The fake VLM returns the
ground-truth table, or that table with one injected error. All 3816 single errors from
`gt/detection_power.py`'s error types were tried, on each of the 10 seen scans. The seen set is dev
data. Source: `stress/out_vlm_dev_seen/*/dev_seen.json`.

| Rule | Perfect read accepted | Single injected errors accepted |
|---|--:|--:|
| A1–A8 (commit 2cbbfa7) | 7/10 | 1341/38160 |
| final rule without A9 | 9/10 | 1723/38160 |
| final rule (A1–A10) | 1/10 | 21/38160 |
| A1–A10 with A9-suffix (pre-registered candidate, rejected: breaks the ≤ 38/38160 bound) | 8/10 | 84/38160 |

- **A9 stops last-digit errors.** These were almost all ones-digit errors inside the truncation
  window.
- **It also rejects 8 of the 10 perfect reads.** Tesseract truncates or misreads at least one number
  on those pages (e.g. 3,377,407 read as 407), and A9 counts that as disagreement.
- **The remaining 21 accepted errors** all fall on raster_300, in cells Tesseract left blank.

## 4. Real-document results (JP-BS-Bench extractor)

**What ran:** the benchmark's XBRL extractor (`bench/fetch_edinet.py --local`) and ground-truth builder
(`bench/build_gt_xbrl.py`), on the filing's XBRL only. **The PDF parser (`docparse`) has not been run on
this filing**: the PDF wasn't supplied, and `pilot_check.md` reports `pdf_missing`. Nothing in this
section is evidence about parsing PDFs.

**The filing.** Nintendo's 有価証券報告書 for the fiscal year ending 2026-03-31 (docID S100Y9NX,
J-GAAP, 百万円). It's a dev document, forced into dev because it was inspected while debugging.

**Outputs** (`bench/results/S100Y9NX/`):

- extractor output: `bs_rows.csv`, `ixbrl_bs.json`;
- ground truth built from the XBRL: `ground_truth_xbrl.*`;
- check report: `pilot_check.md`.

Notes: `bench/VERIFICATION.md`.

| Check | Result |
|---|---|
| Balance sheet found in the XBRL | by its 【連結貸借対照表】 heading; 56 rows (44 with values), 88 values, both years, scale 百万円 |
| Values vs the plain XBRL instance (`.xbrl`) | 88 / 88 equal, 0 mismatches (extractor reads the inline HTML) |
| Identity checks on the ground truth | 28 checks evaluable, 0 failed: 26 template (24 sums + 2 equalities, one per year) + 2 dynamic sums. `pilot_check.md` shows the same as "0/28" (failed / evaluable). Template coverage 26 / 26 |
| Subtotals vs sum of parts | all 26 sum checks are 0–3 above the sum of their parts (24 template + 2 dynamic; consistent with 百万円未満切捨て) |
| 資産合計 = 負債純資産合計 | equal in both years |
| Note marker in a value cell | 「※1 65,180」 read as 65,180 with ※1 as a footnote (not 165,180) |
| Re-run reproducibility | an independent `--local` run reproduced `bs_rows.csv` exactly |

Each check was done independently by the user outside the project and recomputed in the session.
This filing found three extractor bugs that synthetic tests had missed: the 2008 inline-XBRL
namespace, indentation by leading empty columns, and note markers inside value cells. All three are
fixed, with regression tests.

**Still untested on real data:**

- contra items stored as *positive* XBRL values: there are no 減価償却累計額 rows here, and the 4 △
  values are stored negative;
- the PDF side (page finding, end-total check, alignment);
- IFRS, 千円 and two-page balance sheets.

## 5. Held-out v3: Tesseract baseline (VLM pending)

**Held-out v3 is 8 new degradations of the same reference page, not new documents.** It tests robustness to unseen scan conditions, not generalization across filings.

A third degradation set was frozen before any VLM code existed: new seed, new rotation, noise and
JPEG values, and pure-raster 250 and 175 dpi. The details and one logged correction are in
`stress/FREEZE_LOG.txt`. Tesseract was run on it once, with the frozen v2 pipeline (`baselines/v2`,
fingerprint `888416e5df3fbc7c`). Source: `stress/out_heldout_v3/results.json`.

| Variant | Cell F1 | Calc parent | Checks failed | Decision |
|---|--:|--:|--:|---|
| raster 250 dpi | 75.5% | 72.1% | 8 | review |
| raster 175 dpi | 81.1% | 81.4% | 10 | review |
| rotation +0.8° | 74.6% | 74.4% | 10 | review |
| rotation −1.2° | 81.9% | 90.7% | 9 | review |
| noise σ=12 | 67.2% | 72.1% | 7 | review |
| JPEG q=40 | 67.6% | 76.7% | 3 | review |
| 250 dpi + 0.6° + σ=6 + q=55 | 39.4% | 62.8% | 7 | review |
| 175 dpi − 1.1° + σ=9 + q=45 | 78.5% | 83.7% | 5 | review |

- Mean cell F1 over the 8 degraded pages: **70.7%**. No page is fully correct and none is accepted,
  so there are 0 false accepts.
- Every page failed at least one check (A2). △ was lost 6–9 times per page.

**The OCR-agreement rule can't accept anything on this set.** The planned "VLM + checks + OCR"
arm only accepts a page if the Tesseract read has the same values per column as the VLM read. That
was checked against the ground truth itself, with no VLM involved: on 0 of 8 pages does Tesseract
agree with the correct values. The Tesseract read misses 15–22 of the correct values per page
(`stress/out_heldout_v3/ocr_agreement_ceiling.json`). So that arm accepts no page here, even
with a perfect VLM read. "VLM + checks" (A1–A5 alone) is reported next to it.

Any accept from "VLM + checks" has no OCR agreement behind it. It rests on the identity checks alone, and on this page they catch only 70.7% of single ones-digit errors (`gt/out/detection_power.txt`, from `gt/detection_power.py`). This arm was frozen after the Tesseract output on v3 had been compared with the v3 ground truth, but before any VLM output existed (`stress/FREEZE_LOG.txt`, "VLM METHOD FROZEN").

**Planned: held-out v3b, real-filing pages.** Once the filings in `bench/DOWNLOADS.md` are triaged, the balance-sheet pages of the sealed test filings will be rasterized with the v3 degradation parameters and scored against their XBRL ground truth. The pages, spec and code fingerprint will be locked before any of them is run, and each will be run once. That would be the first scan evidence on documents other than the reference page.

**VLM arms: not run.** The VLM was run once on Colab (commit 1ce7f94), on the smoke page only: the clean reference page at 300 dpi, which is not held-out. It scored 20/74 cells path-keyed, below the pre-registered 70/74 gate, so held-out v3 was not run. Its values were all right (74/74 value-correct); the indentation was wrong on 31 of 43 rows, so 54 correct values sat on wrong label paths. Rule A1–A5 still accepted the page: a structure-level false accept (`stress/out_vlm_smoke/diagnosis.md`). Details and the amendment that followed are in [VLM.md](VLM.md).

## 6. Per-dimension correctness (metric added 2026-10-25)

The cell metric above keys printed values on `(label_path, period_end)`. It never looks at the unit,
and a misread ancestor label costs every cell under it. `score_dimensions` (definitions in
[METHOD.md §2](METHOD.md#per-dimension-scores-and-complete-page-correctness-added-2026-10-25)) scores
each dimension separately, and `page_correct` needs all of them. The historical numbers in §1–§5 are
unchanged. The new metric was applied to the predictions **already stored** by every committed run:
scoring only, no page parsed again, no decision changed (`stress/score_dimensions_all.py` →
`stress/dimensions_all_runs.json`).

| Run (degraded pages) | Cell F1 (historical) | Values printed | Labels | Paths | Unit missing or wrong | Periods wrong | Scaled values | Page correct |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| seen, round 1 (10) | 31.7% | 32.7% | 70.2% | 53.3% | 2 of 10 | 3 of 10 | 16.1% | 0 of 10 |
| seen, v2 (10) | 72.7% | 73.0% | 84.4% | 82.3% | 6 of 10 | 1 of 10 | 25.0% | 0 of 10 |
| unseen, round 1 (9) | 37.8% | 45.2% | 79.1% | 58.1% | 2 of 9 | 1 of 9 | 28.7% | 0 of 9 |
| unseen, v2 (9) | 60.9% | 73.1% | 87.3% | 73.1% | 4 of 9 | 0 of 9 | 37.7% | 0 of 9 |
| held-out v3, v2 (8) | 70.7% | 74.7% | 90.7% | 84.6% | 2 of 8 | 0 of 8 | 55.2% | 0 of 8 |

The born-digital page and its 3 geometry variants are page-correct (4 of 4).

What the new columns show:

- **The unit was the hidden error.** On the unseen set, round 2 raised cell F1 from 37.8% to 60.9%,
  but the unit was missing or wrong on 4 of 9 pages (round 1: 2 of 9). On those pages every yen
  amount is wrong, which the cell metric can't see. On held-out v3, the two rotated scans (rot +0.8°,
  rot −1.2°) have no readable unit line, so their scaled values are 0.0% and 1.4% correct while their
  cell F1 is 74.6% and 81.9%. Acceptance already sent these pages to review (A6 under A1–A10).
- **Printed values are better than path-keyed F1 suggests** (unseen v2: 73.1% vs 60.9%): misread
  ancestor labels cost paths, not values.
- **No degraded page is fully correct** in any run, so the scan path has no accept-side evidence yet.

## 7. Acceptance on correct and corrupted pages

`stress/acceptance_eval.py` → `stress/out_acceptance_eval/` (code commit 3397796, pipeline
fingerprint `14deea7864c83094`, frozen in `stress/FREEZE_LOG.txt` before the run). **Dev data only:**
the reference page shaped the parser, and the synthetic pages were written for this work.

**Correct pages.** 10 born-digital base pages: the reference page, its 3 geometry variants, and the
6 synthetic layout fixtures (§8). 9 parse page-correct. Of those, **8 are accepted and 1 is sent to review**: `span_header_date`, a column-spanning header that carries a date, so A11 declines to guess.
The one page that doesn't parse correctly (`ambiguous_wrap`) goes to review.

**Corrupted reads.** Each run edits the text layer the parser reads (a wrapper backend). Then the
whole real pipeline runs, and the output is scored against the **true** ground truth. Every target on
every correct page was tried: 3223 runs, all 3223 outputs incorrect (no edit was a no-op). Detection
is measured on the **8 pages accepted when clean**, since a page in review anyway can't show a catch:
2960 runs, **1128 accepted (38.1%)**.

| Corruption | Runs | Incorrect and accepted | Caught by |
|---|--:|--:|---|
| unit 千円 → 百万円 | 8 | 8 | – (born-digital trusts its unit line) |
| unit line removed | 8 | 0 | A6 |
| period dates swapped (labels kept) | 8 | 0 | A7 |
| whole column headers swapped | 8 | 8 | – (a consistent page with its columns in the other order) |
| value columns swapped | 8 | 8 | – (identity checks are invariant to it) |
| △ added | 392 | 0 | A2 |
| △ dropped | 56 | 4 | A2; the 4 misses are 「△0」 → 「0」 |
| ones digit +1 | 448 | 244 | A2 catches 204; the rest fall inside the truncation window |
| tens digit +1 | 444 | 0 | A2 |
| one value +1,000 with every total above it (identities still hold) | 308 | 308 | – (by construction) |
| missing cell | 448 | 80 | A3; all 80 misses are cells of a 純額 block, whose checks are dynamic, not required |
| missing row | 228 | 20 | A2, A3, A10; all 20 misses are a 純額 row |
| label typo (last character) | 276 | 160 | A2, A3, A5, A10 for headers and totals; every miss is a line item, whose label no rule reads |
| line item one indent deeper | 160 | 160 | – |
| line item one indent shallower | 160 | 128 | A11 32 |

Source: `stress/out_acceptance_eval/results.md` and `results.json` (`by_type_on_pages_accepted_clean`).

**What this means.**

- **Coverage** on correct born-digital pages: 8 of 9. **Correct pages sent to review:** 1 of 9.
  **Incorrect pages accepted:** 0 of 1 clean parse, and 1128 of 2960 corrupted reads of the
  accepted pages.
- **The identity checks only catch arithmetic errors.** Anything that keeps the arithmetic valid is
  accepted: unit, column order, consistent value changes, line-item labels, indentation. On the VLM route,
  A8 and A9 compare these against a second reading. On the born-digital route nothing does: the text
  layer is trusted as the page.
- **Two fixable gaps found here, not fixed:** the dynamic checks of 純額 blocks don't block acceptance
  when not evaluable (missing gross or contra cell, missing 純額 row), and a single ones-digit error
  inside the window passes. They were left unfixed to avoid tuning the rule on its own evaluation.
  Any fix needs a pre-registered re-run.

The scan path isn't in this table, because no stored scan output is page-correct (§6): there are no
correct scanned pages to measure coverage on. The earlier "0 false accepts" on degraded pages comes
from 0 accepts and is not evidence of reliability.

## 8. Wrapped labels and spanning headers (synthetic layout fixtures)

`gt/synthetic_pages.py` draws 6 balance-sheet pages, one layout pattern each. The ground truth comes
from the same spec (values invented but consistent; it does not import the parser). These fixtures
show the rules work as specified; they are not evidence about real filings.

Sources: `stress/out_layout_fixtures/before_effd4e0.json`, `after.json` (`stress/run_layout_fixtures.py`).

| Fixture | Pattern | Before (commit effd4e0): page correct / decision | After: page correct / decision |
|---|---|---|---|
| wrap_last | label over 2 lines, values on the last line | no / review | **yes / accept** |
| wrap_first | values on the first line, hanging indent | no / review | **yes / accept** |
| wrap_last_hanging | values on the last line + hanging indent (looks like header + child) | no / review | **yes / accept** |
| span_header | a header cell spanning both value columns | no / review | **yes / accept** |
| span_header_date | spanning header that carries a date | no / review | yes / review (A11) |
| ambiguous_wrap | lines spaced like rows: wrap or a row whose values are missing | no / review | no / review (A11) |

**Before:** each wrapped label became a "header" row plus a row with a truncated label. On
`wrap_last`, for example, checks T01 and T02 failed and T04 and T05 were not evaluable, in both
periods. **After:** rows carry `row_span` (lines, line boxes, value
line) and pages carry `header_cells` with `colspan`.

**Dev check on the seen scans (the regression test for scans).** All 11 seen pages give the same cell
F1, the same dimensions and the same decisions as before the change
(`stress/out_layout_fixtures/seen_regression_70f9fac_vs_14deea7.json`, from `stress/compare_runs.py`). A first version of the rule
joined lines spaced at full row pitch. On `noise_s15` it glued a 純額 row whose values OCR had lost
onto the row above. That version was rejected and replaced by the tight-spacing rule before the
freeze. The VLM-route dev measurement (§3) reproduces exactly under the new code: 1/10 and
21/38160 (`stress/out_vlm_dev_seen/a9_strict_parser_14deea7/`). The held-out sets (unseen, v3)
were not re-run with this code.

## 9. Real documents through the PDF parser: not run

This was planned but **not done**, and no result is claimed. The plan: several legally accessible
filings from different companies, including complex headers, wrapped labels and scans, with
independently checked ground truth, a dev/test split fixed before tuning, and frozen code.

- **Blocker.** This session's network policy denied every source host: EDINET
  (`disclosure2.edinet-fsa.go.jp`, `api.edinet-fsa.go.jp`), TDnet and company IR sites all returned
  403 at the proxy. No filing could be downloaded, so there are no new documents, no new ground
  truth and no per-document results. Nothing was substituted.
- **What is ready.** `bench/eval_real.py` (new) runs the actual PDF parser on the balance-sheet pages
  that `bench/fetch_edinet.py --local` cuts from each filing. It scores them against the XBRL ground
  truth from `bench/build_gt_xbrl.py` with the per-dimension metric, and writes per-document results
  and failures. Sealed test filings run only with `--sealed`. The run refuses unless the pipeline
  fingerprint is logged as `REAL EVAL FROZEN <fingerprint>` in `bench/FREEZE_LOG.txt`, and it refuses
  a second sealed run into the same folder. Tested on the synthetic EDINET filing only
  (`tests/test_eval_real.py`). The filing list and the locked split are in
  [bench/DOWNLOADS.md](../bench/DOWNLOADS.md).
- **Known gap for that run.** Cross-page stitching isn't implemented. A two-page balance sheet is
  scored as concatenated pages and flagged `multi_page`; its structure dimensions are expected to
  fail.

## 10. Limitations

- **One reference page.** The born-digital 100% is a regression baseline, and "unseen" means unseen
  degradations of the same page, not unseen documents. One real filing confirms the benchmark
  extractor reads that layout. **The PDF parser has not been run on any real filing** (§9).
- **Acceptance relies on arithmetic.** On correct and corrupted born-digital pages (§7) it accepts
  1128 of 2960 corrupted reads of the pages it accepts clean: unit, column-order, consistent-value,
  line-item label and indentation errors pass. A page-level accept means "the arithmetic holds and the layout was certain", not
  "the page is correct".
- **Scan accuracy.**
  - △ is OCR'd as 「和」/「へ」, losing 6–10 signs per scan. The checks catch these (A2), but the
    values stay wrong.
  - Header misreads break exact-match rules and paths.
  - 120 dpi and below is unusable.
  - `is_corrected` is unknown on scans.
- **Evaluation coupling.**
  - Ground truth and pipeline share `label_norm` / `label_path`.
  - Path-keyed scoring cascades on ancestor misreads; this is shown as a separate diagnostic.
  - A second, AI-based ground-truth review was reported by the user and did not run in the project.
- **Acceptance untested on good scans** (see above).
- **"0 false accepts" was first measured with rule A1–A5**, which could not detect column swaps, unit errors, or wrong hierarchies with consistent values. A6–A10 fix this. Re-scored once under A1–A10, the held-out decisions are unchanged (§3). This is still reject-side only.
- **A9's ceiling is low.** On the seen scans, a perfect VLM read passes A9 on 1 page of 10, because Tesseract misreads or truncates a number on the other pages (§3).
- **A6 on the OCR route** needs the unit from the full-page read, which Tesseract often garbles. That is a reject-side cost: good OCR pages without a readable unit line go to review.
- **Not implemented.**
  - cross-page stitching (interface designed);
  - 90° pages, two-column layouts, multi-table pages, ruled-grid recovery; wrapped labels and
    spanning header cells are handled only for the patterns in §8 (others go to review via A11);
  - VLM results on held-out pages: the one real run stopped at the smoke gate ([VLM.md](VLM.md));
  - deskew beyond ±5°.
- **Assumptions.**
  - Rounding is truncation (consistent with all checks).
  - jppfs element candidates on the reference page are unverified and excluded from scoring.

## 11. Next steps

1. **Sign and header robustness**, validated on a *new* held-out set:
   - a glyph-level △ detector, or a VLM on numeric-column crops;
   - fuzzy label matching against a vocabulary before rule matching.
2. **Second extraction and agreement**: a VLM on numeric crops; a cell is flagged when it disagrees
   with OCR or the text layer.
3. **JP-BS-Bench at scale**: stratified real filings with ground truth from XBRL
   ([BENCHMARK.md](BENCHMARK.md)); IFRS template; cross-page `StatementAssembler`.
4. **Accept-side measurement** on fully correct scans; review queue for active learning and VLM
   fine-tuning on XBRL-aligned crops.
5. **Cost and latency** (2 CPU cores, no GPU): born-digital well under 1 s/page; scan path
   11–32 s/page across the committed runs (deskew + Tesseract at 300 dpi). Timings vary by machine.

## Reproducing

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# Japanese OCR for the scan path: sudo apt-get install -y tesseract-ocr tesseract-ocr-jpn
# or, without the jpn package (needs the tesseract binary + curl):
bash scripts/setup_tesseract.sh && export TESSDATA_PREFIX="$PWD/tessdata"

PYTHON=.venv/bin/python bash scripts/run_all.sh
```

`run_all.sh` runs, in order:

1. the tests;
2. the reference-page evaluation (`runs/eval_born_digital/`);
3. the geometry variants (`stress/out_geometry/`);
4. the seen, unseen and held-out v3 sets with the frozen v2 pipeline (`baselines/v2` →
   `stress/out_v2_*`, `stress/out_heldout_v3`), plus the OCR-agreement ceiling;
5. the round-1 baseline on seen and unseen (`stress/out_v1_*`);
6. the per-dimension metric on every stored prediction (`stress/dimensions_all_runs.json`) and the
   acceptance evaluation on correct and corrupted pages (`stress/out_acceptance_eval/`);
7. the VLM comparison, only if a `vlm_cache/` folder exists ([VLM.md](VLM.md));
8. the claim audit (`scripts/audit_claims.py` → `docs/AUDIT.md`).

The degraded PDFs are regenerated from the seeded specs. Expected pipeline fingerprints are
`888416e5df3fbc7c` (v2) and `9e905d908f7ad771` (round 1). The current `docparse/` is v2 plus
`vlm.py`, rules A6–A11 and the wrapped-label reconstruction, fingerprint `14deea7864c83094`. Every
metric reproduces exactly; only timings differ.

Individual commands:

```bash
.venv/bin/python -m docparse.pipeline <file.pdf> out/ --all                    # parse; batch_report.json
.venv/bin/python -m docparse.evaluate <file.pdf> gt/out/ground_truth out/eval  # score vs ground truth
.venv/bin/python stress/run_stress.py <pdf> gt/out/ground_truth <out> stress/specs/unseen.json
.venv/bin/python gt/synthetic_pages.py out/synthetic                           # layout fixtures + ground truth
.venv/bin/python bench/eval_real.py <raw_dir> <gt_dir> <out_dir> [--sealed]    # PDF parser on real filings
(cd gt && ../.venv/bin/python build_gt.py ../tests/fixtures/balance_sheet_after_correction.pdf \
    visual_transcription.json out)                                             # rebuild ground truth
```

**Optional real-filing fixture.** The seven tests in `tests/test_real_edinet.py` need Nintendo's XBRL
zip, which isn't redistributed here. To run them:

1. Download docID **S100Y9NX** from the public EDINET site (書類検索 → XBRL).
2. Save it as `tests/fixtures/edinet/S100Y9NX_xbrl.zip`.

Without it, those seven tests are skipped.
