# Method

Kenzan (検算, "checking a calculation") parses pages of Japanese financial statements into tables
and uses accounting identities to decide which pages can skip human review. This document covers the
problem, the definition of "correct", the ground truth, the architecture and the design decisions.
Results are in [RESULTS.md](RESULTS.md); the real-filing benchmark is in [BENCHMARK.md](BENCHMARK.md).

## 1. Problem

A balance-sheet page is a tree with numbers on it, not a grid. A useful parser has to recover:

- **values exactly as printed**: 「△1,923,585」 is −1,923,585; 「△0」 is negative zero; 「－」 is *nil*
  (not 0); 「（単位：千円）」 scales by 1,000, and figures are truncated to that unit;
- **two hierarchies**:
  - *presentation*: what the indentation shows;
  - *calculation*: what each row sums into. These differ. 減価償却累計額 is indented deeper than the
    gross line it is subtracted from, 純額 rows roll up instead of their gross lines, and totals sit at
    their children's indent;
- **corrections**: in 訂正 (amended) filings, changed figures are underlined, per cell;
- **a trust decision** for pages without ground truth.

## 2. Definition of "correct"

| Level | Metric | Notes |
|---|---|---|
| 1. Cells | Precision / recall / F1 on `(label_path, period_end)` → `(reported_value, is_nil, is_negative)` | Exact match at page precision (thousands as printed), so unit-conversion bugs can't hide. Keyed on `label_path` because `label_ja` repeats (その他 ×4, 減価償却累計額 ×4). |
| 2. Structure | Accuracy of presentation level / presentation parent / calc parent / row type | Rows aligned by label sequence, independent of paths, so structure errors are measured apart from value errors. |
| 2b. Corrections | `is_corrected` accuracy plus positive-class precision/recall | 2 of 74 cells are positive on the reference page. `None` (unknown, e.g. scans) counts as wrong. |
| 3. Label-free | **Consistency** = passed / evaluable identity checks; **coverage** = evaluable / expected template checks | Registry of 13 J-GAAP balance-sheet identities × periods. A missing row is `not_evaluable`, never a pass. Checks whose structure came from the solver fallback are reported separately. |

### Rounding-aware tolerance

Each printed figure is truncated independently (千円未満切捨て), so printed children need not add up
to the printed total; exact matching flags 13 of 20 correct checks on the reference page. With *p*
non-negative and *q* △ children, the tight window for `diff = printed total − Σ printed children` is:

- **[−q, p−1]** when the total is printed without △;
- **[−(q−1), p]** when the total is printed with △.

A printed `0` is a non-negative child, `△0` a negative one, and nil cells count in neither. Both ends
of the window are attained on the reference page, so it can't be tightened. The window holds *n*
integers instead of the 2n−1 of a symmetric ±(n−1) window. Under round-half-up the window would be
about ±(n+1)/2; the rounding convention is a per-document parameter.

**Detection power** (3,816 single injected errors on the reference page, `gt/detection_power.py`):

| Injected error | ±(n−1) | Sign-aware |
|---|--:|--:|
| ones-digit misread | 52.3% | **70.7%** |
| tens-digit misread | 97.2% | **100%** |
| hundreds+ misread, dropped row, swapped columns, spurious △ | 100% | 100% |
| dropped △ | 90% | 90% (the miss is 「△0」 → 「0」) |
| false alarms on the clean page | 0 / 20 | 0 / 20 |

## 3. Ground truth

**Reference page** (`gt/`). The 連結貸借対照表 page of an amended 決算短信 (`tests/fixtures/`):

- **Source.** 43 rows and 74 cells, transcribed from 300-dpi image crops, not from the text layer
  (`gt/visual_transcription.json`). It was then diffed against the text layer; the only difference
  is the codepoint of the two nil dashes, which pixels can't decide.
- **Annotation and checks.** Calc parents were annotated by hand. The table was verified row by row
  by a human.
- **Provenance.** `gt/build_gt.py` records it in `gt/out/ground_truth.json`. Bounding boxes and dash
  codepoints are the only fields taken from the text layer.

**Benchmark filings** (`bench/`). Built automatically from each filing's inline-XBRL table: the filed
HTML that EDINET's PDF is rendered from.

- **Rows and values.** Rows, labels and order come from that table, values from the printed cell
  text. Each value is cross-checked against the tagged fact: |fact| must equal printed × 10^scale.
- **Hierarchy.** Calc parents come from the company's XBRL calculation linkbase. Presentation levels
  come from the label's grid position.
- **Alignment.** A filing is kept when ≥ 95% of its rows align with the PDF pages, by label and by
  value. The matcher is independent of the parser. Pages whose text layer fails the quality gate go
  to an "unverifiable" bucket instead of being dropped.

See [BENCHMARK.md](BENCHMARK.md).

**Not circular, with one known coupling.** The reference ground truth was read from pixels and the
benchmark ground truth from XBRL, not from the parser's input. The ground-truth builders and the
pipeline do share the label normalization (`label_norm` / `label_path`). A normalization bug would
therefore hit both sides the same way and stay invisible to the evaluator.

## 4. Architecture

