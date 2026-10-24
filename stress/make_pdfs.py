"""Regenerate the degraded PDFs of a stress spec and check them against the recorded hashes.

The same generator and random-number order as stress/run_stress.py. For heldout_v3 the expected
sha256 of every page IMAGE (the stream embedded in each PDF, which is what OCR and the VLM see) is in
stress/specs/heldout_v3.image_sha256.json. PDF file hashes are not used: pymupdf writes a random
trailer /ID. If any image hash differs (other pymupdf / Pillow / numpy / libjpeg versions), the script
says so and exits non-zero: the VLM must then be run on the original PDFs, not regenerated ones.

usage: python stress/make_pdfs.py stress/specs/heldout_v3.json stress/out_heldout_v3/pdfs
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docparse.vlm import pdf_image_sha256  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_stress import make_variant  # noqa: E402

SRC = HERE.parent / "tests/fixtures/balance_sheet_after_correction.pdf"


def make(spec_path: str, out_dir: str) -> int:
    spec_p = Path(spec_path)
    spec = json.loads(spec_p.read_text(encoding="utf-8"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    expected_p = spec_p.with_suffix(".image_sha256.json")
    expected = json.loads(expected_p.read_text(encoding="utf-8")) if expected_p.exists() else {}
    rng = np.random.default_rng(spec["seed"])
    bad = 0
    for name, dpi, rot, sigma, q in spec["variants"]:
        if dpi is None:
            continue
        p = out / f"{name}.pdf"
        make_variant(SRC, p, dpi, rot, sigma, q, rng)
        h = pdf_image_sha256(p)
        ok = expected.get(name) in (None, h)
        bad += not ok
        print(f"{name:12} {h[:16]}  {'ok' if ok else 'MISMATCH (expected ' + expected[name][:16] + ')'}")
    if bad:
        print(f"{bad} PDF(s) differ from {expected_p.name}; use the original PDFs instead.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(make(*sys.argv[1:3]))
