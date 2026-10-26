"""Compare two stress runs of the same spec page by page (e.g. the parser before and after a change):
historical cell F1, every per-dimension score, and the decision. Scoring only.

usage: python stress/compare_runs.py <before_run_dir> <after_run_dir> <out.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from docparse.evaluate import score_dimensions  # noqa: E402

KEYS = ("values_printed", "labels", "paths", "structure_presentation_level", "structure_presentation_parent",
        "structure_calc_parent", "structure_row_type", "unit", "periods_columns", "periods_cells", "scaled_values",
        "rows_matched", "no_extra", "overall_score")


def main(before: str, after: str, out: str) -> dict:
    gt = json.loads((ROOT / "gt/out/ground_truth.json").read_text(encoding="utf-8"))
    res = {}
    for v in sorted(p.name for p in (Path(after) / "runs").iterdir()):
        side = {}
        for name, d in (("before", before), ("after", after)):
            pred = json.loads((Path(d) / "runs" / v / "prediction.json").read_text(encoding="utf-8"))
            rep = json.loads((Path(d) / "runs" / v / "report.json").read_text(encoding="utf-8"))
            sd = score_dimensions(gt, pred)
            side[name] = {"cell_f1": rep["cells"]["f1"], "decision": pred["acceptance"]["decision"],
                          **{k: sd[k] for k in KEYS},
                          "uncertain": [u["kind"] for u in pred["metadata"].get("layout", {}).get("uncertain", [])]}
        same = all(side["before"][k] == side["after"][k] for k in ("cell_f1", "decision", *KEYS))
        res[v] = side | {"identical_metrics_and_decision": same}
    summary = {"pages": len(res), "identical": sum(r["identical_metrics_and_decision"] for r in res.values())}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"summary": summary, "pages": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(summary)
    return res


if __name__ == "__main__":
    main(*sys.argv[1:4])
