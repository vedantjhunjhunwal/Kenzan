"""Check a pilot (or full) fetch run before trusting it.

For every filing:
  1a  how the balance sheet was found in the inline XBRL (heading / fact_fallback)
  1b  bs_pages.pdf holds exactly the balance-sheet pages:
        first page shows the 【title】 heading, last page shows a liabilities+equity total label,
        no page in between shows another statement's 【heading】, and every page carries at least
        one ground-truth value (a page with none is an extra page); missing pages show up as
        unaligned rows
  1c  XBRL facts vs printed numbers: |fact| == printed x 10^scale for every tagged cell, and the
      row alignment against the PDF (build_gt_xbrl.align)
  3   cells per XBRL convention (dash tagged zero / nil / untagged; contra positive / negative)
Run level:
  2   exclusion and error reasons from candidates_log.csv
  4   pilot companies in forced_dev.txt -> split 'dev'

usage: python bench/check_pilot.py <run_dir or run.zip> [<report.md>]
"""
from __future__ import annotations

import csv
import json
import re
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_gt_xbrl as gtb  # noqa: E402
import split as sp  # noqa: E402

TITLES = ("連結貸借対照表", "連結財政状態計算書")
OTHER_STATEMENTS = ("損益計算書", "包括利益計算書", "株主資本等変動計算書", "持分変動計算書", "キャッシュ・フロー計算書",
                    "純損益", "財政状態計算書", "貸借対照表")
END_LABELS = ("負債純資産合計", "負債及び純資産合計", "負債・純資産合計", "負債純資産の部合計", "負債及び資本合計",
              "資本及び負債合計", "負債及び資本の合計", "資本合計及び負債合計", "負債・資本合計")


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def page_checks(pdf_path: Path, doc_gt: dict) -> dict:
    pdf = pymupdf.open(pdf_path)
    texts = [gtb.nfkc_nospace(p.get_text()) for p in pdf]
    headings = [re.findall(r"【([^】]{2,40})】", t) for t in texts]
    first_ok = any(any(x in h for x in TITLES) for h in headings[0]) if texts else False
    last_ok = any(l in texts[-1] for l in END_LABELS) if texts else False
    inner_other = [i for i in range(1, len(texts) - 1)
                   if any(any(o in h for o in OTHER_STATEMENTS) and not any(x in h for x in TITLES) for h in headings[i])]
    last_other = any(any(o in h for o in OTHER_STATEMENTS) and not any(x in h for x in TITLES) for h in headings[-1])
    # every page must carry ground-truth values
    vals = [gtb._norm_val(c["raw_text"]) for r in doc_gt["rows"] for c in r["cells"].values()]
    vals = [v for v in vals if v != "－" and len(v.lstrip("△")) >= 3]
    per_page = []
    for i, page in enumerate(pdf):
        one = pymupdf.open()
        one.insert_pdf(pdf, from_page=i, to_page=i)
        toks = set(gtb.pdf_value_tokens(one))
        per_page.append(sum(1 for v in vals if v in toks))
    return {"pages": len(texts), "first_page_has_title": first_ok, "last_page_has_total_label": last_ok,
            "other_statement_heading_inside": inner_other, "next_statement_starts_on_last_page": last_other,
            "gt_values_per_page": per_page, "extra_pages": [i for i, n in enumerate(per_page) if n == 0]}


