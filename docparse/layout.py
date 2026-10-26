"""Page layout: value columns, period headers, unit/title lines, and the table body rows.

Everything is learned from the page:
  value columns  clusters of right edges of value-like tokens (right-aligned numbers and dashes)
  periods        header text stacked above each column (labels + dates, Western or 和暦)
  body           from the line after the period header to the last line holding a column value
  row pitch      median distance between body rows (diagnostic; flags tight rows = possible wraps)
  wrapped labels a label printed over several lines is joined into one row (reconstruct_wrapped_labels);
                 the row keeps its line count and line boxes (a row span over physical lines)
  header cells   every header token with the value columns it covers; a token over two or more
                 columns is a column-spanning cell and never becomes part of one column's period text
  uncertainty    patterns the rules cannot decide (a label-only line that may belong to the row above
                 or below, a label-only line with nothing indented under it, a spanning header that
                 carries a date) are listed in diagnostics["uncertain"]; acceptance A11 sends the page
                 to review. This is a focused reconstruction for single-table statement pages, not
                 general PDF table support.
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
    line_bboxes: list[list[float]] = field(default_factory=list)   # one box per physical line (row span)
    value_line: int | None = None       # which of those lines holds the values (0-based); None = no values

    @property
    def lines(self) -> int:
        return max(len(self.line_bboxes), 1)

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
    header_cells: list[dict] = field(default_factory=list)


WRAP_TIGHT = 0.8         # x row pitch: a line this close to its neighbour is inside the same cell
WRAP_MAX_GAP = 1.25      # x row pitch: never join lines further apart than this
WRAP_CLEAR = 0.15        # x row pitch: the row above and below must differ this much to pick one
HANGING_INDENT = 1.5     # x char size: largest indent of a continuation line
SPAN_OVERLAP = 0.3       # share of a header token's width that must lie over a column to cover it


def _x0(r: BodyRow) -> float | None:
    return r.label_tokens[0].x0 if r.label_tokens else None


def _line_yc(r: BodyRow, k: int) -> float:
    """Vertical centre of physical line k of a row (k = -1: its last line)."""
    if r.line_bboxes:
        b = r.line_bboxes[k]
        return (b[1] + b[3]) / 2
    return (r.y0 + r.y1) / 2


def _join(a: BodyRow, b: BodyRow) -> BodyRow:
    """a above b, one logical row; at most one of them carries values."""
    vline = a.value_line if a.values else (a.lines + b.value_line if b.values else None)
    return BodyRow(a.label_tokens + b.label_tokens, a.values or b.values, min(a.y0, b.y0), max(a.y1, b.y1),
                   a.line_bboxes + b.line_bboxes, vline)


def reconstruct_wrapped_labels(body: list[BodyRow], size: float, pitch: float | None
                               ) -> tuple[list[BodyRow], list[dict], list[dict]]:
    """Join labels printed over several lines. Returns (rows, joins, uncertain).

    A label-only line B (no value in any column) is a section header when the next line is indented
    deeper; otherwise it is part of a neighbouring row:
      values on the last line   the next line C starts where B starts (or up to a hanging indent
                                deeper) and carries values (or is itself a label-only continuation);
      values on the first line  the previous line P carries values and B starts between P's x0 and a
                                hanging indent deeper.
    A join also needs the two lines closer than WRAP_TIGHT x row pitch (lines inside a cell sit closer
    than rows). Wrap-like but spaced like rows, or equally close to both neighbours -> uncertain
    "ambiguous_wrap" (left as is, A11). No wrap geometry and nothing indented below -> uncertain
    "label_only_row_without_children".
    """
    if not pitch or len(body) < 2:
        return body, [], []
    rows, joins, uncertain = list(body), [], []
    i = 0
    while i < len(rows):
        b = rows[i]
        if b.values or not b.label_tokens:
            i += 1
            continue
        p = rows[i - 1] if i > 0 else None
        c = rows[i + 1] if i + 1 < len(rows) else None
        xb = _x0(b)
        gap_c = _line_yc(c, 0) - _line_yc(b, -1) if c else None      # between adjacent physical lines
        gap_p = _line_yc(b, 0) - _line_yc(p, -1) if p else None
        deeper = c is not None and _x0(c) is not None and _x0(c) > xb + 0.5 * size
        # geometry of a wrap (start positions), then the spacing: lines inside one cell sit closer
        # than rows do. A wrap-like line spaced like a row may also be a row whose values were lost
        # (seen on noisy scans), so it is never joined on geometry alone.
        down_geo = (c is not None and _x0(c) is not None and gap_c <= WRAP_MAX_GAP * pitch
                    and (abs(_x0(c) - xb) <= 0.5 * size or (deeper and _x0(c) - xb <= HANGING_INDENT * size)))
        up_geo = (p is not None and p.values and _x0(p) is not None and not deeper
                  and gap_p <= WRAP_MAX_GAP * pitch and _x0(p) - 0.5 * size <= xb <= _x0(p) + HANGING_INDENT * size)
        down = down_geo and gap_c < WRAP_TIGHT * pitch and (bool(c.values) or not deeper)
        up = up_geo and gap_p < WRAP_TIGHT * pitch
        if deeper and not down:
            i += 1                                         # section header with indented rows below
            continue
        if down and up:
            if abs(gap_c - gap_p) <= WRAP_CLEAR * pitch:
                uncertain.append({"kind": "ambiguous_wrap", "text": b_text(b),
                                  "detail": "label-only line equally close to the rows above and below"})
                i += 1
                continue
            up, down = gap_p < gap_c, gap_c < gap_p
        if not (down or up) and (down_geo or up_geo):
            uncertain.append({"kind": "ambiguous_wrap", "text": b_text(b),
                              "detail": "label-only line placed like a continuation but spaced like a row "
                                        "(a wrapped label, or a row whose values are missing)"})
            i += 1
            continue
        if down:
            rows[i:i + 2] = [_join(b, c)]
            joins.append({"text": b_text(rows[i]), "lines": rows[i].lines, "values_on": "last"
                          if rows[i].value_line == rows[i].lines - 1 else "first" if rows[i].value_line == 0
                          else "none"})
            continue                                       # the joined row may continue further
        if up:
            rows[i - 1:i + 1] = [_join(p, b)]
            joins.append({"text": b_text(rows[i - 1]), "lines": rows[i - 1].lines, "values_on": "first"})
            i = max(i - 1, 0)
            continue
        uncertain.append({"kind": "label_only_row_without_children", "text": b_text(b),
                          "detail": "no value and nothing indented below it; not a recognisable wrap"})
        i += 1
    return rows, joins, uncertain


def b_text(r: BodyRow) -> str:
    return "".join(t.text for t in r.label_tokens)


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
    header_cells: list[dict] = []
    uncertain: list[dict] = []
    for i in range(first):
        over = []
        for t in lines[i]:
            if parse_unit(t.text):          # the unit line often sits over the last column
                continue
            w = max(t.x1 - t.x0, 1e-6)
            covers = [c.idx for c, lo, hi in regions if min(t.x1, hi) - max(t.x0, lo) > SPAN_OVERLAP * w]
            xc = (t.x0 + t.x1) / 2
            for c, lo, hi in regions:
                if lo < xc <= hi:
                    over.append((c.idx, t, covers))
                    break
        if over:
            header_end = i
            for ci, t, covers in over:
                spanning = len(covers) > 1
                header_cells.append({"text": t.text, "bbox": [round(v, 1) for v in t.bbox], "line": i,
                                     "columns": covers or [ci], "colspan": max(len(covers), 1)})
                if spanning:                 # a cell over several columns belongs to none of them alone
                    if parse_dates(t.text):
                        uncertain.append({"kind": "spanning_header_with_date", "text": t.text,
                                          "detail": f"header over columns {covers} carries a date"})
                    continue
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
        y0, y1 = min(t.bbox[1] for t in toks), max(t.bbox[3] for t in toks)
        box = [round(min(t.x0 for t in toks), 1), round(y0, 1), round(max(t.x1 for t in toks), 1), round(y1, 1)]
        body.append(BodyRow(label, vals, y0, y1, [box], 0 if vals else None))

    ycs0 = [(r.y0 + r.y1) / 2 for r in body]
    gaps0 = [b - a for a, b in zip(ycs0, ycs0[1:])]
    body, joins, wrap_uncertain = reconstruct_wrapped_labels(body, size, statistics.median(gaps0) if gaps0 else None)
    uncertain += wrap_uncertain
    if joins:
        diag["wrapped_labels"] = joins
    if uncertain:
        diag["uncertain"] = uncertain

    footer = [_line_text(l) for l in lines[last + 1:]]
    printed_page = next((m.group(1) for f in footer if (m := re.fullmatch(r"[-－‐\s]*(\d+)[-－‐\s]*", nfkc(f)))), None)
    ycs = [(r.y0 + r.y1) / 2 for r in body]
    gaps = [b - a for a, b in zip(ycs, ycs[1:])]
    pitch = statistics.median(gaps) if gaps else None
    diag.update(row_pitch=pitch, body_rows=len(body), header_lines=header_end + 1,
                tight_rows=[i for i, g in enumerate(gaps, 1) if pitch and g < 0.6 * pitch])
    return Layout(cols, periods, body, size, unit, title, stype, revision, printed_page, diag, header_cells)