```
PDF page
 └─ router ── text-quality gate: chars decode, numbers parse, page not mostly image
     ├─ born_digital → TextLayerBackend: spans + glyph boxes + horizontal vector segments
     └─ visual       → render 300 dpi → deskew (projection profile) → Tesseract jpn (tessdata_best)
                                                                     [VLMBackend: stub]
 └─ merge       split on whitespace; join touching runs and number fragments (△ joins its digits)
 └─ layout      lines grouped with a robust line height; value columns from right edges;
                period headers (Western/和暦); unit, title, 訂正 marker; body rows
 └─ normalize   NFKC labels + footnote refs; △/▲ → negative; dash glyphs → nil; unit → yen + decimals
 └─ hierarchy   indent → presentation tree; rules → calc tree; solver fallback (contiguous runs, every period)
 └─ corrections glyph-width underline → is_corrected; column-width rules excluded; None without vectors
 └─ validate    statement type from title, else from content; registry checks → consistency, coverage
 └─ acceptance  accept / review with reasons (A1–A10)
 └─ export      JSON + wide CSV + long CSV (same schema as the ground truth); status ok / no_table / failed
```

Nothing page-specific is hard-coded. Indent levels, column edges, row pitch, rule thickness, line
height and skew are learned per page, with tolerances relative to font size.

## 5. Acceptance rule

`docparse/acceptance.py`. A page is **accepted** only if all of these hold; otherwise it goes to
**review**, with every reason listed:

| | Condition |
|---|---|
| A1 | page status is `ok` (not `failed` / `no_table`) |
| A2 | zero failed identity checks (template, dynamic and solver alike) |
| A3 | zero not-evaluable template checks among those whose sections are all on the page |
| A4 | coverage is not n/a (statement type recognised, template exists) |
| A5 | no calc-parent links from the solver fallback (`allow_solver=False` by default): those links are chosen by the same arithmetic the checks test |
| A6 | the unit was read from the page (a unit line such as （単位：千円）) |
| A7 | every value column maps to a period-end date parsed from its header; the dates are distinct, in one direction, and 前… is earlier than 当… |
| A8 | VLM route only: a second reading of the same page (Tesseract; header band for the unit) confirms the unit, the column dates, the column assignment of the values, and the indentation (taken from page geometry for every row) |
| A9 | VLM route only: per-cell agreement. Wherever Tesseract reads a number in an aligned cell, its magnitude equals the VLM value's magnitude; cells Tesseract leaves blank don't block (signs are left to the identity checks, because Tesseract often loses △) |
| A10 | no subtotal or total row with an unresolved calc parent |

**Amendments A6–A8 and A9–A10 (2026-10-25).** Identity checks are invariant to the unit and to swapping whole columns, and template checks match totals by label, not by tree position. So A1–A5 accepted pages with a wrong unit, swapped columns, or a wrong hierarchy whenever the values were consistent. This was found with a fake VLM runner and with the VLM smoke page (the clean reference page, not held-out); see `stress/out_vlm_smoke/diagnosis.md` and `stress/FREEZE_LOG.txt`. Earlier "0 false accepts" results were obtained with A1–A5.

Cross-section checks (e.g. 資産合計 = 負債純資産合計) are required only when every section they need is
on the page. `is_corrected = None` (scans) doesn't block acceptance but is reported.

## 6. Design decisions

- **Born-digital first.** The text layer is used when it passes the quality gate. "Has a text layer"
  alone is not enough: garbage character maps and OCR overlays on scans exist.
- **Values.** Each value keeps several fields:
  - `value` = reported × unit;
  - `decimals` = −3 (the XBRL convention for "accurate to thousands");
  - `reported_value` and `raw_text` as printed.

  Edge cases: 「△0」 keeps its sign. A dash before digits is read as negative with a `dash_minus`
  warning. Bad digit grouping keeps the value with a warning. Warning counts go in the metadata.
- **Merge before column assignment.** A △ drawn as its own glyph run would otherwise fall outside
  the value column and the sign would be lost.
- **Calc tree from rules.** Three rules, in order:
  1. 「X合計」 closes the open header whose stem is X;
  2. 「X（純額）」 claims gross row X plus the contras after it;
  3. registry totals (負債純資産合計 = 負債合計 + 純資産合計).

  The solver fallback only considers contiguous runs, the same run must fit every period (a period
  that can't be evaluated rejects it), and its checks are reported separately. Memo rows (「うち…」)
  are never summed. Rows still open at page end are `unresolved`, not guessed.
- **Corrections.** An underline counts when it is as wide as the glyphs; column-width rules are
  excluded, with thickness as the tie-breaker. No vector data gives `None`, not `False`.
- **Coverage.** The denominator is a fixed registry, so coverage means the same thing on every filing.
  Optional sections (繰延資産, その他の包括利益累計額) are dynamic checks, outside the denominator.
- **Scan path.** Three steps before the rest of the pipeline:
  - deskew maximizes the sharpness of the horizontal ink profile (±5°, 0.02° steps) before OCR;
  - line grouping uses half the page's robust line height, so one degenerate OCR box can't split a
    line;
  - when the title is unreadable, two or more distinct section markers identify a balance sheet.
- **Failures.** One bad page never stops a batch (`status: "failed"` with the reason). OCR with zero
  words is an error, not an empty table.
- **Evaluation discipline.** Results are only evidence on data the code hasn't been fitted to:
  - code is fingerprinted before each evaluation;
  - held-out specs are hashed before the code they test is written;
  - the benchmark split is fixed by a salted hash of the company code, with forced-dev for anything
    inspected (`bench/split_spec.json`, `bench/FREEZE_LOG.txt`).
