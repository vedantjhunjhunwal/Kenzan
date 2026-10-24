# Results

Every number here is copied from a result file in this repository. The file is named under each
table, and [Reproducing](#reproducing) regenerates them all. The definitions are in [METHOD.md](METHOD.md).

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

**Caveat:** no degraded page was fully correct, so it isn't shown yet that the rule would *accept* a
good scan.

## 4. Real-document results (JP-BS-Bench extractor)

**The filing.** Nintendo's 有価証券報告書 for the fiscal year ending 2026-03-31 (docID S100Y9NX,
J-GAAP, 百万円). It's a dev document, forced into dev because it was inspected while debugging.

**Outputs** (`bench/results/S100Y9NX/`):

- extractor output: `bs_rows.csv`, `ixbrl_bs.json`;
- ground truth built from the XBRL: `ground_truth_xbrl.*`;
- check report: `pilot_check.md`.

Notes: `bench/VERIFICATION.md`.

| Check | Result |
|---|---|
| Balance sheet found | by its 【連結貸借対照表】 heading; 56 rows (44 with values), 88 values, both years, scale 百万円 |
| Values vs the plain XBRL instance (`.xbrl`) | 88 / 88 equal, 0 mismatches (extractor reads the inline HTML) |
| Subtotals vs sum of parts | all 26 sum checks 0–3 above the sum (consistent with 百万円未満切捨て); 0 fail; template coverage 26 / 26 |
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

## 5. Limitations

- **One reference page.** The born-digital 100% is a regression baseline, and "unseen" means unseen
  degradations of the same page, not unseen documents. One real filing confirms the benchmark
  extractor reads that layout.
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
- **Not implemented.**
  - cross-page stitching (interface designed);
  - wrapped labels, 90° pages, two-column layouts;
  - the VLM backend (stub);
  - deskew beyond ±5°.
- **Assumptions.**
  - Rounding is truncation (consistent with all checks).
  - jppfs element candidates on the reference page are unverified and excluded from scoring.

## 6. Next steps

1. **Sign and header robustness**, validated on a *new* held-out set:
   - a glyph-level △ detector, or a VLM on numeric-column crops;
   - fuzzy label matching against a vocabulary before rule matching.
2. **Second extraction and agreement**: a VLM on numeric crops; a cell is flagged when it disagrees
   with OCR or the text layer.
3. **JP-BS-Bench at scale**: stratified real filings with ground truth from XBRL
   ([BENCHMARK.md](BENCHMARK.md)); IFRS template; cross-page `StatementAssembler`.
4. **Accept-side measurement** on fully correct scans; review queue for active learning and VLM
   fine-tuning on XBRL-aligned crops.
5. **Cost and latency** (development machine): born-digital well under 1 s/page; scan path
   11–21 s/page (deskew + Tesseract at 300 dpi).

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
4. the seen and unseen stress sets with the current pipeline (`stress/out_v2_*`);
5. the round-1 baseline on both sets (`stress/out_v1_*`).

The degraded PDFs are regenerated from the seeded specs. Expected pipeline fingerprints are
`888416e5df3fbc7c` (current) and `9e905d908f7ad771` (round 1). Every metric reproduces exactly;
only timings differ.

Individual commands:

```bash
.venv/bin/python -m docparse.pipeline <file.pdf> out/ --all                    # parse; batch_report.json
.venv/bin/python -m docparse.evaluate <file.pdf> gt/out/ground_truth out/eval  # score vs ground truth
.venv/bin/python stress/run_stress.py <pdf> gt/out/ground_truth <out> stress/specs/unseen.json
(cd gt && ../.venv/bin/python build_gt.py ../tests/fixtures/balance_sheet_after_correction.pdf \
    visual_transcription.json out)                                             # rebuild ground truth
```

**Optional real-filing fixture.** The six tests in `tests/test_real_edinet.py` need Nintendo's XBRL
zip, which isn't redistributed here. To run them:

1. Download docID **S100Y9NX** from the public EDINET site (書類検索 → XBRL).
2. Save it as `tests/fixtures/edinet/S100Y9NX_xbrl.zip`.

Without it, those six tests are skipped.
