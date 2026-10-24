# JP-BS-Bench verification notes

Each entry records what was checked, by whom, against what source, and the result. "External" means
it was done outside this project's session and reported by the user. "In-session" means it was
recomputed here.

## 2026-10-25: Nintendo 有価証券報告書, FY ending 2026-03-31 (docID S100Y9NX, EDINET E02367)

**Input:** the XBRL zip from the public EDINET site (`XbrlSearchDlInfo.csv` +
`S100Y9NX/XBRL/PublicDoc/`), kept as `tests/fixtures/edinet/S100Y9NX_xbrl.zip`.

**Code:** v3 kit, `fetch_edinet.py` sha256 `45a00b00884c4bbb`, `build_gt_xbrl.py` sha256
`0a3b53a0c56d64de`. No code was changed for this verification.

**Split:** dev (forced: inspected during development; listed in `forced_dev.txt`). By hash alone this
company would have been test.

| # | Check | Source compared against | External (user) | In-session |
|---|---|---|---|---|
| 1 | `--local` on the zip reproduces `S100Y9NX_bs_rows.csv` | the delivered file | identical, 56 rows | — |
| 2 | All extracted values equal an independent source | plain XBRL instance (`.xbrl`) in the same zip; the extractor reads the inline HTML | 88/88, 0 mismatches | 88/88 found in the instance, 0 mismatches |
| 3 | Subtotals vs the sum of their parts, both years | arithmetic on the printed values | every subtotal/total 0–3 above the sum, consistent with 百万円未満切捨て | 26 sum checks, diff ∈ [0, 3] (0: 9, 1: 10, 2: 3, 3: 4); 0 fail under the sign-aware truncation window |
| 4 | Cross-section identity | 資産合計 vs 負債純資産合計 | equal in both years | equal in both years (diff 0) |
| 5 | Note marker inside a value cell | printed cell 「※1 65,180」 | read as 65,180 with ※1 as a footnote (not 165,180) | same (also a regression test) |

**Not covered by this filing:**

- **Contra items stored positive in XBRL** (e.g. 減価償却累計額 with the subtraction in the
  calculation weight): this filing has no 減価償却累計額 rows, and all 4 △ values are stored
  negative (`sign="-"`). The `printed_negative_xbrl_positive` convention is still untested on real
  data.
- **The PDF side**: page finding, the end-total check and row alignment against the printed pages
  need the annual-report PDF, which hasn't been supplied yet.
- **IFRS, 千円, two-page balance sheets**: to come with the next filings.

**Caveat on independence:** check 2 compares two renderings of the same submission (inline HTML and
the generated instance). It validates the extractor's reading of the HTML, not the filer's
numbers. Checks 3–4 are arithmetic on the printed values.
