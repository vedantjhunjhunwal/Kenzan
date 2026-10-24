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


def evaluate(pdf: str, gt_stem: str, out_dir: str, page: int = 0, route: str | None = None) -> dict:
    pred = parse_pdf(pdf, page, safe=True, force_route=route)      # failures become status "failed"
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
    for name, sec in (("cells", c), ("structure", s), ("corrections", k), ("validation", v)):
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
