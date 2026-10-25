# VLM path (scan route)

**Status: code and protocol are done; the model has not been run.** This workspace has no GPU and
can't reach Hugging Face, so the model runs on Colab (or any GPU) and its raw responses are cached.
Scoring is offline. No VLM number appears anywhere in this repository until that cache exists.

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
   pipeline: layout, hierarchy, identity checks and acceptance rule A1–A5.
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
   - VLM + checks (A1–A5);
   - VLM + checks + OCR agreement.
4. **Key question:** does any scan become fully correct *and* accepted, with 0 false accepts?

Any accept from "VLM + checks" has no OCR agreement behind it. It rests on the identity checks alone, and on this page they catch only 70.7% of single ones-digit errors (`gt/out/detection_power.txt`, from `gt/detection_power.py`). This arm was frozen after the Tesseract output on v3 had been compared with the v3 ground truth, but before any VLM output existed (`stress/FREEZE_LOG.txt`, "VLM METHOD FROZEN").

The OCR-agreement arm can't accept any v3 page, even with a perfect read (`stress/out_heldout_v3/ocr_agreement_ceiling.json`).

**Next: v3b.** Once the filings in `bench/DOWNLOADS.md` are triaged, the balance-sheet pages of the sealed test filings will be rasterized with the v3 degradation parameters and scored against their XBRL ground truth. The pages, spec and code fingerprint will be locked before any of them is run, and each will be run once. That would be the first scan evidence on documents other than the reference page.

## How to run it

1. **Generate responses.** Open `notebooks/vlm_heldout_v3.ipynb` in Colab with a T4 (fp16 compute).
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
