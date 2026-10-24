"""Route a page to the born-digital or the visual path.

"Has a text layer" is not enough: some PDFs carry text that decodes to garbage (missing ToUnicode
maps, private-use glyph codes) or an invisible OCR layer over a scan. The gate requires:
  - enough characters,
  - >= 95% of them in scripts a Japanese filing uses (kana, kanji, ASCII, full-width forms,
    CJK punctuation, △/▲, ※ ...), no private-use or replacement characters,
  - >= 90% of number-like tokens parse as values,
  - the page is not mostly covered by an image (a scan with an OCR overlay).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

import pymupdf

from .extract import TextLayerBackend
from .merge import merge_tokens
from .normalize import is_value_token

MIN_CHARS = 30
MIN_KNOWN_RATIO = 0.95
MIN_NUMBER_PARSE_RATIO = 0.90
MAX_IMAGE_COVERAGE = 0.6

_KNOWN = [(0x20, 0x7E), (0xA0, 0xFF), (0x2010, 0x206F), (0x2160, 0x217F), (0x2190, 0x21FF),
          (0x2200, 0x22FF), (0x2460, 0x24FF), (0x2500, 0x25FF), (0x3000, 0x303F), (0x3040, 0x30FF),
          (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF)]


@dataclass
class RouteDecision:
    route: str                         # "born_digital" | "visual"
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _known(ch: str) -> bool:
    cp = ord(ch)
    if unicodedata.category(ch) in ("Co", "Cn", "Cs") or ch == "�":
        return False
    return any(lo <= cp <= hi for lo, hi in _KNOWN)


def _number_like(tok: str) -> bool:
    core = [c for c in tok if not c.isspace()]
    digits = sum(c.isdigit() or c in ",，" for c in core)
    return digits > 0 and digits / len(core) >= 0.6


def assess(chars: str, tokens: list[str], image_coverage: float) -> RouteDecision:
    """Pure decision function (unit-testable without PDFs)."""
    text = [c for c in chars if not c.isspace()]
    n = len(text)
    known = sum(_known(c) for c in text) / n if n else 0.0
    numeric = [t for t in tokens if _number_like(t)]
    parse_ok = sum(is_value_token(t) for t in numeric) / len(numeric) if numeric else 0.0
    m = {"chars": n, "known_char_ratio": round(known, 4), "number_like_tokens": len(numeric),
         "number_parse_ratio": round(parse_ok, 4), "image_coverage": round(image_coverage, 4)}
    reasons = []
    if n < MIN_CHARS:
        reasons.append(f"too little text ({n} chars)")
    if n and known < MIN_KNOWN_RATIO:
        reasons.append(f"text does not decode to real characters ({known:.1%} known)")
    if not numeric:
        reasons.append("no number-like tokens in the text layer")
    elif parse_ok < MIN_NUMBER_PARSE_RATIO:
        reasons.append(f"numbers do not parse ({parse_ok:.1%})")
    if image_coverage > MAX_IMAGE_COVERAGE:
        reasons.append(f"page is mostly image ({image_coverage:.0%}); text layer is likely an OCR overlay")
    return RouteDecision("visual" if reasons else "born_digital", reasons or ["text layer passes quality gate"], m)


def decide(page: pymupdf.Page) -> RouteDecision:
    content = TextLayerBackend().read(page)
    chars = "".join(s.text for s in content.spans)
    tokens = [t.text for line in merge_tokens(content.spans) for t in line]
    area = page.rect.width * page.rect.height
    img = 0.0
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        img += max(r.width, 0) * max(r.height, 0)
    return assess(chars, tokens, min(img / area, 1.0) if area else 0.0)
