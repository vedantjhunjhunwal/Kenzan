"""Evaluation harness: run the pipeline on a page and score it against ground truth.

usage: python -m docparse.evaluate <pdf> <gt_stem> <out_dir> [--page N] [--route ...]
  <gt_stem> is the ground-truth path without extension, e.g. gt/out/ground_truth

Metrics
  cells        P/R/F1 on (label_path, period_end) keys from the long files; a cell is correct when
               reported_value, is_nil and is_negative all match (page precision, no unit scaling).
               Keyed on paths, so a hierarchy error also costs cells -- reported as "path_mismatch".
  structure    rows aligned by label sequence (independent of paths), then accuracy of
               presentation_level, presentation_parent, calc_parent, row_type over ground-truth rows.
  corrections  is_corrected accuracy plus precision/recall of the positive class (2 of 74 cells
               are positive here, so accuracy alone says little). None (unknown) counts as wrong.
  validation   consistency (passed/evaluable) per category and template coverage, plus agreement of
               each check's status with the same check run on the ground truth.
  dimensions   (added 2026-10-25; the four sections above are unchanged, for historical comparison)
               each dimension scored on its own, so one error cannot hide behind another:
                 values_printed   printed value, sign, nil per cell; rows aligned by label sequence then by
                                  position, columns by position -> independent of labels, paths and dates
                 labels / paths   label_norm and label_path per aligned row
                 structure        level, presentation parent, calc parent, row type per aligned row
                 unit             unit_ja and multiplier equal
                 periods          per value column: period-end date and context equal; plus cells whose
                                  value sits under the right period-end date
                 scaled_values    per cell: value x multiplier (the yen amount) under the right date
                 rows             ground-truth rows matched, predicted rows extra
               page_correct is true only when EVERY dimension is perfect (no missing or extra rows or
               cells); overall_score is the mean of the dimension scores, so it is 1.0 only then.
               is_corrected is reported (page_correct_incl_corrections) but not required: scans
               cannot determine it.
"""
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path

from .export import read_long, write_all
from .pipeline import parse_pdf

CELL_FIELDS = ("reported_value", "is_nil", "is_negative")


def _prf(tp, n_pred, n_gt):
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gt if n_gt else 0.0
    return {"precision": p, "recall": r, "f1": (2 * p * r / (p + r)) if p + r else 0.0}


def score_cells(gt_rows: list[dict], pred_rows: list[dict]) -> dict:
    g = {(r["label_path"], r["period_end"]): r for r in gt_rows}
    p = {(r["label_path"], r["period_end"]): r for r in pred_rows}
    tp, diffs, info = 0, [], []
    for k in g.keys() & p.keys():
        bad = [f for f in CELL_FIELDS if g[k][f] != p[k][f]]
        if bad:
            diffs.append({"key": k, "kind": "value_mismatch", "fields": {f: (g[k][f], p[k][f]) for f in bad},
                          "gt_raw": g[k]["raw_text"], "pred_raw": p[k]["raw_text"]})
        else:
            tp += 1
            if g[k]["raw_text"] != p[k]["raw_text"]:
                info.append({"key": k, "kind": "raw_text_differs", "gt": g[k]["raw_text"], "pred": p[k]["raw_text"]})
            if g[k]["value"] != p[k]["value"]:
                diffs.append({"key": k, "kind": "yen_value_mismatch", "gt": g[k]["value"], "pred": p[k]["value"]})
    pred_by_label = {}
    for k in p.keys() - g.keys():
        pred_by_label.setdefault((p[k]["label_norm"], k[1]), []).append(k)
    for k in sorted(g.keys() - p.keys()):
        alt = pred_by_label.get((g[k]["label_norm"], k[1]))
        diffs.append({"key": k, "kind": "path_mismatch" if alt else "missing", "pred_keys_same_label": alt})
    for k in sorted(p.keys() - g.keys()):
        diffs.append({"key": k, "kind": "extra"})
    return {"gt_cells": len(g), "pred_cells": len(p), "correct": tp, **_prf(tp, len(p), len(g)),
            "differences": diffs, "info": info}


