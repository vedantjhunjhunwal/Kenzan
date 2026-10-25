"""Run an open VLM over the scanned PDFs of a stress set and cache its raw responses (GPU side).

Meant for Google Colab or a local GPU. It only produces the cache: scoring happens offline with
stress/run_vlm_compare.py, which never calls a model. Each response is stored with the image
sha256, model id and revision, settings, prompt hash, library versions, GPU and timing.

Order of a run:
  1. SMOKE TEST on the clean reference page rendered at 300 dpi (not a held-out page). Its read goes
     through the pipeline and is scored against the hand-verified ground truth. If fewer than
     SMOKE_MIN_CELLS of 74 cells are correct, the run stops and the held-out set is not touched.
  2. Every held-out PDF's page image is checked against the frozen hashes
     (stress/specs/heldout_v3.image_sha256.json); a mismatch stops the run.
  3. The held-out pages are read and cached.

usage:
  python scripts/vlm_run.py --smoke-only --cache vlm_cache            # step 1 only
  python scripts/vlm_run.py --pdfs stress/out_heldout_v3/pdfs --cache vlm_cache \
      [--model Qwen/Qwen3-VL-8B-Instruct] [--quant 4bit|none]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import io

import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from docparse import vlm  # noqa: E402
from docparse.evaluate import evaluate  # noqa: E402

SMOKE_MIN_CELLS = 70          # of 74; fixed before any model output was seen
REF = ROOT / "tests/fixtures/balance_sheet_after_correction.pdf"
GT = ROOT / "gt/out/ground_truth"


def smoke(be: vlm.VLMBackend, cache: Path) -> dict:
    """Clean reference page -> 300 dpi grey PNG -> one-image PDF -> VLM -> pipeline -> score."""
    page = pymupdf.open(REF)[0]
    pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
    buf = io.BytesIO()
    Image.frombytes("L", (pix.width, pix.height), pix.samples).save(buf, format="PNG")
    doc = pymupdf.open()
    p = doc.new_page(width=page.rect.width, height=page.rect.height)
    p.insert_image(p.rect, stream=buf.getvalue())
    pdf = cache / "smoke" / "reference_300dpi.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(pdf)
    rep = evaluate(str(pdf), str(GT), str(cache / "smoke" / "eval"), backend=be)
    # value-correct cells (positional diagnostic): path-keyed misses whose value is nevertheless right
    sys.path.insert(0, str(ROOT / "stress"))
    from run_stress import diagnose
    pred = json.loads((cache / "smoke" / "eval" / "prediction.json").read_text(encoding="utf-8"))
    diag = diagnose(json.loads(Path(f"{GT}.json").read_text(encoding="utf-8")), pred)
    value_correct = rep["cells"]["correct"] + sum("path mismatch" in d["cause"] for d in diag)
    res = {"page": "clean reference page rendered at 300 dpi (not a held-out page)",
           "image_sha256": vlm.pdf_image_sha256(pdf), "model": be.model,
           "settings_key": vlm.settings_key(be.model, be.settings), "status": rep["status"],
           "reason": rep["reason"], "cells_correct": rep["cells"]["correct"], "cells_gt": rep["cells"]["gt_cells"],
           "value_correct": value_correct, "f1": rep["cells"]["f1"], "decision": rep["acceptance"]["decision"],
           "acceptance_reasons": rep["acceptance"]["reasons"],
           "threshold": SMOKE_MIN_CELLS, "passed": rep["cells"]["correct"] >= SMOKE_MIN_CELLS,
           "run_info": getattr(be.runner, "info", {})}
    (cache / "smoke" / "smoke_result.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdfs")
    ap.add_argument("--cache", default="vlm_cache")
    ap.add_argument("--model", default=vlm.DEFAULT_MODEL)
    ap.add_argument("--quant", default=vlm.DEFAULT_SETTINGS["quant"], choices=["4bit", "none"])
    ap.add_argument("--hashes", default=str(ROOT / "stress/specs/heldout_v3.image_sha256.json"))
    ap.add_argument("--smoke-only", action="store_true")
    a = ap.parse_args(argv)
    if not a.smoke_only and not a.pdfs:
        ap.error("--pdfs is required unless --smoke-only")

    import transformers
    print("transformers", transformers.__version__)
    if not a.smoke_only:                      # check the frozen inputs before loading anything big
        expected = json.loads(Path(a.hashes).read_text(encoding="utf-8"))
        pdfs = sorted(Path(a.pdfs).glob("*.pdf"))
        bad = [p.name for p in pdfs if expected.get(p.stem) != vlm.pdf_image_sha256(p)]
        missing = sorted(set(expected) - {p.stem for p in pdfs})
        if bad or missing or not pdfs:
            print("refusing to run: page images differ from the frozen hashes:", bad, "missing:", missing)
            return 1

    runner = vlm.TransformersRunner(a.model, a.quant)
    be = vlm.VLMBackend(a.cache, a.model, {"quant": a.quant}, runner=runner)
    print("model:", json.dumps(runner.info), "| settings key:", vlm.settings_key(a.model, be.settings))

    sm = smoke(be, Path(a.cache))
    print(f"SMOKE TEST (clean reference page, 300 dpi): {sm['cells_correct']}/{sm['cells_gt']} cells correct "
          f"(gate, path-keyed), {sm['value_correct']}/{sm['cells_gt']} value-correct, "
          f"status {sm['status']}, decision {sm['decision']}"
          + (f", reason {sm['reason']}" if sm["reason"] else ""))
    if not sm["passed"]:
        print(f"STOP: fewer than {SMOKE_MIN_CELLS}/74 cells correct on the clean page. Held-out v3 NOT run. "
              f"Send {a.cache}/smoke/ for diagnosis.")
        return 3
    if a.smoke_only:
        print("smoke test passed; held-out v3 not run (--smoke-only)")
        return 0

    for p in pdfs:
        t0 = time.time()
        image, how = vlm.page_image(pymupdf.open(p)[0])
        raw, info = be.response_for(image)
        try:
            vlm.parse_response(raw)
            verdict = "valid JSON table"
        except vlm.VLMSchemaError as e:
            verdict = f"SCHEMA ERROR ({e}); kept as is, the page will fail"
        print(f"{p.stem:12} {how:15} {info['cache']:7} {time.time() - t0:6.1f}s  {verdict}", flush=True)
    print("done. Download the cache folder:", a.cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
