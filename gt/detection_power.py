"""How many realistic extraction errors does each tolerance window catch on this page?

Injects one error at a time into the ground truth and reports whether any evaluable identity
check fails. Also reports each window's false-alarm rate on the clean page.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docparse.validate import Cell, check_sum  # noqa: E402

gt = json.load(open(sys.argv[1], encoding="utf-8"))
rows = gt["rows"]
PERIODS = ("prior", "current")
kids_of = defaultdict(list)
for r in rows:
    if r["calc_parent"]:
        kids_of[r["calc_parent"]].append(r["row_id"])
totals = [r["row_id"] for r in rows if r["row_type"] in ("subtotal", "total")]


def base_cells():
    out = {}
    for r in rows:
        for p, c in r["cells"].items():
            out[(r["row_id"], p)] = Cell(c["reported_value"], c["is_negative"], c["is_nil"])
    return out


def failing(cells, mode, kids=kids_of):
    bad = set()
    for t in totals:
        for p in PERIODS:
            ks = kids[t]
            res = check_sum(t, p, t, cells.get((t, p)), ks, [cells.get((k, p)) for k in ks], mode)
            if res.status == "fail":
                bad.add((t, p))
    return bad


def with_value(cells, key, mag, neg):
    c = dict(cells)
    c[key] = Cell(-mag if neg else mag, neg, False)
    return c


def errors():
    """Yield (category, cells_after_error, kids_map)."""
    cells = base_cells()
    for key, c in cells.items():
        if c.is_nil:
            continue
        mag, neg = abs(c.reported), c.is_negative
        s = str(mag)
        for pos in range(len(s)):           # pos 0 = ones digit
            i = len(s) - 1 - pos
            for d in "0123456789":
                if d == s[i] or (i == 0 and d == "0" and len(s) > 1):
                    continue
                new = int(s[:i] + d + s[i + 1:])
                cat = "digit:ones" if pos == 0 else "digit:tens" if pos == 1 else "digit:hundreds+"
                yield cat, with_value(cells, key, new, neg), kids_of
        if neg:
            yield "sign:dropped_△", with_value(cells, key, mag, False), kids_of
        elif mag > 0:
            yield "sign:spurious_△", with_value(cells, key, mag, True), kids_of
    # whole-row errors
    for r in rows:
        if r["calc_parent"] and r["cells"]:
            k2 = {t: [k for k in ks if k != r["row_id"]] for t, ks in kids_of.items()}
            yield "row:dropped", cells, k2
            if "prior" in r["cells"] and "current" in r["cells"]:
                c = dict(cells)
                c[(r["row_id"], "prior")], c[(r["row_id"], "current")] = \
                    cells[(r["row_id"], "current")], cells[(r["row_id"], "prior")]
                if c != cells:
                    yield "row:columns_swapped", c, kids_of


modes = ("exact", "symmetric", "truncate")
clean = base_cells()
n_eval = len(totals) * len(PERIODS)
print("False alarms on the clean page (evaluable checks failing):")
for m in modes:
    print(f"  {m:10} {len(failing(clean, m))}/{n_eval}")

stats = {m: defaultdict(lambda: [0, 0]) for m in modes[1:]}
for cat, cells, kids in errors():
    for m in modes[1:]:
        s = stats[m][cat]
        s[1] += 1
        s[0] += bool(failing(cells, m, kids))

cats = sorted(stats["truncate"])
print(f"\nDetection rate by injected error (one error at a time; {sum(stats['truncate'][c][1] for c in cats)} cases)")
print(f"  {'error type':22} {'cases':>6} {'symmetric ±(n-1)':>18} {'sign-aware':>12}")
for c in cats:
    a, n = stats["symmetric"][c]
    b, _ = stats["truncate"][c]
    print(f"  {c:22} {n:6} {a / n:17.1%} {b / n:12.1%}")