def align_rows(gt: list[dict], pred: list[dict]) -> dict[str, str]:
    """gt row_id -> pred row_id, by longest common subsequence of label_norm."""
    sm = difflib.SequenceMatcher(a=[r["label_norm"] for r in gt], b=[r["label_norm"] for r in pred],
                                 autojunk=False)
    m = {}
    for blk in sm.get_matching_blocks():
        for i in range(blk.size):
            m[gt[blk.a + i]["row_id"]] = pred[blk.b + i]["row_id"]
    return m


def score_structure(gt: dict, pred: dict) -> dict:
    gm = align_rows(gt["rows"], pred["rows"])
    pr = {r["row_id"]: r for r in pred["rows"]}
    inv = {v: k for k, v in gm.items()}
    fields = {"presentation_level": [], "presentation_parent": [], "calc_parent": [], "row_type": []}
    diffs = []
    for g in gt["rows"]:
        p = pr.get(gm.get(g["row_id"], ""))
        for f in fields:
            if p is None:
                ok, got = False, "<row not matched>"
            elif f in ("presentation_parent", "calc_parent"):
                got = inv.get(p[f]) if p[f] else None
                ok = got == g[f]
            else:
                got = p[f]
                ok = got == g[f]
            fields[f].append(ok)
            if not ok:
                diffs.append({"row": g["row_id"], "label": g["label_ja"], "field": f, "gt": g[f], "pred": got})
    out = {f: sum(v) / len(v) for f, v in fields.items()}
    out["rows_matched"] = f"{len(gm)}/{len(gt['rows'])}"
    out["differences"] = diffs
    return out


def score_corrections(gt: dict, pred: dict) -> dict:
    gm = align_rows(gt["rows"], pred["rows"])
    pr = {r["row_id"]: r for r in pred["rows"]}
    gper = {p["id"]: p["date"] for p in gt["periods"]}
    pper = {p["date"]: p["id"] for p in pred["periods"]}
    n = ok = tp = fp = fn = 0
    diffs = []
    for g in gt["rows"]:
        p = pr.get(gm.get(g["row_id"], ""))
        for gpid, gc in g["cells"].items():
            n += 1
            pc = p["cells"].get(pper.get(gper[gpid], ""), {}) if p else {}
            got = pc.get("is_corrected", "<missing>")
            if got == gc["is_corrected"]:
                ok += 1
                tp += got is True
            else:
                diffs.append({"row": g["row_id"], "label": g["label_ja"], "period": gper[gpid],
                              "gt": gc["is_corrected"], "pred": got})
                fp += got is True
                fn += gc["is_corrected"] is True
    decided = n - sum(1 for d in diffs if d["pred"] is None or d["pred"] == "<missing>")
    return {"accuracy": ok / n if n else None, "decided": decided, "cells": n, "positives_gt": tp + fn,
            **_prf(tp, tp + fp, tp + fn), "differences": diffs}


def score_validation(gt: dict, pred: dict) -> dict:
    gs = {(c["check_id"], c["period"]): c["status"] for c in gt["checks"]}
    ps = {(c["check_id"], c["period"]): c["status"] for c in pred["checks"]}
    disagree = [{"check": k, "gt": gs.get(k), "pred": ps.get(k)} for k in sorted(gs.keys() | ps.keys(), key=str)
                if gs.get(k) != ps.get(k)]
    return {"pred_summary": pred["check_summary"], "status_agreement_with_gt":
            1 - len(disagree) / max(len(gs.keys() | ps.keys()), 1), "differences": disagree}