def check(run: str, report: str | None = None) -> dict:
    tmp = None
    root = Path(run)
    if root.suffix == ".zip":
        tmp = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(root) as z:
            z.extractall(tmp.name)
        root = next(p.parent for p in Path(tmp.name).rglob("manifest.csv"))
    manifest = _rows(root / "manifest.csv")
    log = _rows(root / "candidates_log.csv")
    out = {"filings": [], "run": {}}
    conv_total = Counter()
    problems = []
    for m in manifest:
        fdir = root / "filings" / m["docID"]
        rec = {"docID": m["docID"], "edinetCode": m["edinetCode"], "filer": m.get("filerName"), "pages": m.get("pages"),
               "standard": m["standard"], "unit": m["unit"], "pages_manifest": m["pages"],
               "found_by": m.get("bs_found_by"), "end_total_check": m.get("end_total_value_on_last_page")}
        try:
            doc, stats = gtb.build_filing(fdir)
            has_pdf = (fdir / "bs_pages.pdf").exists()
            if has_pdf:
                al = gtb.align(doc, fdir / "bs_pages.pdf")
                pc = page_checks(fdir / "bs_pages.pdf", doc)
            else:                                        # local mode before the PDF arrives
                al = {"status": "pdf_missing", "match_rate": None, "unaligned": []}
                pc = {"pages": None, "first_page_has_title": None, "last_page_has_total_label": None,
                      "other_statement_heading_inside": [], "next_statement_starts_on_last_page": None,
                      "gt_values_per_page": [], "extra_pages": []}
            convs = {k[5:]: v for k, v in stats.items() if k.startswith("conv:")}
            conv_total.update(convs)
            rec |= {"rows": stats["rows"], "value_cells": stats["cells"], "untagged_cells": stats["untagged_cells"],
                    "fact_vs_printed_mismatch": stats["xbrl_value_mismatch"],
                    "alignment": al["status"], "match_rate": al["match_rate"],
                    "unaligned": al.get("unaligned", [])[:5], "gt_checks_failed": stats["gt_checks_failed"],
                    "gt_checks_evaluable": stats["gt_checks_evaluable"], "cal_role": stats["cal_role"],
                    "calc_parent_from_cal": stats["calc_parent_from_cal"],
                    "calc_parent_ancestor": stats["calc_parent_ancestor"],
                    "presentation_levels": stats["presentation_levels"], "conventions": convs, **pc}
            issues = []
            if rec["found_by"] != "heading":
                issues.append(f"found by {rec['found_by']}")
            if not has_pdf:
                issues.append("PDF missing: page checks and alignment pending")
            elif not pc["first_page_has_title"]:
                issues.append("first page lacks the 【title】")
            if has_pdf and not pc["last_page_has_total_label"]:
                issues.append("last page lacks the total label")
            if pc["other_statement_heading_inside"]:
                issues.append(f"other statement inside on pages {pc['other_statement_heading_inside']}")
            if pc["extra_pages"]:
                issues.append(f"pages without balance-sheet values: {pc['extra_pages']}")
            if stats["xbrl_value_mismatch"]:
                issues.append(f"{stats['xbrl_value_mismatch']} facts differ from the printed number")
            if has_pdf and al["status"] != "kept":
                issues.append(f"alignment {al['status']} ({al['match_rate']})")
            if stats["gt_checks_failed"]:
                issues.append(f"{stats['gt_checks_failed']} identity checks fail on the ground truth")
            if stats["presentation_levels"] < 3:
                issues.append(f"only {stats['presentation_levels']} indent levels found")
            rec["issues"] = issues
        except Exception as e:
            rec["issues"] = [f"build error {type(e).__name__}: {e}"[:300]]
        problems += [f"{m['docID']}: {i}" for i in rec["issues"]]
        out["filings"].append(rec)

    forced_run = root / "forced_dev.txt"
    forced = sp.load_forced_dev(forced_run) if forced_run.exists() else set()
    pilot_codes = {m["edinetCode"].upper() for m in manifest if m.get("pilot") in ("True", "true", "1")}
    out["run"] = {
        "processed": len(log), "accepted": len(manifest),
        "decisions": dict(Counter(r["decision"] for r in log)),
        "reasons": dict(Counter(f"{r['decision']}: {r['reason']}" for r in log).most_common()),
        "errors": [{"docID": r["docID"], "reason": r["reason"]} for r in log if r["decision"] == "error"],
        "conventions_total": dict(conv_total),
        "forced_dev_in_run": sorted(forced), "pilot_codes_in_manifest": sorted(pilot_codes),
        "pilot_all_in_forced_dev": pilot_codes <= forced,
        "pilot_split_with_forced_list": {c: sp.split_of(c, forced) for c in sorted(pilot_codes)},
        "pilot_split_by_hash_alone": {c: sp.hash_split(c) for c in sorted(pilot_codes)},
        "problems": problems,
    }
    if report:
        Path(report).write_text(to_markdown(out), encoding="utf-8")
    if tmp:
        tmp.cleanup()
    return out


def to_markdown(o: dict) -> str:
    r = o["run"]
    d = r["decisions"]
    L = ["# Pilot check", "", f"Processed {r['processed']} filings: {d.get('accepted', 0)} accepted, "
         f"{d.get('xbrl_only', 0)} XBRL-only (PDF pending), {d.get('excluded', 0)} excluded, "
         f"{d.get('error', 0)} errors.", "",
         "## Per filing", "",
         "| docID | std | unit | pages | found by | first/last page ok | extra pages | facts ≠ printed | "
         "alignment | GT checks failed / evaluable | indent levels | issues |",
         "|---|---|---|--:|---|---|---|--:|---|---|--:|---|"]
    for f in o["filings"]:
        L.append(f"| {f['docID']} | {f['standard']} | {f['unit']} | {f.get('pages', '?')} | {f['found_by']} "
                 f"| {f.get('first_page_has_title')}/{f.get('last_page_has_total_label')} | {f.get('extra_pages')} "
                 f"| {f.get('fact_vs_printed_mismatch')} | {f.get('alignment')} {f.get('match_rate')} "
                 f"| {f.get('gt_checks_failed')}/{f.get('gt_checks_evaluable')} | {f.get('presentation_levels')} "
                 f"| {'; '.join(f['issues']) or 'none'} |")
    L += ["", "## Candidate outcomes", ""] + [f"- {k}: {v}" for k, v in r["reasons"].items()]
    L += ["", "## XBRL conventions (cells)", ""] + [f"- {k}: {v}" for k, v in sorted(r["conventions_total"].items())]
    L += ["", "## Pilot → dev", "", f"- all pilot companies in forced_dev.txt: {r['pilot_all_in_forced_dev']}",
          f"- split with the forced list: {r['pilot_split_with_forced_list']}",
          f"- split by hash alone (would have been): {r['pilot_split_by_hash_alone']}"]
    L += ["", "## Problems", ""] + ([f"- {p}" for p in r["problems"]] or ["- none"])
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    res = check(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps(res["run"], ensure_ascii=False, indent=1))
