"""Minimal text-layer reader for this page, used only to cross-check the visual ground truth
and to supply what pixels cannot (exact bbox coordinates, dash codepoints).

Not the pipeline: column windows are hard-coded for this page.
"""
import pymupdf

PRIOR_X = (300, 400)     # right-edge window, prior-period column
CURRENT_X = (400, 600)   # right-edge window, current-period column
BODY_Y = (145, 676)      # between the header rule and the closing rule


def merge_touching(spans, gap=1.0):
    """Join spans whose x-gap is < gap pt (a separate '△' glyph run + its digits).
    Column assignment must happen on merged tokens, never on raw spans."""
    out = []
    for s in sorted(spans, key=lambda s: s["bbox"][0]):
        if out and s["bbox"][0] - out[-1]["bbox"][2] < gap:
            p = out[-1]
            p["t"] += s["t"]
            p["bbox"] = (p["bbox"][0], min(p["bbox"][1], s["bbox"][1]),
                         s["bbox"][2], max(p["bbox"][3], s["bbox"][3]))
        else:
            out.append(dict(s))
    return out


def read_rows(pdf_path):
    page = pymupdf.open(pdf_path)[0]
    spans = []
    for b in page.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = "".join(c["c"] for c in s["chars"])
                if t.strip() and BODY_Y[0] < s["bbox"][1] < BODY_Y[1]:
                    spans.append({"t": t.strip(), "bbox": tuple(s["bbox"])})
    groups = {}
    for s in spans:
        yc = round((s["bbox"][1] + s["bbox"][3]) / 2)
        key = next((k for k in groups if abs(k - yc) <= 2), yc)
        groups.setdefault(key, []).append(s)

    # correction underlines: thin black fills (<=0.6pt); column rules are 0.7pt and column-wide
    thin = [d["rect"] for d in page.get_drawings()
            if d.get("fill") == (0.0, 0.0, 0.0) and d["rect"].height <= 0.6]

    def underlined(bbox):
        return any(abs(r.x0 - bbox[0]) < 1.0 and abs(r.x1 - bbox[2]) < 1.0
                   and 0 <= r.y0 - bbox[3] < 1.5 for r in thin)

    rows = []
    for y in sorted(groups):
        toks = merge_touching(groups[y])
        label, rest = toks[0], toks[1:]
        cells = {}
        for col, (lo, hi) in (("prior", PRIOR_X), ("current", CURRENT_X)):
            hit = [t for t in rest if lo < t["bbox"][2] <= hi]
            if hit:
                t = hit[0]
                cells[col] = {"raw": t["t"], "bbox": [round(v, 1) for v in t["bbox"]],
                              "underlined": underlined(t["bbox"])}
        rows.append({"label": label["t"], "label_bbox": [round(v, 1) for v in label["bbox"]],
                     "x0": round(label["bbox"][0], 1), "cells": cells})
    levels = sorted({r["x0"] for r in rows})
    for r in rows:
        r["level"] = levels.index(r["x0"])
    return rows, levels