def align_rows_positional(gt_rows: list[dict], pred_rows: list[dict]) -> dict[str, tuple[str, bool]]:
    """gt row_id -> (pred row_id, labels equal). Label-sequence alignment, then leftover rows are
    paired by order inside each unmatched gap, so a misread label still pairs with its row and its
    values are scored on their own (same rule as stress/run_stress.py's diagnosis)."""
    sm = difflib.SequenceMatcher(a=[r["label_norm"] for r in gt_rows], b=[r["label_norm"] for r in pred_rows],
                                 autojunk=False)
    m = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal", "replace"):
            for k in range(min(i2 - i1, j2 - j1)):
                m[gt_rows[i1 + k]["row_id"]] = (pred_rows[j1 + k]["row_id"], tag == "equal")
    return m


def _ratio(ok: int, n: int) -> float | None:
    return ok / n if n else None


def score_dimensions(gt: dict, pred: dict) -> dict:
    """Separate per-dimension scores and complete-page correctness (see module docstring)."""
    m = align_rows_positional(gt["rows"], pred["rows"])
    pr = {r["row_id"]: r for r in pred["rows"]}
    inv = {pid: gid for gid, (pid, _) in m.items()}
    gper, pper = gt["periods"], pred.get("periods", [])
    gdate = {p["id"]: p.get("date") for p in gper}
    pid_by_date = {p.get("date"): p["id"] for p in pper if p.get("date")}
    gcol = {p["id"]: k for k, p in enumerate(gper)}
    diffs: list[dict] = []

    def cell_sig(c):
        return None if c is None else (c.get("reported_value"), bool(c.get("is_negative")), bool(c.get("is_nil")))

    n_cells = v_ok = per_ok = sc_ok = corr_ok = 0
    lab_ok = path_ok = 0
    struct = {"presentation_level": 0, "presentation_parent": 0, "calc_parent": 0, "row_type": 0}
    for g in gt["rows"]:
        hit = m.get(g["row_id"])
        p = pr.get(hit[0]) if hit else None
        if p is None:
            diffs.append({"dimension": "rows", "row": g["row_id"], "label": g["label_ja"], "kind": "missing_row"})
        else:
            if p["label_norm"] == g["label_norm"]:
                lab_ok += 1
            else:
                diffs.append({"dimension": "labels", "row": g["row_id"], "gt": g["label_norm"], "pred": p["label_norm"]})
            if p.get("label_path") == g["label_path"]:
                path_ok += 1
            else:
                diffs.append({"dimension": "paths", "row": g["row_id"], "gt": g["label_path"],
                              "pred": p.get("label_path")})
            for f in struct:
                got = p.get(f)
                if f in ("presentation_parent", "calc_parent"):
                    got = inv.get(got) if got else None
                if got == g.get(f):
                    struct[f] += 1
                else:
                    diffs.append({"dimension": "structure", "row": g["row_id"], "field": f, "gt": g.get(f),
                                  "pred": got})
        for gpid, gc in g["cells"].items():
            n_cells += 1
            k = gcol[gpid]
            pc_pos = (p["cells"].get(pper[k]["id"]) if p is not None and k < len(pper) else None)
            if cell_sig(pc_pos) == cell_sig(gc):
                v_ok += 1
            else:
                diffs.append({"dimension": "values_printed", "row": g["row_id"], "column": k,
                              "gt": gc.get("raw_text"), "pred": None if pc_pos is None else pc_pos.get("raw_text")})
            dpid = pid_by_date.get(gdate[gpid])
            pc_date = p["cells"].get(dpid) if p is not None and dpid else None
            if cell_sig(pc_date) == cell_sig(gc):
                per_ok += 1
            if pc_date is not None and pc_date.get("value") == gc.get("value") and cell_sig(pc_date) == cell_sig(gc):
                sc_ok += 1
            else:
                diffs.append({"dimension": "scaled_values", "row": g["row_id"], "period": gdate[gpid],
                              "gt": gc.get("value"), "pred": None if pc_date is None else pc_date.get("value")})
            if pc_date is not None and pc_date.get("is_corrected") == gc.get("is_corrected"):
                corr_ok += 1
    matched_pred = {pid for pid, _ in m.values()}
    extra_rows = [r["row_id"] for r in pred["rows"] if r["row_id"] not in matched_pred]
    extra_cells = sum(len(r["cells"]) for r in pred["rows"] if r["row_id"] in extra_rows)
    g_by_id = {g["row_id"]: g for g in gt["rows"]}
    for r in pred["rows"]:                       # cells in aligned rows under columns the gt row leaves empty
        g = g_by_id.get(inv.get(r["row_id"]))
        if g is not None:
            expected = {pper[gcol[x]]["id"] for x in g["cells"] if gcol[x] < len(pper)}
            extra_cells += sum(1 for pid in r["cells"] if pid not in expected)
    for rid in extra_rows:
        diffs.append({"dimension": "rows", "row": rid, "label": pr[rid]["label_ja"], "kind": "extra_row"})

    gm, pm = gt["metadata"], pred.get("metadata", {})
    unit_ok = gm.get("unit_ja") == pm.get("unit_ja") and gm.get("unit_multiplier") == pm.get("unit_multiplier")
    if not unit_ok:
        diffs.append({"dimension": "unit", "gt": [gm.get("unit_ja"), gm.get("unit_multiplier")],
                      "pred": [pm.get("unit_ja"), pm.get("unit_multiplier")]})
    col_ok = 0
    for k, gp in enumerate(gper):
        pp = pper[k] if k < len(pper) else {}
        if pp.get("date") == gp.get("date") and pp.get("context") == gp.get("context"):
            col_ok += 1
        else:
            diffs.append({"dimension": "periods", "column": k, "gt": [gp.get("date"), gp.get("context")],
                          "pred": [pp.get("date"), pp.get("context")]})
    n_rows = len(gt["rows"])
    dims = {
        "values_printed": _ratio(v_ok, n_cells),
        "labels": _ratio(lab_ok, n_rows),
        "paths": _ratio(path_ok, n_rows),
        **{f"structure_{f}": _ratio(v, n_rows) for f, v in struct.items()},
        "unit": 1.0 if unit_ok else 0.0,
        "periods_columns": _ratio(col_ok, len(gper)) if len(pper) == len(gper) else 0.0,
        "periods_cells": _ratio(per_ok, n_cells),
        "scaled_values": _ratio(sc_ok, n_cells),
        "rows_matched": _ratio(len(m), n_rows),
        "no_extra": 1.0 if not extra_rows and not extra_cells else 0.0,
    }
    scores = [v for v in dims.values() if v is not None]
    page_correct = (pred.get("status") == "ok" and all(v == 1.0 for v in scores))
    return {
        **dims,
        "counts": {"gt_cells": n_cells, "values_printed_correct": v_ok, "cells_under_correct_period": per_ok,
                   "scaled_values_correct": sc_ok, "gt_rows": n_rows, "rows_matched": len(m),
                   "labels_correct": lab_ok, "paths_correct": path_ok, "extra_rows": len(extra_rows),
                   "extra_cells": extra_cells, "period_columns_gt": len(gper), "period_columns_pred": len(pper),
                   "period_columns_correct": col_ok, "is_corrected_correct": corr_ok},
        "overall_score": sum(scores) / len(scores) if scores else 0.0,
        "page_correct": page_correct,
        "page_correct_incl_corrections": page_correct and corr_ok == n_cells,
        "differences": diffs,
    }


