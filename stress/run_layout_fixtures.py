"""Parse the synthetic layout fixtures (gt/synthetic_pages.py) with a given pipeline and score them
with the current per-dimension metric. Used for the before/after table in docs/RESULTS.md §8.

usage: python stress/run_layout_fixtures.py <out.json> [<code_root>]
  code_root: a checkout of the pipeline to test (default: this repository), e.g. a `git archive` of
  an older commit. Parsing runs in a subprocess with that code; scoring uses this repository's
  docparse.evaluate.score_dimensions, so both sides are scored the same way.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gt"))
from docparse.evaluate import score_dimensions  # noqa: E402
import synthetic_pages  # noqa: E402

PARSE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from docparse.pipeline import parse_pdf
for pdf in sys.argv[2:]:
    d = parse_pdf(pdf)
    open(pdf + ".pred.json", "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, default=list))
"""


def main(out_path: str, code_root: str | None = None) -> dict:
    code = Path(code_root or ROOT).resolve()
    res = {"code_root": str(code) if code_root else "this repository", "fixtures": {}}
    with tempfile.TemporaryDirectory() as td:
        gts = {v: synthetic_pages.draw(Path(td) / f"{v}.pdf", v) for v in synthetic_pages.VARIANTS}
        subprocess.run([sys.executable, "-c", PARSE, str(code), *[str(Path(td) / f"{v}.pdf") for v in gts]],
                       check=True, cwd=td)
        for v, gt in gts.items():
            pred = json.loads((Path(td) / f"{v}.pdf.pred.json").read_text(encoding="utf-8"))
            sd = score_dimensions(gt, pred)
            res["fixtures"][v] = {"page_correct": sd["page_correct"], "overall_score": round(sd["overall_score"], 4),
                                  "decision": pred["acceptance"]["decision"],
                                  "reasons": [r.split()[0] for r in pred["acceptance"]["reasons"]],
                                  "values_printed": sd["values_printed"], "labels": sd["labels"],
                                  "rows_matched": sd["rows_matched"], "no_extra": sd["no_extra"]}
    res["page_correct"] = sum(f["page_correct"] for f in res["fixtures"].values())
    res["accepted"] = sum(f["decision"] == "accept" for f in res["fixtures"].values())
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    r = main(*sys.argv[1:3])
    for v, f in r["fixtures"].items():
        print(f"{v:18s} page_correct={f['page_correct']} decision={f['decision']} {f['reasons']}")
