import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
GT_STEM = ROOT / "gt" / "out" / "ground_truth"

from docparse.extract import Span  # noqa: E402


def make_span(text: str, x0: float, y0: float, size: float = 9.0, right: float | None = None) -> Span:
    """Synthetic span with per-glyph boxes (ASCII = half width). If `right` is given, the span is
    right-aligned to that x (how numbers are typeset)."""
    widths = [size * (0.5 if ord(c) < 0x2000 else 1.0) for c in text]
    if right is not None:
        x0 = right - sum(widths)
    chars, x = [], x0
    for c, w in zip(text, widths):
        chars.append((c, (x, y0, x + w, y0 + size)))
        x += w
    return Span(text, (x0, y0, x, y0 + size), size, "synthetic", chars)


def make_cells(**periods):
    """make_cells(prior=123, current=-5) -> cells dict; use 'nil' for a dash, '△0' for negative zero."""
    out = {}
    for pid, v in periods.items():
        if v == "nil":
            out[pid] = {"reported_value": None, "is_negative": False, "is_nil": True}
        elif v == "△0":
            out[pid] = {"reported_value": 0, "is_negative": True, "is_nil": False}
        else:
            out[pid] = {"reported_value": v, "is_negative": v < 0, "is_nil": False}
    return out
