"""Apply the per-dimension metric (docparse.evaluate.score_dimensions, added 2026-10-25) to the
predictions already stored by every committed run. Scoring only: no page is parsed again, no
pipeline code runs, no decision changes. The historical cell F1 is copied next to the new numbers.

usage: python stress/score_dimensions_all.py [out.json]     (default stress/dimensions_all_runs.json)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from docparse.evaluate import score_dimensions  # noqa: E402

RUNS = ["out_geometry", "out_v1_seen", "out_v2_seen", "out_v1_unseen", "out_v2_unseen", "out_heldout_v3",
        "out_v2_unseen_rescore_A10", "out_heldout_v3_rescore_A10"]
KEYS = ["values_printed", "labels", "paths", "structure_presentation_level", "structure_calc_parent", "unit",
        "periods_columns", "scaled_values", "rows_matched", "no_extra", "overall_score"]


def main(out_path: str | None = None) -> dict:
    gt = json.loads((ROOT / "gt/out/ground_truth.json").read_text(encoding="utf-8"))
    out = {"note": __doc__.strip().splitlines()[0], "runs": {}}
    for run in RUNS:
        base = ROOT / "stress" / run / "runs"
        if not base.is_dir():
            continue
        res = {}
        for vdir in sorted(p for p in base.iterdir() if (p / "prediction.json").exists()):
            pred = json.loads((vdir / "prediction.json").read_text(encoding="utf-8"))
            rep = json.loads((vdir / "report.json").read_text(encoding="utf-8"))
            d = score_dimensions(gt, pred)
            res[vdir.name] = {"cell_f1_historical": rep["cells"]["f1"],
                              "decision": (rep.get("acceptance") or pred.get("acceptance") or {}).get("decision"),
                              **{k: d[k] for k in KEYS}, "page_correct": d["page_correct"],
                              "values_printed_correct": f"{d['counts']['values_printed_correct']}/{d['counts']['gt_cells']}"}
        degraded = {k: v for k, v in res.items() if k != "born_digital" and run != "out_geometry"}
        out["runs"][run] = {
            "pages": res,
            "page_correct": sum(v["page_correct"] for v in res.values()),
            "degraded_pages": len(degraded),
            "degraded_page_correct": sum(v["page_correct"] for v in degraded.values()),
            "degraded_mean_values_printed": (sum(v["values_printed"] for v in degraded.values()) / len(degraded)
                                             if degraded else None),
            "degraded_unit_correct": sum(v["unit"] == 1.0 for v in degraded.values()),
            "degraded_periods_correct": sum(v["periods_columns"] == 1.0 for v in degraded.values()),
        }
    path = Path(out_path or ROOT / "stress/dimensions_all_runs.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = main(*sys.argv[1:2])
    for run, r in o["runs"].items():
        print(f"{run:28s} page_correct {r['page_correct']}/{len(r['pages'])}  degraded: "
              f"{r['degraded_page_correct']}/{r['degraded_pages']} correct, mean values_printed "
              f"{r['degraded_mean_values_printed'] if r['degraded_mean_values_printed'] is None else round(r['degraded_mean_values_printed'], 3)}, "
              f"unit ok {r['degraded_unit_correct']}, periods ok {r['degraded_periods_correct']}")
