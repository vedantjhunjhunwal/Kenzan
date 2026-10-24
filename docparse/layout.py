"""Page layout: value columns, period headers, unit/title lines, and the table body rows.

Everything is learned from the page:
  value columns  clusters of right edges of value-like tokens (right-aligned numbers and dashes)
  periods        header text stacked above each column (labels + dates, Western or 和暦)
  body           from the line after the period header to the last line holding a column value
  row pitch      median distance between body rows (diagnostic; flags tight rows = possible wraps)
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from .merge import Token
from .normalize import is_value_token, nfkc, parse_dates, parse_unit

STATEMENT_TITLES = [  # longest first so 連結貸借対照表 wins over 貸借対照表
    ("連結キャッシュ・フロー計算書", "consolidated_cash_flow_statement"),
    ("連結株主資本等変動計算書", "consolidated_statement_of_changes_in_equity"),
    ("連結包括利益計算書", "consolidated_statement_of_comprehensive_income"),
    ("連結損益計算書", "consolidated_income_statement"),
    ("連結貸借対照表", "consolidated_balance_sheet"),
    ("連結財政状態計算書", "consolidated_statement_of_financial_position"),
    ("キャッシュ・フロー計算書", "cash_flow_statement"),
    ("損益計算書", "income_statement"),
    ("貸借対照表", "balance_sheet"),
]
INSTANT_TYPES = {"consolidated_balance_sheet", "balance_sheet", "consolidated_statement_of_financial_position"}


@dataclass
class Column:
    idx: int
    right: float
    left: float
    period: dict | None = None


@dataclass
class BodyRow:
    label_tokens: list[Token]
    values: dict[int, Token]
    y0: float
    y1: float

    @property
    def label_text(self) -> str:
        parts, prev = [], None
        for t in self.label_tokens:
            if prev is not None and t.x0 - prev.x1 > 0.5 * t.size:
                parts.append(" ")
            parts.append(t.text)
            prev = t
        return "".join(parts)

    @property
    def label_bbox(self):
        if not self.label_tokens:
            return None
        b = [t.bbox for t in self.label_tokens]
        return [round(min(x[0] for x in b), 1), round(min(x[1] for x in b), 1),
                round(max(x[2] for x in b), 1), round(max(x[3] for x in b), 1)]


@dataclass
class Layout:
    columns: list[Column]
    periods: list[dict]
    body: list[BodyRow]
    char_size: float
    unit: tuple[str, int] | None
    title: str | None
    statement_type: str | None
    revision: str | None
    printed_page: str | None
    diagnostics: dict = field(default_factory=dict)


def _line_text(line: list[Token]) -> str:
    return "".join(t.text for t in line)


def find_columns(lines: list[list[Token]], size: float) -> list[Column]:
    cands = [t for line in lines for t in line[1:] if is_value_token(t.text)]
    if not cands:
        return []
    edges = sorted(t.x1 for t in cands)
    groups: list[list[float]] = []
    for e in edges:
        if groups and e - groups[-1][-1] <= 1.0 * size:
            groups[-1].append(e)
        else:
            groups.append([e])
    n_value_lines = sum(1 for line in lines if any(is_value_token(t.text) for t in line[1:]))
    keep = [g for g in groups if len(g) >= max(2, 0.2 * n_value_lines)]
    cols = []
    for i, g in enumerate(sorted(keep, key=lambda g: g[0])):
        right = statistics.median(g)
        members = [t for t in cands if abs(t.x1 - right) <= 0.6 * size]
        cols.append(Column(i, right, min(t.x0 for t in members)))
    return cols


def _assign_values(line: list[Token], cols: list[Column], size: float) -> tuple[dict[int, Token], list[Token]]:
    vals: dict[int, Token] = {}
    rest = []
    for t in line:
        hit = [c for c in cols if abs(t.x1 - c.right) <= 0.6 * size and is_value_token(t.text)]
        if hit and hit[0].idx not in vals:
            vals[hit[0].idx] = t
        else:
            rest.append(t)
    return vals, rest


def _period_from_text(text: str, idx: int, statement_type: str | None) -> dict:
    t = nfkc(text)
    dates = parse_dates(t)
    label = re.split(r"[(（]", t, maxsplit=1)[0].strip() or None
    pid = "prior" if label and "前" in label else "current" if label and "当" in label else f"col{idx}"
    p = {"id": pid, "label_ja": label, "header_text": text, "context": None, "date": None}
    if len(dates) >= 2 or "自" in t and "至" in t:
        p.update(context="duration", start=dates[0] if dates else None, date=dates[-1] if dates else None)
    elif dates:
        p.update(context="instant" if (statement_type in INSTANT_TYPES or statement_type is None) else "duration",
                 date=dates[-1])
    return p


def analyze(lines: list[list[Token]]) -> Layout:
    sizes = [t.size for line in lines for t in line]
    size = statistics.median(sizes) if sizes else 10.0
    cols = find_columns(lines, size)
    assigned = [_assign_values(line, cols, size) for line in lines]
    valued = [i for i, (v, _) in enumerate(assigned) if v]
    diag: dict = {"char_size": size, "column_right_edges": [round(c.right, 1) for c in cols]}
    if not cols or not valued:
        return Layout(cols, [], [], size, None, None, None, None, None, diag | {"error": "no value columns"})
    first, last = valued[0], valued[-1]
    left_of_values = min(c.left for c in cols) - 0.5 * size

    # header zone: lines above the first valued line; period header = lines with text over a column
    # each column's header region reaches left to the previous column (or one column-spacing)
    spacing = min((b.right - a.right for a, b in zip(cols, cols[1:])), default=None)
    regions = []
    for k, c in enumerate(cols):
        lo = cols[k - 1].right if k > 0 else c.right - (spacing or (c.right - c.left + 6 * size))
        regions.append((c, lo, c.right + size))
    header_end = -1
    col_text: dict[int, list[str]] = {c.idx: [] for c in cols}
    for i in range(first):
        over = []
        for t in lines[i]:
            if parse_unit(t.text):          # the unit line often sits over the last column
                continue
            xc = (t.x0 + t.x1) / 2
            for c, lo, hi in regions:
                if lo < xc <= hi:
                    over.append((c.idx, t))
                    break
        if over:
            header_end = i
            for ci, t in over:
                col_text[ci].append(t.text)
    head_text = " ".join(_line_text(l) for l in lines[: max(header_end + 1, 0)] or lines[:first])
    title, stype = None, None
    for kw, st in STATEMENT_TITLES:
        if kw in head_text:
            title, stype = kw, st
            break
    unit = parse_unit(head_text)
    revision = "訂正後" if "訂正後" in head_text else "訂正前" if "訂正前" in head_text else None
    periods = []
    for c in cols:
        if col_text[c.idx]:
            c.period = _period_from_text("".join(col_text[c.idx]), c.idx, stype)
        else:
            c.period = {"id": f"col{c.idx}", "label_ja": None, "header_text": "", "context": None, "date": None}
        periods.append(c.period)

    body = []
    for i in range(header_end + 1, last + 1):
        vals, rest = assigned[i]
        label = [t for t in rest if t.x1 <= left_of_values]
        orphans = [t for t in rest if t.x1 > left_of_values]
        if not label and not vals:
            continue
        if orphans:
            diag.setdefault("orphan_tokens", []).append({"line": i, "texts": [t.text for t in orphans]})
        toks = label + list(vals.values())
        body.append(BodyRow(label, vals, min(t.bbox[1] for t in toks), max(t.bbox[3] for t in toks)))

    footer = [_line_text(l) for l in lines[last + 1:]]
    printed_page = next((m.group(1) for f in footer if (m := re.fullmatch(r"[-－‐\s]*(\d+)[-－‐\s]*", nfkc(f)))), None)
    ycs = [(r.y0 + r.y1) / 2 for r in body]
    gaps = [b - a for a, b in zip(ycs, ycs[1:])]
    pitch = statistics.median(gaps) if gaps else None
    diag.update(row_pitch=pitch, body_rows=len(body), header_lines=header_end + 1,
                tight_rows=[i for i, g in enumerate(gaps, 1) if pitch and g < 0.6 * pitch])
    return Layout(cols, periods, body, size, unit, title, stype, revision, printed_page, diag)