def evaluate(pdf: str, gt_stem: str, out_dir: str, page: int = 0, route: str | None = None,
             backend=None) -> dict:
    """backend: optional visual-route backend (e.g. docparse.vlm.VLMBackend); default Tesseract."""
    pred = parse_pdf(pdf, page, safe=True, force_route=route, ocr=backend)   # failures -> status "failed"
    paths = write_all(pred, out_dir, "prediction")
    gt = json.loads(Path(f"{gt_stem}.json").read_text(encoding="utf-8"))
    rep = {
        "status": pred["status"], "reason": pred["reason"],
        "route": pred["metadata"].get("route", {"route": "n/a", "reasons": [], "backend": "n/a"}),
        "warnings": pred["metadata"].get("warnings", {}),
        "acceptance": pred["acceptance"],
        "cells": score_cells(read_long(f"{gt_stem}.long.csv"), read_long(paths["long"])),
        "structure": score_structure(gt, pred),
        "corrections": score_corrections(gt, pred),
        "validation": score_validation(gt, pred),
        "dimensions": score_dimensions(gt, pred),
    }
    Path(out_dir, "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=list),
                                            encoding="utf-8")
    return rep


def print_report(rep: dict) -> None:
    c, s, k, v = rep["cells"], rep["structure"], rep["corrections"], rep["validation"]
    ps = v["pred_summary"]
    print(f"status: {rep['status']}" + (f"  reason: {rep['reason']}" if rep["reason"] else ""))
    print(f"route: {rep['route']['route']} ({rep['route'].get('backend', '')})  {rep['route']['reasons']}")
    print(f"warnings: {rep['warnings'] or 'none'}")
    acc = rep["acceptance"]
    print(f"acceptance: {acc['decision'].upper()}" + (f"  {acc['reasons']}" if acc["reasons"] else ""))
    print(f"cells        P={c['precision']:.2%} R={c['recall']:.2%} F1={c['f1']:.2%}  "
          f"({c['correct']}/{c['gt_cells']} gt, {c['pred_cells']} predicted)")
    print(f"structure    rows matched {s['rows_matched']}  level={s['presentation_level']:.2%}  "
          f"pres_parent={s['presentation_parent']:.2%}  calc_parent={s['calc_parent']:.2%}  "
          f"row_type={s['row_type']:.2%}")
    print(f"corrections  accuracy={k['accuracy']:.2%}  decided={k['decided']}/{k['cells']}  "
          f"positives P={k['precision']:.2%} R={k['recall']:.2%} (gt positives {k['positives_gt']})")
    fmt = lambda x: "n/a" if x is None else f"{x:.2%}"
    print(f"validation   consistency template={fmt(ps['template']['consistency'])} "
          f"dynamic={fmt(ps['dynamic']['consistency'])} solver={fmt(ps['solver']['consistency'])} "
          f"(solver checks: {ps['solver']['checks']})  coverage={fmt(ps['coverage'])} "
          f"({ps['template_evaluable']}/{ps['template_expected']})  "
          f"status agreement with GT={v['status_agreement_with_gt']:.2%}")
    dm = rep["dimensions"]
    pct = lambda x: "n/a" if x is None else f"{x:.2%}"
    print(f"dimensions   values_printed={pct(dm['values_printed'])} labels={pct(dm['labels'])} "
          f"paths={pct(dm['paths'])} unit={'ok' if dm['unit'] == 1.0 else 'WRONG'} "
          f"periods={pct(dm['periods_columns'])} scaled_values={pct(dm['scaled_values'])} "
          f"rows_matched={pct(dm['rows_matched'])} extra={dm['counts']['extra_rows']} rows/"
          f"{dm['counts']['extra_cells']} cells")
    print(f"page         page_correct={dm['page_correct']}  overall_score={dm['overall_score']:.4f}  "
          f"(incl. is_corrected: {dm['page_correct_incl_corrections']})")
    for name, sec in (("cells", c), ("structure", s), ("corrections", k), ("validation", v), ("dimensions", dm)):
        for d in sec["differences"]:
            print(f"  DIFF {name}: {d}")
    for d in c.get("info", []):
        print(f"  info cells: {d}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("gt_stem")
    ap.add_argument("out_dir")
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--route", choices=["born_digital", "visual"])
    a = ap.parse_args(argv)
    print_report(evaluate(a.pdf, a.gt_stem, a.out_dir, a.page, a.route))


if __name__ == "__main__":
    main()
