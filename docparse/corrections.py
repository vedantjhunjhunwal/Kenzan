"""Correction marks (訂正 filings underline changed figures) per cell.

A horizontal segment just below a value token is a correction underline when it spans the
token's glyphs: its right end meets the token's right edge and it is no wider than the token.
Column rules (drawn above/below subtotals) span the whole column, so width separates them.
When a value fills its column, width cannot decide; then thickness must differ from the page's
rule thickness (learned as the median thickness of column-width segments).

Sources without vector data (OCR) give is_corrected=None: unknown, not False.
"""
from __future__ import annotations

import statistics

from .extract import Segment


def mark(rows: list[dict], segments: list[Segment] | None, char_size: float) -> dict:
    if segments is None:
        for r in rows:
            for c in r["cells"].values():
                c["is_corrected"] = None
        return {"underline_source": "none (no vector data)"}

    tol = 0.35 * char_size
    cells = [c for r in rows for c in r["cells"].values() if c.get("bbox")]
    tok_w = statistics.median([c["bbox"][2] - c["bbox"][0] for c in cells]) if cells else char_size * 4
    wide = [s for s in segments if s.width > 2.5 * tok_w]
    rule_thickness = statistics.median([s.thickness for s in wide]) if wide else None

    found = []
    for r in rows:
        for pid, c in r["cells"].items():
            c["is_corrected"] = False
            b = c.get("bbox")
            if not b:
                continue
            x0, _, x1, y1 = b
            width = x1 - x0
            below = [s for s in segments if y1 - 0.5 <= s.y0 <= y1 + 0.45 * char_size and s.x1 > x0 and s.x0 < x1]
            for s in below:
                glyph_width = abs(s.x1 - x1) <= tol and s.x0 >= x0 - tol and s.width <= width + 2 * tol
                if not glyph_width:
                    continue
                fills_column = s.width > 0.8 * tok_w * 2.5
                if fills_column and rule_thickness and abs(s.thickness - rule_thickness) <= 0.2 * rule_thickness:
                    continue
                c["is_corrected"] = True
                found.append({"row_id": r["row_id"], "period_id": pid, "thickness": s.thickness,
                              "width": round(s.width, 1)})
                break
    return {"underline_source": "vector segments", "rule_thickness": rule_thickness,
            "median_value_width": round(tok_w, 1), "column_width_segments": len(wide), "underlines": found}
