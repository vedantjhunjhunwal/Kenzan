"""Run the PDF parser (docparse) on real filings and score it against their XBRL ground truth.

Inputs are the outputs of the existing tools, nothing else:
  <raw>/filings/<docID>/bs_pages.pdf     balance-sheet pages cut by bench/fetch_edinet.py (--local)
  <gt>/<docID>/ground_truth.json         ground truth from bench/build_gt_xbrl.py (alignment recorded)
For every filing whose ground truth was KEPT by the alignment check, every page of bs_pages.pdf goes
through docparse.pipeline (route chosen by the router, as in production). Pages are concatenated in
order and scored with docparse.evaluate.score_dimensions. Cross-page stitching is not implemented, so
a statement over two pages is reported with "multi_page": true; its structure dimensions are expected
to fail and its pages are decided one by one.

Per filing the report records status and decision of every page, every dimension, page_correct, and
the first differences (the failures), so a reader can see where it breaks. Filings whose ground
truth was not kept (alignment, build error) are listed with that reason, not scored.

Sealed test filings (split "test"): run with --sealed. The run refuses unless the current pipeline
fingerprint is recorded in bench/FREEZE_LOG.txt on a line starting "REAL EVAL FROZEN", and refuses to
write into an output folder that already holds a sealed result (each sealed filing is evaluated once).

usage: python bench/eval_real.py <raw_dir> <gt_dir> <out_dir> [--sealed]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stress"))
from docparse.evaluate import score_dimensions  # noqa: E402
from docparse.pipeline import parse_document  # noqa: E402
from run_stress import code_fingerprint  # noqa: E402

FREEZE_LOG = ROOT / "bench" / "FREEZE_LOG.txt"
DIMS = ("values_printed", "labels", "paths", "structure_presentation_level", "structure_calc_parent", "unit",
        "periods_columns", "scaled_values", "rows_matched", "no_extra", "overall_score")


def combine(pages: list[dict]) -> dict:
    """One document from per-page results: rows in page order with unique ids, the first page's
    periods and metadata (unit, title)."""
    ok = [p for p in pages if p["status"] == "ok"]
    if not ok:
        return {"status": pages[0]["status"] if pages else "failed", "metadata": {}, "periods": [], "rows": []}
    rows, n = [], 0
    for k, p in enumerate(ok):
        ren = {}
        for r in p["rows"]:
            n += 1
            ren[r["row_id"]] = f"p{k}_{r['row_id']}"
        for r in p["rows"]:
            r = dict(r)
            for f in ("row_id", "presentation_parent", "calc_parent"):
                if r.get(f):
                    r[f] = ren.get(r[f], r[f])
            rows.append(r)
    first = ok[0]
    return {"status": "ok", "metadata": first["metadata"], "periods": first["periods"], "rows": rows}


def evaluate_filing(pdf: Path, gt: dict) -> dict:
    pages = parse_document(str(pdf))
    doc = combine(pages)
    sd = score_dimensions(gt, doc)
    return {
        "pages": [{"page_index": p["metadata"]["source"]["page_index"], "status": p["status"],
                   "reason": p.get("reason"), "route": (p["metadata"].get("route") or {}).get("route"),
                   "decision": p["acceptance"]["decision"], "reasons": p["acceptance"]["reasons"]} for p in pages],
        "multi_page": len(pages) > 1,
        **{d: sd[d] for d in DIMS},
        "page_correct": sd["page_correct"],
        "accepted_all_pages": all(p["acceptance"]["decision"] == "accept" for p in pages),
        "counts": sd["counts"],
        "first_differences": sd["differences"][:25],
    }


def frozen_fingerprints() -> set[str]:
    if not FREEZE_LOG.exists():
        return set()
    return {ln.split()[3] for ln in FREEZE_LOG.read_text(encoding="utf-8").splitlines()
            if ln.startswith("REAL EVAL FROZEN") and len(ln.split()) > 3}


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_dir")
    ap.add_argument("gt_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--sealed", action="store_true", help="the sealed test set: frozen code, one run only")
    a = ap.parse_args(argv)
    raw, gtd, out = Path(a.raw_dir), Path(a.gt_dir), Path(a.out_dir)
    fp = code_fingerprint()
    if a.sealed:
        if fp not in frozen_fingerprints():
            raise SystemExit(f"refused: pipeline fingerprint {fp} is not logged as 'REAL EVAL FROZEN {fp}' in "
                             f"{FREEZE_LOG}; freeze the code first")
        if (out / "real_eval.json").exists():
            raise SystemExit(f"refused: {out} already holds a sealed result; sealed filings are evaluated once")
    out.mkdir(parents=True, exist_ok=True)
    results = {}
    for fdir in sorted((raw / "filings").iterdir()) if (raw / "filings").is_dir() else []:
        gt_path = gtd / fdir.name / "ground_truth.json"
        if not gt_path.exists():
            results[fdir.name] = {"scored": False, "why": "no ground truth built"}
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        al = (gt["metadata"].get("alignment") or {}).get("status")
        if al != "kept":
            results[fdir.name] = {"scored": False, "why": f"ground truth not kept: {al}"}
            continue
        try:
            results[fdir.name] = {"scored": True, **evaluate_filing(fdir / "bs_pages.pdf", gt)}
        except Exception as e:                     # one bad filing never stops the run; it is a result
            results[fdir.name] = {"scored": True, "error": f"{type(e).__name__}: {e}"[:300], "page_correct": False,
                                  "accepted_all_pages": False}
        r = results[fdir.name]
        print(f"{fdir.name}: page_correct={r.get('page_correct')} accepted={r.get('accepted_all_pages')} "
              f"values={r.get('values_printed')} {r.get('error', '')}", flush=True)
    scored = {k: v for k, v in results.items() if v.get("scored")}
    summary = {"fingerprint": fp, "sealed": a.sealed, "filings": len(results), "scored": len(scored),
               "page_correct": sum(bool(v.get("page_correct")) for v in scored.values()),
               "accepted": sum(bool(v.get("accepted_all_pages")) for v in scored.values()),
               "accepted_and_incorrect": sum(bool(v.get("accepted_all_pages")) and not v.get("page_correct")
                                             for v in scored.values()),
               "correct_but_review": sum(bool(v.get("page_correct")) and not v.get("accepted_all_pages")
                                         for v in scored.values())}
    res = {"summary": summary, "filings": results}
    (out / "real_eval.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=list),
                                        encoding="utf-8")
    (out / "real_eval.md").write_text(to_markdown(res), encoding="utf-8")
    print(json.dumps(summary))
    return res


def to_markdown(res: dict) -> str:
    s = res["summary"]
    pct = lambda x: "n/a" if x is None else f"{x:.1%}"
    L = ["# Real-filing PDF evaluation", "",
         f"Pipeline fingerprint `{s['fingerprint']}`; sealed: {s['sealed']}. Generated by `bench/eval_real.py`.", "",
         f"Scored {s['scored']} of {s['filings']} filings; page-correct {s['page_correct']}; accepted {s['accepted']}; "
         f"accepted but incorrect {s['accepted_and_incorrect']}; correct but sent to review {s['correct_but_review']}.", "",
         "| docID | Pages | Values printed | Labels | Paths | Unit | Periods | Scaled values | Page correct | Decisions |",
         "|---|--:|--:|--:|--:|---|--:|--:|---|---|"]
    for d, r in res["filings"].items():
        if not r.get("scored"):
            L.append(f"| {d} | – | not scored: {r['why']} | | | | | | | |")
            continue
        if "error" in r:
            L.append(f"| {d} | – | error: {r['error']} | | | | | | no | |")
            continue
        dec = ", ".join(f"p{p['page_index']} {p['decision']}" for p in r["pages"])
        L.append(f"| {d} | {len(r['pages'])} | {pct(r['values_printed'])} | {pct(r['labels'])} | {pct(r['paths'])} | "
                 f"{'ok' if r['unit'] == 1.0 else 'wrong'} | {pct(r['periods_columns'])} | {pct(r['scaled_values'])} | "
                 f"{'yes' if r['page_correct'] else 'no'} | {dec} |")
    L += ["", "## Failures (first differences per filing)", ""]
    for d, r in res["filings"].items():
        if r.get("scored") and not r.get("page_correct"):
            L.append(f"### {d}")
            for p in r.get("pages", []):
                if p["reasons"]:
                    L.append(f"- page {p['page_index']} ({p['status']}, {p['route']}): {'; '.join(p['reasons'])[:400]}")
            for x in r.get("first_differences", [])[:10]:
                L.append(f"- {json.dumps(x, ensure_ascii=False)[:300]}")
            L.append("")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
