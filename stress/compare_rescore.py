"""Compare a frozen held-out run with its re-score under a later acceptance rule.

Cell F1 (and every other extraction metric) must be identical: only the acceptance rule changed.
Any difference is reported as a BUG and the script exits 1. Writes <new_dir>/rescore_vs_original.md.

usage: python stress/compare_rescore.py <original_dir> <rescore_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SAME = ("f1", "precision", "recall", "cells_correct", "calc_parent", "pres_parent", "level", "row_type",
        "status", "checks_failed", "coverage")


def main(orig: str, new: str) -> int:
    a = json.loads(Path(orig, "results.json").read_text(encoding="utf-8"))
    b = json.loads(Path(new, "results.json").read_text(encoding="utf-8"))
    ra, rb = {r["variant"]: r for r in a["results"]}, {r["variant"]: r for r in b["results"]}
    bugs = []
    lines = [f"# Re-score of `{orig}` under the final acceptance rule", "",
             f"Original pipeline fingerprint `{a['code_fingerprint']}`, re-score `{b['code_fingerprint']}`; "
             f"spec sha256 `{a['spec_sha256']}` / `{b['spec_sha256']}`.", "",
             "| Variant | Cell F1 (orig = new) | Fully correct | Old decision | Old reasons | New decision | New reasons |",
             "|---|--:|---|---|---|---|---|"]
    for v in ra:
        x, y = ra[v], rb.get(v)
        if y is None:
            bugs.append(f"{v}: missing in re-score")
            continue
        diff = [k for k in SAME if x.get(k) != y.get(k)]
        if diff:
            bugs.append(f"{v}: {', '.join(f'{k} {x.get(k)} -> {y.get(k)}' for k in diff)}")
        codes = lambda r: ", ".join(sorted({s.split()[0] for s in r["acceptance_reasons"]})) or "–"
        lines.append(f"| {v} | {100 * x['f1']:.1f}%{'' if x['f1'] == y['f1'] else ' **≠ ' + str(round(100 * y['f1'], 1)) + '%**'} "
                     f"| {'yes' if y['fully_correct'] else 'no'} | {x['decision']} | {codes(x)} | {y['decision']} | {codes(y)} |")
    acc_a = sum(r["decision"] == "accept" for r in ra.values())
    acc_b = sum(r["decision"] == "accept" for r in rb.values())
    fa_b = sum(r["false_accept"] for r in rb.values())
    lines += ["", f"Accepted: {acc_a} → {acc_b}. False accepts under the final rule: {fa_b}.", ""]
    lines += ["**Extraction metrics identical: yes.**" if not bugs else "**BUG: extraction metrics differ:**"]
    lines += [f"- {b_}" for b_ in bugs]
    Path(new, "rescore_vs_original.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if bugs else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
