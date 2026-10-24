"""Acceptance rule: decide whether a parsed page can be used without human review.

ACCEPT only if all of these hold; anything else goes to REVIEW (with every reason listed):
  A1  page status is "ok" (not "failed" / "no_table");
  A2  zero failed identity checks (template, dynamic and solver alike);
  A3  zero not-evaluable template checks among those whose sections are all on the page
      (sections are recognised from header/total labels, see registry.SECTION_MARKERS);
  A4  coverage is not n/a (statement type recognised and a check template exists);
  A5  no calc_parent links made by the solver fallback (allow_solver=False by default): solver links
      are chosen by the same arithmetic the checks test, so passing them is not evidence.

Notes: cross-page checks (e.g. 資産合計 = 負債純資産合計) are only required when every section they
need is on the page. is_corrected = None (scans) does not block acceptance but is reported.
"""
from __future__ import annotations

from .registry import sections_present


def decide(doc: dict, allow_solver: bool = False) -> dict:
    reasons: list[str] = []
    if doc.get("status") != "ok":
        reasons.append(f"A1 page status {doc.get('status')}: {doc.get('reason')}")
    checks = doc.get("checks", [])
    failed = [c for c in checks if c["status"] == "fail"]
    if failed:
        reasons.append(f"A2 {len(failed)} failed check(s): "
                       + ", ".join(sorted({f"{c['check_id']}@{c['period']}" for c in failed}))[:300])
    present = sections_present([r["label_norm"] for r in doc.get("rows", [])])
    required = [c for c in checks if c.get("is_template") and c.get("sections")
                and set(c["sections"]) <= present]
    missing = [c for c in required if c["status"] == "not_evaluable"]
    if missing:
        reasons.append(f"A3 {len(missing)} required template check(s) not evaluable: "
                       + ", ".join(sorted({c["check_id"] for c in missing})))
    if doc.get("status") == "ok" and not present:
        reasons.append("A3 no statement section recognised on the page")
    if doc.get("check_summary", {}).get("coverage") is None:
        reasons.append("A4 coverage n/a (statement type not recognised or no template)")
    solver = [r["row_id"] for r in doc.get("rows", []) if r.get("calc_parent_source") == "solver"]
    if solver and not allow_solver:
        reasons.append(f"A5 {len(solver)} calc_parent link(s) from the solver fallback")
    unknown_corr = sum(1 for r in doc.get("rows", []) for c in r["cells"].values() if c.get("is_corrected") is None)
    return {"decision": "accept" if not reasons else "review", "reasons": reasons,
            "sections_present": sorted(present), "required_template_checks": len(required),
            "cells_with_unknown_correction_status": unknown_corr}
