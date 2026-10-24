"""End-to-end page parser: route -> extract -> merge -> layout -> normalize -> hierarchy ->
corrections -> validate -> export (same schema as the ground truth).

usage: python -m docparse.pipeline <pdf> <out_dir> [--page N | --all] [--route born_digital|visual]

Every page result carries status: "ok", "no_table" (processed, no value columns found) or
"failed" (an exception, recorded with its reason). parse_document never stops on a bad page.
"""
from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

import pymupdf

from . import corrections, hierarchy, router
from .export import write_all
from .extract import TesseractBackend, TextLayerBackend
from .layout import analyze
from .merge import merge_tokens
from .normalize import decimals_for, label_norm, parse_value
from .validate import run_checks, summarize

FAILED_CHECK_PENALTY = 0.5


def parse_page(page: pymupdf.Page, *, force_route: str | None = None, ocr=None,
               mode: str = "truncate", source_file: str | None = None) -> dict:
    decision = router.decide(page) if force_route is None else router.RouteDecision(force_route, ["forced"])
    backend = TextLayerBackend() if decision.route == "born_digital" else (ocr or TesseractBackend())
    content = backend.read(page)
    lay = analyze(merge_tokens(content.spans))

    unit_ja, mult = lay.unit if lay.unit else (None, None)
    decimals = decimals_for(mult) if mult else None
    col_period = {c.idx: c.period["id"] for c in lay.columns}
    rows = []
    for i, br in enumerate(lay.body, 1):
        norm, refs = label_norm(br.label_text)
        cells = {}
        for ci, tok in br.values.items():
            pv = parse_value(tok.text)
            cells[col_period[ci]] = {
                "raw_text": pv.raw_text, "reported_value": pv.reported_value,
                "value": pv.reported_value * mult if (mult and pv.reported_value is not None) else None,
                "decimals": None if pv.is_nil else decimals,
                "is_negative": pv.is_negative, "is_nil": pv.is_nil, "is_corrected": None,
                "confidence": round(tok.conf, 3), "bbox": [round(v, 1) for v in tok.bbox],
                "warnings": pv.warnings,
            }
        rows.append({
            "row_id": f"r{i:02d}", "label_ja": br.label_text, "label_norm": norm, "label_path": None,
            "label_en": None, "row_type": None, "presentation_level": None, "presentation_parent": None,
            "calc_parent": None, "calc_parent_source": None, "calc_rule": None, "closes_header": None,
            "footnote_refs": refs, "xbrl_candidate": None, "xbrl_confidence": None, "xbrl_verified": False,
            "human_verification": None, "label_bbox": br.label_bbox,
            "label_x0": br.label_tokens[0].x0 if br.label_tokens else (min(t.x0 for t in br.values.values())),
            "cells": cells,
        })

    levels = hierarchy.assign_presentation(rows, lay.char_size)
    hierarchy.assign_calc(rows, lay.periods, mode)
    hierarchy.assign_label_paths(rows)
    corr_diag = corrections.mark(rows, content.segments, lay.char_size)
    checks, summary = run_checks(rows, lay.periods, lay.statement_type or "", mode)

    # confidence: extraction confidence, halved for cells in a failed check; flag unchecked cells
    covered, failed = set(), set()
    for c in checks:
        if c["status"] == "not_evaluable":
            continue
        ids = [c["total_row"], *c["child_rows"]]
        for rid in ids:
            covered.add((rid, c["period_id"]))
            if c["status"] == "fail":
                failed.add((rid, c["period_id"]))
    for r in rows:
        for pid, cell in r["cells"].items():
            if (r["row_id"], pid) in failed:
                cell["confidence"] = round(cell["confidence"] * FAILED_CHECK_PENALTY, 3)
            cell["checked"] = (r["row_id"], pid) in covered

    meta = {
        "company": None, "company_code": None,
        "revision": lay.revision, "statement_type": lay.statement_type, "statement_title_ja": lay.title,
        "unit_ja": unit_ja, "unit_multiplier": mult,
        "currency": "JPY" if unit_ja and unit_ja.endswith("円") else None, "decimals": decimals,
        "rounding_convention": f"{mode} (assumed)",
        "source": {"file": source_file, "page_index": page.number, "printed_page": lay.printed_page},
        "sections_present": [r["label_ja"] for r in rows if r["label_norm"].endswith("の部")],
        "route": {"route": decision.route, "reasons": decision.reasons, "metrics": decision.metrics,
                  "backend": backend.name},
        "layout": lay.diagnostics | {"indent_levels_x0": [round(x, 1) for x in levels]},
        "corrections": corr_diag,
    }
    periods = [{k: v for k, v in p.items() if k != "header_text"} for p in lay.periods]
    if not lay.columns:
        status, reason = "no_table", lay.diagnostics.get("error", "no value columns found")
    else:
        status, reason = "ok", None
    warn: dict[str, int] = {}
    for r in rows:
        for c in r["cells"].values():
            for w in c.get("warnings", []):
                warn[w] = warn.get(w, 0) + 1
    meta["warnings"] = warn
    return {"status": status, "reason": reason, "metadata": meta, "periods": periods, "rows": rows,
            "checks": checks, "check_summary": summary}


def failed_page(page_index: int, source_file: str | None, err: BaseException) -> dict:
    """Uniform record for a page that raised: same schema, empty content, status 'failed'."""
    return {"status": "failed", "reason": f"{type(err).__name__}: {err}",
            "metadata": {"source": {"file": source_file, "page_index": page_index},
                         "traceback": traceback.format_exception_only(type(err), err)[-1].strip()},
            "periods": [], "rows": [], "checks": [], "check_summary": summarize([])}


def parse_page_safe(page: pymupdf.Page, **kw) -> dict:
    try:
        return parse_page(page, **kw)
    except Exception as e:                       # one bad page must never stop a batch
        return failed_page(page.number, kw.get("source_file"), e)


def parse_document(path: str, pages: list[int] | None = None, **kw) -> list[dict]:
    doc = pymupdf.open(path)
    idx = range(doc.page_count) if pages is None else pages
    return [parse_page_safe(doc[i], source_file=Path(path).name, **kw) for i in idx]


def parse_pdf(path: str, page_index: int = 0, safe: bool = False, **kw) -> dict:
    page = pymupdf.open(path)[page_index]
    fn = parse_page_safe if safe else parse_page
    return fn(page, source_file=Path(path).name, **kw)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out_dir")
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="process every page")
    ap.add_argument("--route", choices=["born_digital", "visual"])
    a = ap.parse_args(argv)
    pages = None if a.all else [a.page]
    results = parse_document(a.pdf, pages, force_route=a.route)
    batch = []
    for res in results:
        pi = res["metadata"]["source"]["page_index"]
        entry = {"page_index": pi, "status": res["status"], "reason": res["reason"]}
        if res["status"] != "failed":
            paths = write_all(res, a.out_dir, f"{Path(a.pdf).stem}.p{pi}")
            entry |= {"outputs": {k: str(v) for k, v in paths.items()},
                      "route": res["metadata"]["route"]["route"], "check_summary": res["check_summary"],
                      "warnings": res["metadata"]["warnings"]}
        batch.append(entry)
        print(f"page {pi}: {res['status']}" + (f" ({res['reason']})" if res["reason"] else ""))
    Path(a.out_dir).mkdir(parents=True, exist_ok=True)
    Path(a.out_dir, "batch_report.json").write_text(json.dumps(batch, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")


if __name__ == "__main__":
    main()
