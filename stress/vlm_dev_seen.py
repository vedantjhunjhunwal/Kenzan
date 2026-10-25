"""DEV measurement of the VLM-route acceptance rule with a FAKE VLM, on the SEEN scans (dev data).

A fake VLM returns the ground-truth table (a perfect read), or the ground truth with ONE injected
error of every type in gt/detection_power.py. Each table goes through the real pipeline,
including the Tesseract second reading of the actual scanned page, and the acceptance rule.
Reports:
  - the accept ceiling: perfect reads accepted;
  - the share of single injected errors accepted, by error type.
No held-out page is touched. No model is called.

usage: python stress/vlm_dev_seen.py <seen_pdf_dir> <out_dir> [--max-cases N]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stress"))
import pymupdf  # noqa: E402

from docparse import vlm  # noqa: E402
from docparse.normalize import parse_value  # noqa: E402
from docparse.pipeline import parse_page  # noqa: E402
from run_stress import code_fingerprint  # noqa: E402

GT = json.loads((ROOT / "gt/out/ground_truth.json").read_text(encoding="utf-8"))
PERIODS = ("prior", "current")


def gt_table() -> dict:
    rows = [{"label": r["label_ja"], "indent": r["presentation_level"],
             "values": [r["cells"][p]["raw_text"] if p in r["cells"] else None for p in PERIODS]} for r in GT["rows"]]
    return {"heading_lines": ["- 4 -", "（訂正後）", "３．連結財務諸表及び主な注記", "（１）連結貸借対照表", "（単位：千円）"],
            "columns": ["前連結会計年度(2025年３月31日)", "当連結会計年度(2026年３月31日)"], "rows": rows}


def fmt(mag: int, neg: bool) -> str:
    return ("△" if neg else "") + f"{mag:,}"


def injected_tables():
    """(category, table) with exactly one error, mirroring gt/detection_power.py's error types."""
    base = gt_table()
    for i, r in enumerate(base["rows"]):
        for k, raw in enumerate(r["values"]):
            pv = parse_value(raw) if raw else None
            if pv is None or pv.is_nil or pv.reported_value is None:
                continue
            mag, neg = abs(pv.reported_value), pv.is_negative
            s = str(mag)
            for pos in range(len(s)):
                j = len(s) - 1 - pos
                for d in "0123456789":
                    if d == s[j] or (j == 0 and d == "0" and len(s) > 1):
                        continue
                    t = copy.deepcopy(base)
                    t["rows"][i]["values"][k] = fmt(int(s[:j] + d + s[j + 1:]), neg)
                    yield ("digit:ones" if pos == 0 else "digit:tens" if pos == 1 else "digit:hundreds+"), t
            t = copy.deepcopy(base)
            if neg:
                t["rows"][i]["values"][k] = fmt(mag, False)
                yield "sign:dropped_△", t
            elif mag > 0:
                t["rows"][i]["values"][k] = fmt(mag, True)
                yield "sign:spurious_△", t
    for i, (r, g) in enumerate(zip(base["rows"], GT["rows"])):
        if g["calc_parent"] and g["cells"]:
            t = copy.deepcopy(base)
            del t["rows"][i]
            yield "row:dropped", t
            if all(v for v in r["values"]) and r["values"][0] != r["values"][1]:
                t = copy.deepcopy(base)
                t["rows"][i]["values"] = r["values"][::-1]
                yield "row:columns_swapped", t


_IMG: dict[str, tuple[str, str]] = {}      # pdf path -> (how, image sha256), computed once per page


class TableBackend(vlm.VLMBackend):
    """VLMBackend whose 'response' is a given table (no cache files, no model). Uses the same
    content_from_response as the real backend; only the image hashing is done once per page."""

    def __init__(self, table: dict):
        super().__init__(cache_dir="/nonexistent", model="fake/gt")
        self.table = table

    def read(self, page):
        import hashlib
        key = page.parent.name
        if key not in _IMG:
            img, how = vlm.page_image(page)
            _IMG[key] = (how, hashlib.sha256(img).hexdigest())
        how, sha = _IMG[key]
        return self.content_from_response(page, json.dumps(self.table, ensure_ascii=False), how,
                                          {"cache": "fake", "image_sha256": sha})


