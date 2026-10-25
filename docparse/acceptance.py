"""Acceptance rule: decide whether a parsed page can be used without human review.

ACCEPT only if all of these hold; anything else goes to REVIEW (with every reason listed):
  A1  page status is "ok" (not "failed" / "no_table");
  A2  zero failed identity checks (template, dynamic and solver alike);
  A3  zero not-evaluable template checks among those whose sections are all on the page
      (sections are recognised from header/total labels, see registry.SECTION_MARKERS);
  A4  coverage is not n/a (statement type recognised and a check template exists);
  A5  no calc_parent links made by the solver fallback (allow_solver=False by default): solver links
      are chosen by the same arithmetic the checks test, so passing them is not evidence.
  A6  the unit was read from the page (a unit line such as （単位：千円）). Identity checks are
      invariant to the unit, so a missing or wrong unit can't be caught by A2.
  A7  every value column maps to a period end date parsed from its own header; the dates are
      distinct and run in one direction across the columns, and a 前… column is earlier than a 当…
      column. Identity checks are invariant to swapping whole columns.
  A8  VLM route only: an independent second reading (Tesseract on the same page) confirms the
      unit, the column dates in order, the column assignment of the values, and the indentation
      (levels taken from the page geometry for every row). Anything unconfirmed -> review.

A6-A8 were added on 2026-10-25 (amendment in stress/FREEZE_LOG.txt) after the VLM smoke page and
fake-runner cases showed that A1-A5 accept pages with swapped columns, a wrong unit, or a wrong
hierarchy whenever the values themselves are consistent.

Notes: cross-page checks (e.g. 資産合計 = 負債純資産合計) are only required when every section they
need is on the page. is_corrected = None (scans) does not block acceptance but is reported.
"""
from __future__ import annotations

from .registry import sections_present

AMENDMENT = "A6-A8 (2026-10-25)"


def period_order_problem(periods: list[dict]) -> str | None:
    """None if every column has a parsed date and the dates are distinct, monotonic and consistent
    with 前/当 labels; else a short reason."""
    if not periods:
        return "no value columns"
    dates = [p.get("date") for p in periods]
    if any(d is None for d in dates):
        return f"{sum(d is None for d in dates)} column(s) without a parsed period-end date"
    if len(set(dates)) != len(dates):
        return "two columns have the same period-end date"
    inc = all(a < b for a, b in zip(dates, dates[1:]))
    dec = all(a > b for a, b in zip(dates, dates[1:]))
    if not (inc or dec):
        return "column dates are not in one direction"
    lab = {p.get("id"): p.get("date") for p in periods}
    if "prior" in lab and "current" in lab and not lab["prior"] < lab["current"]:
        return "前 column is not earlier than 当 column"
    return None


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
    meta = doc.get("metadata", {})
    if doc.get("status") == "ok" and not meta.get("unit_ja"):
        reasons.append("A6 no unit line read from the page")
    if doc.get("status") == "ok":
        why = period_order_problem(doc.get("periods", []))
        if why:
            reasons.append(f"A7 {why}")
    route = meta.get("route", {})
    if doc.get("status") == "ok" and route.get("backend") == "vlm":
        cc = (route.get("backend_diagnostics") or {}).get("cross_check") or {}
        if not cc.get("ok"):
            reasons.append("A8 VLM read not confirmed by the second reading: "
                           + ("; ".join(cc.get("problems", [])) or "no cross-check recorded")[:300])
    unknown_corr = sum(1 for r in doc.get("rows", []) for c in r["cells"].values() if c.get("is_corrected") is None)
    return {"decision": "accept" if not reasons else "review", "reasons": reasons, "rule": f"A1-A5 + {AMENDMENT}",
            "sections_present": sorted(present), "required_template_checks": len(required),
            "cells_with_unknown_correction_status": unknown_corr}
