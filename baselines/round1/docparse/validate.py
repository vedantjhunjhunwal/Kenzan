"""Accounting-identity checks with rounding-aware tolerance windows.

Why tolerance at all: each printed figure is the true yen amount truncated to the display unit
(千円未満切捨て) independently, so printed children need not add up to the printed total.

Sign-aware truncation window (derivation)
-----------------------------------------
Let x_i be a child's true amount in display units and r_i = trunc(x_i) its printed value
(magnitude floored, sign kept: △ items are truncated toward zero). Per-child error
e_i = x_i - r_i lies in [0, 1) for a non-negative child and (-1, 0] for a △ child.
With T = sum(x_i), R = trunc(T) the printed total and E = T - R,

    diff := R - sum(r_i) = sum(e_i) - E

With p non-negative children and q △ children (nil cells contribute exactly 0, counted in neither):
    sum(e_i) in (-q, p)   (endpoints: 0 is attainable on a side with no children)
    total printed without △  : E in [0, 1)   -> diff in [-q, max(p-1, 0)]
    total printed with △     : E in (-1, 0]  -> diff in [-max(q-1, 0), p]
Both bounds are attainable, so the window is tight. It holds p+q integers versus 2(p+q)-1 for the
symmetric +-(n-1) window.

A printed "0" is a non-negative child (true value in [0, 1)); "△0" is a negative one.
The window is only valid under truncation. Under round-half-up each e_i is in [-0.5, 0.5) and the
window becomes roughly +-(n+1)/2; using the truncation window on a rounded filing would raise
false alarms, so the convention is a per-document parameter.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Cell:
    reported: int | None          # printed value in display units; None for nil/blank
    is_negative: bool = False     # True for △/▲ (keeps the sign of "△0")
    is_nil: bool = False          # printed dash: an explicit nothing, treated as exact 0

    @property
    def amount(self) -> int:
        return 0 if self.reported is None else self.reported


def window(p: int, q: int, total_negative: bool, mode: str = "truncate") -> tuple[int, int]:
    n = p + q
    if mode == "exact":
        return (0, 0)
    if mode == "symmetric":
        return (-max(n - 1, 0), max(n - 1, 0))
    if mode == "truncate":
        if total_negative:
            return (-max(q - 1, 0), p)
        return (-q, max(p - 1, 0))
    if mode == "round":
        lo = math.floor(-(n + 1) / 2) + 1
        hi = math.ceil((n + 1) / 2) - 1
        return (lo, hi)
    raise ValueError(mode)


@dataclass
class CheckResult:
    check_id: str
    period: str
    kind: str                       # "sum" | "equality"
    total_row: str | None
    child_rows: list[str]
    printed: int | None
    computed: int | None
    diff: int | None
    window: tuple[int, int] | None
    status: str                     # exact | within_tolerance | fail | not_evaluable
    reason: str = ""
    p: int = 0
    q: int = 0


def check_sum(check_id: str, period: str, total_row: str, total: Cell | None,
              child_rows: list[str], children: list[Cell | None], mode: str = "truncate") -> CheckResult:
    """Children must sum to total within the rounding window. Missing cells -> not_evaluable."""
    if total is None or any(c is None for c in children):
        missing = [total_row] if total is None else []
        missing += [r for r, c in zip(child_rows, children) if c is None]
        return CheckResult(check_id, period, "sum", total_row, child_rows, None, None, None, None,
                           "not_evaluable", f"rows not present: {missing}")
    kids = [c for c in children if not c.is_nil]
    p = sum(1 for c in kids if not c.is_negative)
    q = sum(1 for c in kids if c.is_negative)
    computed = sum(c.amount for c in kids)
    diff = total.amount - computed
    lo, hi = window(p, q, total.is_negative, mode)
    status = "exact" if diff == 0 else ("within_tolerance" if lo <= diff <= hi else "fail")
    return CheckResult(check_id, period, "sum", total_row, child_rows, total.amount, computed, diff,
                       (lo, hi), status, p=p, q=q)


def check_equal(check_id: str, period: str, a_row: str, a: Cell | None,
                b_row: str, b: Cell | None) -> CheckResult:
    """Two printed figures of the same true amount (e.g. 資産合計 = 負債純資産合計): tolerance 0."""
    if a is None or b is None:
        missing = [r for r, c in ((a_row, a), (b_row, b)) if c is None]
        return CheckResult(check_id, period, "equality", a_row, [b_row], None, None, None, None,
                           "not_evaluable", f"rows not present: {missing}")
    diff = a.amount - b.amount
    return CheckResult(check_id, period, "equality", a_row, [b_row], a.amount, b.amount, diff, (0, 0),
                       "exact" if diff == 0 else "fail")


# ---------------------------------------------------------------------------------------------
# Statement-level validation driven by the check registry
# ---------------------------------------------------------------------------------------------
def _cell(row: dict | None, period_id: str) -> Cell | None:
    if row is None:
        return None
    c = row.get("cells", {}).get(period_id)
    if c is None:
        return None
    return Cell(c["reported_value"], bool(c["is_negative"]), bool(c["is_nil"]))


def run_checks(rows: list[dict], periods: list[dict], statement_type: str,
               mode: str = "truncate") -> tuple[list[dict], dict]:
    """Run template + dynamic checks for every period. Returns (checks, summary).

    category: "template" (from the registry; defines coverage), "dynamic" (any other row with
    parsed children), "solver" (any child linked by the solver fallback -> scored separately,
    because the same arithmetic was used to infer the structure).
    """
    from .registry import TEMPLATES, concept_of

    by_id = {r["row_id"]: r for r in rows}
    by_concept: dict[str, dict] = {}
    for r in rows:
        c = concept_of(r["label_norm"])
        if c and c not in by_concept:
            by_concept[c] = r
    kids: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("calc_parent") and r.get("row_type") != "memo":
            kids.setdefault(r["calc_parent"], []).append(r)

    def category(child_rows: list[dict], default: str) -> str:
        return "solver" if any(k.get("calc_parent_source") == "solver" for k in child_rows) else default

    out: list[dict] = []
    covered_totals: set[str] = set()
    for tc in TEMPLATES.get(statement_type, ()):
        total = by_concept.get(tc.total)
        for p in periods:
            pid, pend = p["id"], p["date"]
            cid = f"{tc.check_id}:{tc.total}"
            if tc.kind == "equal":
                other = by_concept.get(tc.children[0])
                res = check_equal(cid, pend, total["row_id"] if total else tc.total, _cell(total, pid),
                                  other["row_id"] if other else tc.children[0], _cell(other, pid))
                cat = "template"
            elif tc.kind == "sum_named":
                ch = [by_concept.get(c) for c in tc.children]
                ch += [by_concept[c] for c in tc.optional_children if c in by_concept]
                ids = [r["row_id"] if r else c for r, c in zip(ch, tc.children + tc.optional_children)]
                res = check_sum(cid, pend, total["row_id"] if total else tc.total, _cell(total, pid),
                                ids, [_cell(r, pid) for r in ch], mode)
                cat = category([r for r in ch if r], "template")
            else:  # sum_parsed
                ch = kids.get(total["row_id"], []) if total else []
                if total is not None and not ch:
                    res = CheckResult(cid, pend, "sum", total["row_id"], [], None, None, None, None,
                                      "not_evaluable", "total present but no children parsed")
                else:
                    res = check_sum(cid, pend, total["row_id"] if total else tc.total, _cell(total, pid),
                                    [r["row_id"] for r in ch], [_cell(r, pid) for r in ch], mode)
                cat = category(ch, "template")
            if total is not None:
                covered_totals.add(total["row_id"])
            out.append(res.__dict__ | {"category": cat, "is_template": True, "period_id": pid})

    for tid, ch in kids.items():
        if tid in covered_totals or tid not in by_id:
            continue
        t = by_id[tid]
        for p in periods:
            res = check_sum(f"D:{t.get('label_path', t['label_norm'])}", p["date"], tid,
                            _cell(t, p["id"]), [r["row_id"] for r in ch], [_cell(r, p["id"]) for r in ch], mode)
            out.append(res.__dict__ | {"category": category(ch, "dynamic"), "is_template": False,
                                        "period_id": p["id"]})
    return out, summarize(out)


def summarize(checks: list[dict]) -> dict:
    def stats(cs):
        ev = [c for c in cs if c["status"] != "not_evaluable"]
        passed = [c for c in ev if c["status"] in ("exact", "within_tolerance")]
        return {"checks": len(cs), "evaluable": len(ev), "passed": len(passed),
                "exact": sum(c["status"] == "exact" for c in ev),
                "within_tolerance": sum(c["status"] == "within_tolerance" for c in ev),
                "failed": sum(c["status"] == "fail" for c in ev),
                "consistency": (len(passed) / len(ev)) if ev else None}
    s = {cat: stats([c for c in checks if c["category"] == cat]) for cat in ("template", "dynamic", "solver")}
    tpl = stats([c for c in checks if c.get("is_template")])
    s["coverage"] = (tpl["evaluable"] / tpl["checks"]) if tpl["checks"] else None
    s["template_expected"], s["template_evaluable"] = tpl["checks"], tpl["evaluable"]
    rule_based = [c for c in checks if c["category"] in ("template", "dynamic")]
    s["consistency_rule_based"] = stats(rule_based)["consistency"]
    return s
