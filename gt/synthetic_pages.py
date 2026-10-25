"""Synthetic layout fixtures: balance-sheet pages with wrapped labels and spanning header cells.

Each page is drawn from a fixed spec (SPEC below) with pymupdf, and its ground truth is written from
the SAME spec, so ground truth and page agree by construction. These are layout fixtures, not
evidence about real filings: the values are invented (but add up), the font is pymupdf's built-in
Droid Sans Fallback, and the geometry is chosen to exercise one pattern per page. The ground truth
does not import the parser; label paths and normalisation are computed here independently.

Variants (name -> what it exercises):
  wrap_last          long labels wrap, values printed on the LAST line, continuation flush with line 1,
                     rows have padding (pitch 16 pt, leading inside a cell 11 pt)
  wrap_first         values on the FIRST line, continuation with a one-character hanging indent
  wrap_last_hanging  values on the last line AND a hanging indent: geometrically the same as a header
                     followed by a child, told apart only by the tight leading
  span_header        a header cell spanning both value columns above the period headers
  ambiguous_wrap     wrapped label with leading == row pitch (no padding) and values on the last line:
                     the line could belong to the row above or below -> the parser must not guess
  span_header_date   a spanning header that carries a date -> no column can own it

usage: python gt/synthetic_pages.py <out_dir>      writes <variant>.pdf and <variant>.gt.json
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

import pymupdf

SIZE = 9.0
LEFT = 60.0
STEP = 9.0                     # indent step per level (one character)
LABEL_RIGHT = LEFT + 11 * SIZE  # the label column ends here; longer labels wrap
COL_RIGHT = (400.0, 525.0)
UNIT = ("千円", 1000)
PERIODS = [("prior", "前連結会計年度", "(2025年3月31日)", "2025-03-31"),
           ("current", "当連結会計年度", "(2026年3月31日)", "2026-03-31")]

# (key, label, level, prior, current, calc_parent key, row_type)
SPEC = [
    ("h_assets", "資産の部", 0, None, None, None, "header"),
    ("h_ca", "流動資産", 1, None, None, None, "header"),
    ("cash", "現金及び預金", 2, "1,200,000", "1,350,000", "ca", "line_item"),
    ("ar", "受取手形、売掛金及び契約資産", 2, "845,210", "901,334", "ca", "line_item"),
    ("inv", "商品及び製品", 2, "320,000", "298,765", "ca", "line_item"),
    ("raw", "原材料及び貯蔵品", 2, "85,400", "90,210", "ca", "line_item"),
    ("oth1", "その他", 2, "45,321", "50,012", "ca", "line_item"),
    ("allow", "貸倒引当金", 2, "△3,210", "△2,980", "ca", "line_item"),
    ("ca", "流動資産合計", 2, "2,492,721", "2,687,341", "ta", "subtotal"),
    ("h_nca", "固定資産", 1, None, None, None, "header"),
    ("h_ppe", "有形固定資産", 2, None, None, None, "header"),
    ("bld", "建物及び構築物", 3, "500,000", "520,000", "bld_net", "line_item"),
    ("dep", "減価償却累計額", 4, "△200,000", "△215,000", "bld_net", "line_item"),
    ("bld_net", "建物及び構築物（純額）", 4, "300,000", "305,000", "ppe", "subtotal"),
    ("land", "土地", 3, "400,000", "400,000", "ppe", "line_item"),
    ("ppe", "有形固定資産合計", 3, "700,000", "705,000", "nca", "subtotal"),
    ("h_int", "無形固定資産", 2, None, None, None, "header"),
    ("sw", "ソフトウエア", 3, "30,000", "28,000", "int", "line_item"),
    ("int", "無形固定資産合計", 3, "30,000", "28,000", "nca", "subtotal"),
    ("h_inv", "投資その他の資産", 2, None, None, None, "header"),
    ("sec", "投資有価証券", 3, "150,000", "160,000", "ioa", "line_item"),
    ("dta", "繰延税金資産", 3, "22,000", "21,000", "ioa", "line_item"),
    ("ret", "退職給付に係る資産", 3, "18,404", "16,435", "ioa", "line_item"),
    ("ioa", "投資その他の資産合計", 3, "190,404", "197,435", "nca", "subtotal"),
    ("nca", "固定資産合計", 2, "920,404", "930,435", "ta", "subtotal"),
    ("ta", "資産合計", 1, "3,413,125", "3,617,776", None, "total"),
]

VARIANTS = {
    #                    pitch, leading, values_on, hanging, spanning header text (None = none)
    "wrap_last":         (16.0, 11.0, "last", 0.0, None),
    "wrap_first":        (16.0, 11.0, "first", STEP, None),
    "wrap_last_hanging": (16.0, 11.0, "last", STEP, None),
    "span_header":       (16.0, 11.0, "last", 0.0, "前期及び当期の比較"),
    "ambiguous_wrap":    (13.0, 13.0, "last", 0.0, None),
    "span_header_date":  (16.0, 11.0, "last", 0.0, "2026年3月31日現在"),
}


def norm(label: str) -> str:
    return "".join(unicodedata.normalize("NFKC", label).split())


def wrap(label: str, level: int) -> list[str]:
    room = int((LABEL_RIGHT - (LEFT + level * STEP)) // SIZE)
    return [label[i:i + room] for i in range(0, len(label), room)]


def parse(raw: str | None):
    if raw is None:
        return None
    neg = raw.startswith("△")
    mag = int(raw.lstrip("△").replace(",", ""))
    return {"raw_text": raw, "reported_value": -mag if neg else mag, "value": (-mag if neg else mag) * UNIT[1],
            "decimals": -3, "is_negative": neg, "is_nil": False, "is_corrected": None}


def ground_truth(variant: str) -> dict:
    ids = {k: f"r{i:02d}" for i, (k, *_) in enumerate(SPEC, 1)}
    rows, stack = [], []
    for key, label, level, prior, current, parent, rtype in SPEC:
        while stack and stack[-1][1] >= level:
            stack.pop()
        pres_parent = stack[-1][0] if stack else None
        path = "/".join([norm(lbl) for _, _, lbl in stack] + [norm(label)])
        cells = {pid: c for pid, c in (("prior", parse(prior)), ("current", parse(current))) if c}
        rows.append({"row_id": ids[key], "label_ja": label, "label_norm": norm(label), "label_path": path,
                     "presentation_level": level, "presentation_parent": ids.get(pres_parent),
                     "calc_parent": ids.get(parent), "row_type": rtype, "cells": cells})
        stack.append((key, level, label))
    return {"metadata": {"source": {"synthetic": True, "variant": variant, "generator": "gt/synthetic_pages.py"},
                         "statement_type": "consolidated_balance_sheet", "unit_ja": UNIT[0],
                         "unit_multiplier": UNIT[1]},
            "periods": [{"id": pid, "label_ja": lab, "context": "instant", "date": date}
                        for pid, lab, _, date in PERIODS],
            "rows": rows}


def _text(page, x, y, s):
    page.insert_text((x, y), s, fontname="japan", fontsize=SIZE)


def _width(s: str) -> float:
    return len(s) * SIZE        # the built-in "japan" font's text layer gives every glyph a 1-em box


def _right(page, x1, y, s):
    _text(page, x1 - _width(s), y, s)


def draw(path: str | Path, variant: str) -> dict:
    pitch, leading, values_on, hanging, span = VARIANTS[variant]
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    _text(page, 240, 60, "連結貸借対照表")
    _right(page, COL_RIGHT[1], 80, "（単位：千円）")
    y = 100.0
    if span:
        mid = COL_RIGHT[0]           # centred on the boundary between the two value columns
        _text(page, mid - _width(span) / 2, y, span)
        y += 14
    for (_, lab, date, _), x1 in zip(PERIODS, COL_RIGHT):
        _right(page, x1, y, lab)
        _right(page, x1, y + 12, date)
    y += 12 + 28
    for _, label, level, prior, current, _, _ in SPEC:
        parts = wrap(label, level)
        x0 = LEFT + level * STEP
        vline = len(parts) - 1 if values_on == "last" else 0
        for k, part in enumerate(parts):
            _text(page, x0 + (hanging if k else 0.0), y + k * leading, part)
        vy = y + vline * leading
        for raw, x1 in zip((prior, current), COL_RIGHT):
            if raw is not None:
                _right(page, x1, vy, raw)
        y += (len(parts) - 1) * leading + pitch
    doc.save(str(path))
    return ground_truth(variant)


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for v in VARIANTS:
        gt = draw(out / f"{v}.pdf", v)
        (out / f"{v}.gt.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1), encoding="utf-8")
        print(v, "->", out / f"{v}.pdf")


if __name__ == "__main__":
    main(sys.argv[1])
