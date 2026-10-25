# Why Tesseract scored worse at 250 dpi than at 175 dpi (held-out v3)

This is a diagnosis from the existing outputs only. No code was changed and v3 was not re-run.
Sources:

- `stress/out_heldout_v3/results.json`
- `runs/<variant>/cell_diagnosis.json`
- `runs/<variant>/prediction.json`

**Short answer.** There are two different causes:

- **scan 250 vs scan 175 (39.4% vs 78.5%) is a scoring cascade.** Two header labels were misread by
  one character, and that moved 25 cells whose values were read correctly. Counting values only,
  the two pages are level: 52 vs 53 cells.
- **raster 250 vs raster 175 (75.5% vs 81.1%) is 4 cells of OCR digit noise.** Two of the misreads
  appear on both 250 dpi pages and on no other v3 page, so they are tied to 250 dpi. The mechanism
  is a hypothesis, not shown.

## 1. scan_250 vs scan_175: path cascade from two header misreads

In scan_250, OCR read 固 as 同 in two header rows that hold no values:

| Row | Ground truth | scan_250 read | Effect |
|---|---|---|---|
| r15 | 有形固定資産 | 有形 同定 資産 | the path of every row under it changes (r16–r30) |
| r31 | 無形固定資産 | 無形同定 資産 | the path of r32–r34 changes |

Cells are scored on (label path, period). So a cell with the right value under a misread header
counts as wrong. `cell_diagnosis.json` labels these "hierarchy differs → path mismatch".

| | Scored correct | Path mismatch, value right | Value-correct |
|---|--:|--:|--:|
| scan_250 | 27 | 25 | **52** |
| scan_175 | 53 | 0 | **53** |

The 25 cascaded cells, listed in `runs/scan_250/cell_diagnosis.json`:

- both periods: r18, r19, r21, r22, r23, r25, r27, r29, r30, r32, r33 (22 cells);
- one period: r16 current, r26 prior, r34 prior (3 cells).

Every one of them has `pred_raw` equal to `gt_raw`, except r26 prior: there the dash 「－」 was read
as 「ー」, which parses to the same nil value. scan_175 read both headers correctly.

## 2. raster_250 vs raster_175: four cells of digit noise

| | Scored correct | Path mismatch, value right | Value-correct |
|---|--:|--:|--:|
| raster_250 | 54 | 2 | 56 |
| raster_175 | 58 | 0 | 58 |

The digit misreads on each v3 page (`cause = digits misread`):

| Page | Misreads |
|---|---|
| raster_250 | r05 current 884,339 → **89884,339**; r07 prior 589,488 → **5989,488**; r27 prior 848,814 → 9848,814; r30 current 1,352,610 → 610 |
| scan_250 | r05 current 884,339 → **8984,339**; r07 prior 589,488 → **5989,488**; r10 prior 7,530 → 530; r17 prior △1,923,585 → 923,585 |
| raster_175 | r30 current 1,352,610 → 610; r43 prior 7,534,110 → 534,110 |
| scan_175 | r43 prior 7,534,110 → 110 |
| 300 dpi pages (rot, noise, jpeg) | none at r05 or r07 except noise_s12 r05 → 894,339 |

- Both 250 dpi pages insert an extra digit into r05 current and r07 prior. No 175 dpi or 300 dpi
  page makes that error on r07, and none makes the same r05 error. So this is a property of the
  250 dpi scans.
- The other raster_250 losses are also found at 175 dpi: lost △ signs (9 on both raster pages),
  missed column assignments (5 on both), and the r30 truncation (on both).
- Two raster_250 cells (r34) are a path mismatch from the same 固 → 同 misread in 無形固定資産合計.

**Hypothesis for the 250 dpi insertions (not tested).** The OCR backend re-renders each image page
at 300 dpi before OCR (`TesseractBackend`, dpi = 300). A 250 dpi image is therefore upsampled by a
non-integer 1.2×, and a 175 dpi image by 1.71×. Interpolation artifacts at 1.2× could make one
glyph read as two. This would need a controlled test on dev or seen pages to confirm, not on v3.

## 3. What it is not

| Candidate cause | Evidence | Verdict |
|---|---|---|
| Line-height / row grouping | char size 7.92 on all four pages; row pitch 12.18–12.36; 43 body rows on all four | no |
| Column detection | right edges 397.0 / 522.0 (scan_175: 397.2 / 522.2) on all four | no |
| Indent levels | indent x0 clusters 63 / 76 / 89 / 102 / 115 on all four | no |
| Deskew | scan_250 true +0.6°, measured −0.61° (levelling angle); scan_175 true −1.1°, measured +1.11°; rasters 0.0 | no |
| Period header misread | raster_175, scan_250 and scan_175 all read the current column as `col1`; scored by date, so no effect on F1 | no |

The values in this table come from `prediction.json` → `metadata.layout` and
`route.backend_diagnostics` for each variant.

## Implication

Path-keyed cell F1 can swing by up to 39 points on one misread header character. That is why
RESULTS.md already reports value-correct counts beside F1, and why this page's scan-path F1
numbers are noisy at the single-page level. Fixing it, for example with fuzzy header matching
against a label vocabulary, is pipeline work for a new held-out set, not for v3.
