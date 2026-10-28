# VLM path (scan route)

**Status: run once, on the smoke page only; it failed the gate, so no held-out page has a VLM
result. The rule has since been amended twice, A6–A8 and then A9–A10, on dev data only.** This workspace has no GPU and can't reach Hugging Face. The model runs on Colab and its
raw responses are cached; scoring is offline.

## Run 1: smoke test only (2026-10-25)

| | |
|---|---|
| Code | commit 1ce7f94, pushed to GitHub before any VLM output existed |
| Hardware | Colab Tesla T4; 4-bit weights, fp16 compute; transformers 5.20.2, torch 2.11.0+cu130 |
| Page | the clean reference page at 300 dpi (dev, not held-out) |
| Result | **20/74** cells path-keyed, below the 70/74 gate; **74/74** value-correct; status ok; **accepted** by A1–A5 |
| Time | 270.8 s for one page, 1648 new tokens |

Every value, label, column header and the unit line were read correctly. The indentation was wrong
on 31 of 43 rows, so 54 correct values sat on wrong label paths. Rule A1–A5 still accepted the page,
because its checks match totals by label and only reject solver links. That makes it a false accept
at the structure level. Full diagnosis: `stress/out_vlm_smoke/diagnosis.md`.

**Limits of this run's record:**

- `vlm_cache.zip` never reached this workspace, so its sha256 isn't recorded. The response was read
  through Colab's file viewer; the copy kept here has a browser-computed sha256, and replaying it
  offline reproduces 20/74 and the accept.
- The model revision was not recorded: `config._commit_hash` was null. The runner now takes it from
  huggingface_hub.

## Amendment A6–A8 (after run 1, before any held-out VLM run)

This was motivated by the smoke page, which is not held-out, and by fake-runner cases. It is logged
in `stress/FREEZE_LOG.txt`.

- **A6.** The unit must be read from the page.
- **A7.** Every value column must map to a parsed period end, in a consistent order.
- **A8 (VLM route).** A second reading of the same page by Tesseract must confirm four things:
  - the unit, read from an OCR pass on the header band;
  - the column dates;
  - the column assignment of the values: each VLM column must overlap most with the same OCR
    column;
  - the indentation. Levels are now taken from the page geometry: the OCR label x0 of each aligned
    row. The model's `indent` field is no longer used. If any row can't be aligned, the page goes to
    review.

Fake-runner results after the amendment (`tests/test_vlm.py`):

| Case | Decision |
|---|---|
| exact table; all indents 0; 減価償却累計額 one step less | accept, 74/74 cells, structure 100% |
| values in swapped columns | review (A8) |
| unit line 百万円 instead of 千円 | review (A8) |
| no heading lines | review (A6) |

## Amendment A9–A10 and the unit fix (dev data only)

You found a hole in A8: a perfect fake read with one last-digit error was accepted on 7 of 10 seen
scans. A8 compares column assignment by overlap and never compares individual values.

- **A9 (VLM route).** Wherever Tesseract reads a number in an aligned cell, its magnitude must equal
  the VLM value's magnitude. Cells Tesseract leaves blank don't block. Signs are left to the
  identity checks: with signed comparison, the perfect-read ceiling is 0/10, because Tesseract drops
  most △.
- **A10.** A subtotal or total with an unresolved calculation parent goes to review. This also
  catches the smoke failure when the geometry levels are unavailable.
- **Unit fix.** On rot_+1 and rot_+2 the header band was already cropped after deskew. The real
  cause was psm 6 garbling the right-aligned unit line when the column headers shared the band. The
  band is now cut above the period-header lines.

Fake-VLM measurement on the 10 seen scans, with every single error type from
`gt/detection_power.py` (3816 per page). Source: `stress/out_vlm_dev_seen/`.

| Rule | Perfect read accepted | Single injected errors accepted |
|---|--:|--:|
| A1–A8 (commit 2cbbfa7) | 7/10 | 1341/38160 |
| final rule without A9 | 9/10 | 1723/38160 |
| final rule (A1–A10) | **1/10** | **21/38160** |
| A1–A10 with A9-suffix (pre-registered dev candidate) | 8/10 | 84/38160 |

**The perfect-read ceiling falls to 1/10, not quite 0.** On 8 pages a correct read is rejected
because Tesseract truncates or misreads a number (3,377,407 → 407; 4,553 → 553; 884,339 → 894339).
On raster_100, Tesseract can't read the column dates at all. So A9 is safe on dev, but on this
evidence it accepts almost nothing.

**A9-suffix, tested with a pre-registered rule.** The variant counts a Tesseract number as
agreeing when its digit string is a suffix of the VLM's. The selection rule was pushed before
measuring (`stress/FREEZE_LOG.txt`): take the variant with more perfect reads accepted, provided
single injected errors accepted stay ≤ 0.1% (38/38160).

A9-suffix accepts 8/10 perfect reads but 84/38160 injected errors (0.22%), which breaks the bound.
**A9-strict is kept** (`stress/out_vlm_dev_seen/a9_suffix/`).

**Remaining alternative (untested):**

