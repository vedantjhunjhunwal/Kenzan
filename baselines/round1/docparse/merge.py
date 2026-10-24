"""Turn spans into tokens: split on whitespace, then merge pieces that belong together.

Merging happens BEFORE column assignment. A '△' drawn as its own glyph run ends left of the
column's right edge; assigned on its own it would fall outside the column and the sign would be
lost. Merged with its digits, the token's right edge is the number's right edge.

Two merge rules (tolerances relative to font size, nothing page-specific):
  touching      gap < 0.2 x size: adjacent glyph runs ('△' + '1,234', split label glyphs)
  number parts  gap < 0.8 x size and the pieces continue a number: '2,' + '002,' + '429'
                (OCR inserts spaces after commas) or a sign mark followed by digits
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .extract import Span
from .normalize import NEG_MARKS

TOUCH = 0.2
NUMBER_GAP = 0.8


@dataclass
class Token:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    source: str = "text_layer"
    conf: float = 1.0
    parts: int = 1

    @property
    def x0(self): return self.bbox[0]

    @property
    def x1(self): return self.bbox[2]

    @property
    def yc(self): return (self.bbox[1] + self.bbox[3]) / 2

    @property
    def height(self): return self.bbox[3] - self.bbox[1]


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def split_spans(spans: list[Span]) -> list[Token]:
    out: list[Token] = []
    for s in spans:
        if not s.chars:                                    # OCR words: already split
            if s.text.strip():
                out.append(Token(s.text.strip(), s.bbox, s.size, s.source, s.conf))
            continue
        cur_t, cur_b = "", None
        for ch, bb in s.chars + [(" ", None)]:
            if ch.isspace():
                if cur_t:
                    out.append(Token(cur_t, cur_b, s.size, s.source, s.conf))
                cur_t, cur_b = "", None
            else:
                cur_t += ch
                cur_b = bb if cur_b is None else _union(cur_b, bb)
    return out


def group_lines(tokens: list[Token]) -> list[list[Token]]:
    """Cluster tokens into lines by vertical centre (tolerance: half the smaller token height)."""
    lines: list[list[Token]] = []
    for t in sorted(tokens, key=lambda t: t.yc):
        if lines:
            ref = lines[-1]
            yc = sum(x.yc for x in ref) / len(ref)
            h = min(min(x.height for x in ref), t.height)
            if abs(t.yc - yc) <= 0.5 * h:
                ref.append(t)
                continue
        lines.append([t])
    return [sorted(l, key=lambda t: t.x0) for l in lines]


def _should_merge(a: Token, b: Token) -> bool:
    gap = b.x0 - a.x1
    s = max(min(a.size, b.size), 1.0)
    if gap < TOUCH * s:
        return True
    if gap < NUMBER_GAP * s and b.text[:1].isdigit() and (a.text[-1:] in ",，" or a.text in NEG_MARKS):
        return True
    if gap < NUMBER_GAP * s and a.text[-1:].isdigit() and b.text[:1] in ",，":
        return True
    return False


def merge_line(line: list[Token]) -> list[Token]:
    out: list[Token] = []
    for t in line:
        if out and _should_merge(out[-1], t):
            a = out[-1]
            out[-1] = Token(a.text + t.text, _union(a.bbox, t.bbox), max(a.size, t.size), a.source,
                            min(a.conf, t.conf), a.parts + t.parts)
        else:
            out.append(t)
    return out


def merge_tokens(spans: list[Span]) -> list[list[Token]]:
    """Spans -> lines of merged tokens (top to bottom, left to right)."""
    return [merge_line(l) for l in group_lines(split_spans(spans))]
