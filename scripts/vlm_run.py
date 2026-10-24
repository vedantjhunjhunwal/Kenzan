"""Run an open VLM over the scanned PDFs of a stress set and cache its raw responses (GPU side).

Meant for Google Colab or a local GPU. It only produces the cache: scoring happens offline with
stress/run_vlm_compare.py, which never calls a model. Each response is stored with the image
sha256, model id and revision, settings, prompt hash, library versions, GPU and timing.

Before running, every PDF's page image is checked against the frozen hashes (stress/specs/<set>.image_sha256.json);
a mismatch stops the run, so the model is only ever shown the frozen inputs.

usage:
  python scripts/vlm_run.py --pdfs stress/out_heldout_v3/pdfs --cache vlm_cache \
      [--model Qwen/Qwen3-VL-8B-Instruct] [--quant 4bit|none]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from docparse import vlm  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdfs", required=True)
    ap.add_argument("--cache", default="vlm_cache")
    ap.add_argument("--model", default=vlm.DEFAULT_MODEL)
    ap.add_argument("--quant", default=vlm.DEFAULT_SETTINGS["quant"], choices=["4bit", "none"])
    ap.add_argument("--hashes", default=str(ROOT / "stress/specs/heldout_v3.image_sha256.json"))
    a = ap.parse_args(argv)

    expected = json.loads(Path(a.hashes).read_text(encoding="utf-8"))
    pdfs = sorted(Path(a.pdfs).glob("*.pdf"))
    bad = [p.name for p in pdfs if expected.get(p.stem) != vlm.pdf_image_sha256(p)]
    missing = sorted(set(expected) - {p.stem for p in pdfs} - {"born_digital"})
    if bad or missing or not pdfs:
        print("refusing to run: PDFs differ from the frozen hashes:", bad, "missing:", missing)
        return 1

    runner = vlm.TransformersRunner(a.model, a.quant)
    be = vlm.VLMBackend(a.cache, a.model, {"quant": a.quant}, runner=runner)
    print("model:", json.dumps(runner.info), "| settings key:", vlm.settings_key(a.model, be.settings))
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