def decide(page, table) -> dict:
    d = parse_page(page, force_route="visual", ocr=TableBackend(table))
    return d["acceptance"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--max-cases", type=int, default=0, help="0 = all injected cases")
    ap.add_argument("--disable", default="", help="comma-separated rule codes to ablate, e.g. A9 (dev only)")
    ap.add_argument("--a9-compare", default="magnitude", choices=["magnitude", "signed"])
    ap.add_argument("--perfect-only", action="store_true")
    a = ap.parse_args(argv)
    from docparse import acceptance
    acceptance.DISABLED = {x for x in a.disable.split(",") if x}
    vlm.A9_COMPARE = a.a9_compare
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(Path(a.pdf_dir).glob("*.pdf"))
    cases = [] if a.perfect_only else list(injected_tables())
    if a.max_cases:
        cases = cases[:a.max_cases]
    res = {"fingerprint": code_fingerprint(), "pages": {}, "n_cases": len(cases),
           "disabled": sorted(acceptance.DISABLED), "a9_compare": vlm.A9_COMPARE}
    t0 = time.time()
    for pdf in pdfs:
        page = pymupdf.open(pdf)[0]
        perfect = decide(page, gt_table())
        by_cat = defaultdict(lambda: [0, 0])
        reasons = defaultdict(int)
        for cat, t in cases:
            acc = decide(page, t)
            by_cat[cat][1] += 1
            by_cat[cat][0] += acc["decision"] == "accept"
            for r in acc["reasons"]:
                reasons[r.split()[0]] += 1
        res["pages"][pdf.stem] = {"perfect": perfect["decision"], "perfect_reasons": perfect["reasons"],
                                  "injected_accepted": {c: v for c, v in sorted(by_cat.items())},
                                  "injected_reason_counts": dict(reasons)}
        print(f"{pdf.stem:12} perfect={perfect['decision']:6} injected accepted="
              f"{sum(v[0] for v in by_cat.values())}/{sum(v[1] for v in by_cat.values())}  "
              f"{time.time() - t0:.0f}s  {perfect['reasons'][:1]}", flush=True)
    res["summary"] = summarize(res)
    (out / "dev_seen.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))
    return 0


def summarize(res: dict) -> dict:
    pages = res["pages"]
    acc_pages = [p for p, v in pages.items() if v["perfect"] == "accept"]
    cats = defaultdict(lambda: [0, 0])
    cats_on_acc = defaultdict(lambda: [0, 0])
    for p, v in pages.items():
        for c, (k, n) in v["injected_accepted"].items():
            cats[c][0] += k
            cats[c][1] += n
            if p in acc_pages:
                cats_on_acc[c][0] += k
                cats_on_acc[c][1] += n
    tot = [sum(x[0] for x in cats.values()), sum(x[1] for x in cats.values())]
    tot_acc = [sum(x[0] for x in cats_on_acc.values()), sum(x[1] for x in cats_on_acc.values())]
    return {"perfect_read_accepted": f"{len(acc_pages)}/{len(pages)}", "perfect_read_rejected": {
        p: v["perfect_reasons"] for p, v in pages.items() if v["perfect"] != "accept"},
        "injected_accepted_all_pages": {c: f"{k}/{n}" for c, (k, n) in sorted(cats.items())},
        "injected_accepted_total": f"{tot[0]}/{tot[1]}",
        "injected_accepted_total_on_accepted_pages": f"{tot_acc[0]}/{tot_acc[1]}",
        "injected_accepted_on_pages_where_perfect_read_is_accepted": {
            c: f"{k}/{n}" for c, (k, n) in sorted(cats_on_acc.items())}}


if __name__ == "__main__":
    sys.exit(main())
