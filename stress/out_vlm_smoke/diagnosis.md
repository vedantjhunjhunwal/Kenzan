# VLM smoke test: why 20/74 cells and still "accept"

**Page.** The clean reference page, rendered at 300 dpi. It is a dev page, not a held-out page.

**Run.** Colab run 1:

- commit 1ce7f94;
- Qwen/Qwen3-VL-8B-Instruct, 4-bit, Tesla T4;
- 270.8 s, 1648 new tokens; the 6144 limit was not hit.

**Source of the response.** `vlm_cache.zip` never reached this workspace, so its sha256 is not
recorded. The cached response file was read through Colab's file viewer and saved as
`smoke_response.colab_viewer.json`. That file is 4897 bytes, with sha256 `1c909eef…7e06`. The hash
was computed in the browser on the viewer text. It was not cross-checked with `sha256sum` on the
runtime; see `stress/FREEZE_LOG.txt`.

**Replay.** Replaying that response offline through the same pipeline reproduces the Colab result
exactly: 20/74 cells, status ok, accept. The local rendering of the smoke page also has the same
image sha256 as the Colab one (`bffc9ac6…`). Outputs: `eval/report.json`, `eval/prediction.json`,
`cell_diagnosis.json`.

## Answer

**For values, it is a scoring artifact. For structure, it is a real false accept.**

- **Everything that holds a value was read correctly.** Every printed value matches the ground
  truth: 74/74 by row order (positional diagnostic). So do every label, both column headers and the
  unit.
- **The indentation is wrong on 31 of 43 rows.** Cells are scored on label paths, so the wrong
  hierarchy makes 54 correct values count as wrong. All 54 have the cause "hierarchy differs → path
  mismatch".
- **Structure.** Presentation level is right on 27.9% of rows, presentation parent on 65.1%, and
  calculation parent on 93.0%. Three subtotals have no calculation parent: 流動資産合計,
  有形固定資産合計 and 無形固定資産合計. They are typed `total` instead of `subtotal`, and their link
  is left `unresolved`.
- **Why the page was accepted anyway.** The acceptance rule does not see any of this. Template
  checks match totals by label, not by tree position: 12/12 template and 8/8 dynamic checks pass
  because the values are right. Rule A5 rejects only solver links; it does not reject unresolved
  links.
- **The verdict under our own definition.** "Fully correct" requires cell F1, calculation parent
  and presentation parent all at 100%. So this page is a **false accept at the structure level**,
  with correct values.

## Details

| Item | VLM read | Ground truth | Match |
|---|---|---|---|
| Value cells, row-aligned | 74 values | 74 | 74/74 |
| Labels | 43 rows | 43 | 43/43 |
| Column → period | `前連結会計年度(2025年3月31日)` → prior 2025-03-31; `当連結会計年度(2026年3月31日)` → current 2026-03-31 | same | yes |
| Unit line | 「(単位：千円)」 → 千円, ×1000 | 千円 | yes |
| Title / revision | 連結貸借対照表 / 訂正後 | same | yes |
| Indentation | levels 0–2 | levels 0–4 | 12/43 rows |

Rows whose indent differs (ground-truth level → VLM level):

- **Totals one step too far left:**
  - 流動資産合計 2→1
  - 有形固定資産合計 3→1
  - 無形固定資産合計 3→1
  - 投資その他の資産合計 3→1
  - 固定資産合計 2→0
  - 資産合計 1→0
- **Second-level headers one step too far left:**
  - 固定資産 1→0
  - 有形固定資産 2→1
  - 無形固定資産 2→1
  - 投資その他の資産 2→0
- **Depth 3 and 4 collapsed to 2:**
  - 建物及び構築物 and the other items under 有形固定資産: 3→2;
  - every 減価償却累計額 and every （純額） row: 4→2.
- **Items under 無形固定資産:** 3→2.
- **Items under 投資その他の資産:** 3→1.

**Wrong cells.** All 54 have one cause: path mismatch from hierarchy (`cell_diagnosis.json`). There
are no digit, sign, column or unit errors.

## Implications

1. **The smoke gate failed for indentation only.** The 70/74 gate stays as pre-registered and
   path-keyed. Value-correct cells are now reported beside it: 74/74 here.
2. **The acceptance rule cannot see hierarchy errors when values are right.** This adds to the holes
   found with the fake runner: swapped value columns and wrong unit lines. The fixes are logged as
   amendment A6–A8 in `stress/FREEZE_LOG.txt`.
