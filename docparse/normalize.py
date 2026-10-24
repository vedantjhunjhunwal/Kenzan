"""Text normalization: labels, values, dates, units.

Every function here is page-independent. Values keep their printed form in raw_text; the
numeric fields are derived from it.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

# A cell consisting of exactly one of these glyphs means "nil" (explicitly nothing), never 0.
# Checked before NFKC so the original glyph survives in raw_text.
DASH_GLYPHS = frozenset("－―—–ー−-‐‑ｰ一")   # incl. katakana ー and kanji 一 (common OCR confusions)
NEG_MARKS = frozenset("△▲")
UNIT_MULTIPLIERS = {"円": 1, "千円": 10**3, "百万円": 10**6, "億円": 10**8}

_FOOTNOTE_RE = re.compile(r"[※＊*]\s*[0-9０-９]+(?:\s*[,，、・]\s*[※＊*]?\s*[0-9０-９]+)*")
_STRICT_NUM = re.compile(r"\d{1,3}(?:,\d{3})*")
_LOOSE_NUM = re.compile(r"[\d,]*\d")
_UNIT_RE = re.compile(r"単位\s*[:：]?\s*(百万円|千円|億円|円)")
_ERA = {"令和": 2018, "平成": 1988, "昭和": 1925}
_DATE_RE = re.compile(r"(令和|平成|昭和)?\s*(\d{1,4}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def label_norm(raw: str) -> tuple[str, list[str]]:
    """Return (normalized label, footnote refs).

    NFKC folds full-width brackets/digits to ASCII, so （純額） and (純額) give the same key;
    whitespace (incl. ideographic space) is removed; footnote marks are split off.
    """
    refs = ["※" + n for m in _FOOTNOTE_RE.finditer(raw) for n in re.findall(r"\d+", nfkc(m.group(0)))]
    s = _FOOTNOTE_RE.sub("", raw)
    s = re.sub(r"\s+", "", nfkc(s))
    return s, refs


@dataclass
class ParsedValue:
    raw_text: str
    reported_value: int | None
    is_negative: bool
    is_nil: bool
    warnings: list[str] = field(default_factory=list)


def parse_value(raw: str) -> ParsedValue | None:
    """Parse one value cell. Returns None if the token is not value-like at all.

    "△1,234" -> -1234;  "△0" -> 0 with is_negative=True;  "－" -> nil;
    "－1,234" (dash before digits) -> -1234 with warning "dash_minus" (ambiguous convention);
    "(1,234)" -> -1234 with warning "paren_negative";  bad digit grouping -> warning, value kept.
    """
    t = raw.strip()
    if not t:
        return None
    if len(t) == 1 and t in DASH_GLYPHS:
        return ParsedValue(t, None, False, True)
    s = re.sub(r"\s+", "", nfkc(t)).replace("，", ",")
    warnings: list[str] = []
    neg = False
    if s[0] in NEG_MARKS:
        neg, s = True, s[1:]
    elif s[0] in DASH_GLYPHS and len(s) > 1:
        neg, s = True, s[1:]
        warnings.append("dash_minus")
    elif s.startswith("(") and s.endswith(")") and len(s) > 2:
        neg, s = True, s[1:-1]
        warnings.append("paren_negative")
    if not _LOOSE_NUM.fullmatch(s):
        return None
    if "," in s and not _STRICT_NUM.fullmatch(s):
        warnings.append("bad_digit_grouping")
    mag = int(s.replace(",", ""))
    return ParsedValue(t, -mag if neg else mag, neg, False, warnings)


def is_value_token(raw: str) -> bool:
    return parse_value(raw) is not None


def parse_dates(text: str) -> list[str]:
    """All dates in text as ISO strings; handles Western years and 令和/平成/昭和 (incl. 元年)."""
    out = []
    for era, y, m, d in _DATE_RE.findall(nfkc(text)):
        year = 1 if y == "元" else int(y)
        if era:
            year += _ERA[era]
        elif year < 100:
            continue
        out.append(f"{year:04d}-{int(m):02d}-{int(d):02d}")
    return out


def parse_unit(text: str) -> tuple[str, int] | None:
    m = _UNIT_RE.search(nfkc(text).replace(" ", ""))
    if not m:
        return None
    return m.group(1), UNIT_MULTIPLIERS[m.group(1)]


def decimals_for(multiplier: int) -> int:
    return -round(math.log10(multiplier))