- **VLM-native second reading.** A second VLM pass on crops of each value column ("list the numbers
  top to bottom"), with per-cell agreement required. The input and the prompt differ, but the model
  is the same, so the errors are less independent than OCR's. It costs about 2 more model calls per
  page.

**The smoke response re-scored offline under A6–A8.** These are the same response bytes as above,
with no new model call: 74/74 path-keyed, structure 100%, accepted
(`stress/out_vlm_smoke/rescore_A6_A8/`). This is not the gate's official rerun. The gate is rerun
at the start of the next Colab run, and v3 runs only if it passes there.

## Code after the VLM freeze (2026-10-25)

The notebook stays pinned to commit 8450d48: the frozen method for VLM run 2. The code after it adds
the wrapped-label reconstruction and rule A11 ([RESULTS.md §8](RESULTS.md#8-wrapped-labels-and-spanning-headers-synthetic-layout-fixtures)).
That code is **not** the frozen VLM method. On the dev measurement it behaves the same: 1/10
perfect reads and 21/38160 injected errors accepted, page for page
(`stress/out_vlm_dev_seen/a9_strict_parser_14deea7/`).

## Design

`docparse/vlm.py`:

1. The model sees the scan's embedded page image, exactly as stored in the PDF. There's no
   re-render and no deskew.
2. It answers with one JSON table: heading lines, column headers, and rows of
   `{label, indent, values}`, with values verbatim as printed (△, －, commas).
3. Parsing is strict. Any of these fails the page, and so sends it to review: prose around the JSON,
   a missing or extra key, a wrong type, or a value the shared number parser rejects. A response is
   never repaired.
4. The table is laid out as synthetic positioned text. From there everything is the existing
   pipeline: layout, hierarchy, identity checks and acceptance rule A1–A10.
5. The model never sees the checks. There's no re-prompting or best-of-N on a failed check, so the
   read is verified, not optimised against the verifier.
6. Responses are cached under the sha256 of the image bytes plus the model, settings and prompt hash.
   A missing response fails the page as `VLMCacheMiss`; it's never skipped.

`tests/test_vlm.py` uses a fake runner (no model):

- a correct table gives F1 100% and calc/presentation parents 100%, and is accepted;
- one wrong digit fails a check and goes to review;
- malformed responses and cache misses fail the page.

This tests the plumbing only. It says nothing about any real model's accuracy.

## Model choice: Qwen3-VL-8B-Instruct (4-bit on a free Colab T4)

| | Why |
|---|---|
| Japanese | The Qwen2-VL card names Japanese among the languages it reads in images ([card](https://huggingface.co/Qwen/Qwen2-VL-7B-Instruct)). The Qwen3-VL card says its OCR "supports 32 languages (up from 19)" and is "robust in low light, blur, and tilt", with "improved long-document structure parsing" ([card](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)). Blur and tilt are exactly the held-out degradations. The part of the card I read doesn't list the 32 languages. |
| Size | About 8–9B parameters, about 18 GB in bf16. That fits an L4 or A100; with 4-bit weights for the language model and the vision encoder kept in fp16, it should fit a 16 GB T4. That memory figure is my estimate, not a measurement. |
| License | Apache-2.0, so it can be run locally and redistributed. |
| Not chosen | 32B/72B-class models don't fit Colab. Smaller models (2–4B) save memory and are untested here. Qwen2.5-VL-7B-Instruct runs with the same code (`--model`) as a second opinion. |

Settings were fixed before any output existed, and they are part of the cache key:

- long side capped at 2200 px;
- greedy decoding;
- `max_new_tokens` 6144;
- 4-bit.

They are logged in `stress/FREEZE_LOG.txt` ("VLM METHOD FROZEN"), together with the pipeline
fingerprint and prompt hash.

## Experiment: held-out v3

Held-out v3 is **8 new degradations of the same reference page, not new documents**.

1. **Inputs frozen first.** The new degradation set (`stress/specs/heldout_v3.json`) was hashed
   before any VLM code existed. Each page's identity is the sha256 of its embedded image
   (`stress/specs/heldout_v3.image_sha256.json`). PDF file hashes change on every save.
2. **Tesseract arm.** Run once with the frozen v2 pipeline (`baselines/v2`) →
   `stress/out_heldout_v3/results.json`.
3. **VLM arms** are scored by `stress/run_vlm_compare.py`:
   - VLM alone (no checks);
   - VLM + checks (A1–A10 since the amendments; before them, A1–A5);
   - VLM + checks + OCR agreement.
4. **Key question:** does any scan become fully correct *and* accepted, with 0 false accepts?

Any accept from "VLM + checks" has no OCR agreement behind it. It rests on the identity checks alone, and on this page they catch only 70.7% of single ones-digit errors (`gt/out/detection_power.txt`, from `gt/detection_power.py`). This arm was frozen after the Tesseract output on v3 had been compared with the v3 ground truth, but before any VLM output existed (`stress/FREEZE_LOG.txt`, "VLM METHOD FROZEN").

The OCR-agreement arm can't accept any v3 page, even with a perfect read (`stress/out_heldout_v3/ocr_agreement_ceiling.json`).

**Next: v3b.** Once the filings in `bench/DOWNLOADS.md` are triaged, the balance-sheet pages of the sealed test filings will be rasterized with the v3 degradation parameters and scored against their XBRL ground truth. The pages, spec and code fingerprint will be locked before any of them is run, and each will be run once. That would be the first scan evidence on documents other than the reference page.

## How to run it

1. **Generate responses.** Open `notebooks/vlm_heldout_v3.ipynb` in Colab ([open in Colab](https://colab.research.google.com/github/vedantjhunjhunwal/Kenzan/blob/main/notebooks/vlm_heldout_v3.ipynb)) with a T4 (fp16 compute).
   It prints the transformers version and regenerates the 8 pages, checking their image hashes.
   Then it runs a **smoke test** on the clean reference page rendered at 300 dpi; this is not a v3
   page. If fewer than 70 of 74 cells are read correctly, it stops and v3 is not run. Otherwise it
   reads the v3 pages and downloads `vlm_cache.zip`. If regeneration reports MISMATCH, upload `heldout_v3_pdfs.zip` (the frozen
   PDFs).
2. **Score offline.** Unzip into the repo root and run:

   ```bash
   python stress/run_vlm_compare.py --cache vlm_cache
   ```

   Output: `stress/out_heldout_v3_vlm/<model>_<settings>/compare.{json,md}`.
