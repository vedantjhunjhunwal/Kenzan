"""Exports shared by the ground-truth builder and the pipeline, so both produce identical formats.

- <stem>.json       : {"metadata", "periods", "rows", "checks", "check_summary"}
- <stem>.wide.csv   : one line per row, period fields side by side (re-renders the printed table)
- <stem>.long.csv   : one line per cell, keyed on (label_path, period_end) -- the scoring file
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROW_COLS = ["row_id", "label_ja", "label_norm", "label_path", "label_en", "row_type", "presentation_level",
            "presentation_parent", "calc_parent", "calc_parent_source", "closes_header", "footnote_refs",
            "xbrl_candidate", "xbrl_confidence", "xbrl_verified", "human_verification"]
CELL_COLS = ["raw_text", "reported_value", "value", "decimals", "is_negative", "is_nil", "is_corrected",
             "confidence", "bbox"]
LONG_COLS = ["label_path", "period_end", "period_id", "row_id", "label_ja", "label_norm", "row_type",
             "presentation_level", "presentation_parent_path", "calc_parent_path", "calc_parent_source",
             *CELL_COLS]


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, (list, tuple, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v


def write_all(doc: dict, out_dir: str | Path, stem: str) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"json": out / f"{stem}.json", "wide": out / f"{stem}.wide.csv", "long": out / f"{stem}.long.csv"}
    paths["json"].write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=list), encoding="utf-8")

    periods = doc["periods"]
    with open(paths["wide"], "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(ROW_COLS + [f"{p['id']}_{c}" for p in periods for c in CELL_COLS])
        for r in doc["rows"]:
            line = [_fmt(r.get(c)) for c in ROW_COLS]
            for p in periods:
                cell = r["cells"].get(p["id"], {})
                line += [_fmt(cell.get(c)) for c in CELL_COLS]
            w.writerow(line)

    path_of = {r["row_id"]: r["label_path"] for r in doc["rows"]}
    with open(paths["long"], "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(LONG_COLS)
        for r in doc["rows"]:
            for p in periods:
                cell = r["cells"].get(p["id"])
                if cell is None:
                    continue
                w.writerow([r["label_path"], p["date"], p["id"], r["row_id"], r["label_ja"], r["label_norm"],
                            r["row_type"], r["presentation_level"], path_of.get(r["presentation_parent"], ""),
                            path_of.get(r["calc_parent"], ""), _fmt(r.get("calc_parent_source")),
                            *[_fmt(cell.get(c)) for c in CELL_COLS]])
    return paths


def read_long(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
