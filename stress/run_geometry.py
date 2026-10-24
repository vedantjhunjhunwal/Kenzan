"""Geometry variants of the born-digital page (vector content kept): 80% scale, shifted 40pt right,
A4 content centred on an A3 page. Same placement as tests/test_variants_and_failures.py.

usage: python stress/run_geometry.py <original.pdf> <gt_stem> <out_dir>
"""
import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from docparse.evaluate import evaluate  # noqa: E402


def place(src_pdf, kind, out_pdf):
    src = pymupdf.open(src_pdf)
    w, h = src[0].rect.width, src[0].rect.height
    out = pymupdf.open()
    if kind == "scale80":
        page, rect = out.new_page(width=w, height=h), pymupdf.Rect(0, 0, 0.8 * w, 0.8 * h)
    elif kind == "shift40":
        page, rect = out.new_page(width=w, height=h), pymupdf.Rect(40, 0, w + 40, h)
    else:
        W, H = 841.89, 1190.55
        page = out.new_page(width=W, height=H)
        rect = pymupdf.Rect((W - w) / 2, (H - h) / 2, (W + w) / 2, (H + h) / 2)
    page.show_pdf_page(rect, src, 0)
    out.save(out_pdf)


def main(src, gt_stem, out_dir):
    out = Path(out_dir)
    (out / "pdfs").mkdir(parents=True, exist_ok=True)
    rows = []
    for kind in ("original", "scale80", "shift40", "larger_page"):
        pdf = Path(src) if kind == "original" else out / "pdfs" / f"{kind}.pdf"
        if kind != "original":
            place(src, kind, pdf)
        r = evaluate(str(pdf), gt_stem, str(out / "runs" / kind))
        c, s, k = r["cells"], r["structure"], r["corrections"]
        v = r["validation"]["pred_summary"]
        rows.append({"variant": kind, "route": r["route"]["route"], "cells": f"{c['correct']}/{c['gt_cells']}",
                     "f1": c["f1"], "level": s["presentation_level"], "pres_parent": s["presentation_parent"],
                     "calc_parent": s["calc_parent"], "row_type": s["row_type"], "corrections": k["accuracy"],
                     "checks_failed": v["template"]["failed"] + v["dynamic"]["failed"], "coverage": v["coverage"],
                     "decision": r["acceptance"]["decision"]})
        print(rows[-1])
    (out / "geometry_results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:4])
