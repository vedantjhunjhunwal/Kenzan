"""Presentation tree (from indentation) and calculation tree (what each row sums into).

calc_parent rules, in order (calc_parent_source="rule", calc_rule says which):
  closing_total  "X合計" closes the open header whose stem is X ("資産の部" -> 資産, "流動資産" -> 流動資産);
                 its children are the rows still unclaimed inside that header's block.
  net_block      "X（純額）" claims the nearest preceding unclaimed row labelled X (the gross line)
                 plus every unclaimed row after it (the 減価償却累計額 / 減損損失累計額 contras).
                 If no row is labelled X, the gross line is the nearest preceding row indented less.
  registry       a total with fixed components by definition (e.g. 負債純資産合計 = 負債合計 + 純資産合計).
Fallback (calc_parent_source="solver"): only for a total no rule explains. Candidates are the
contiguous runs of unclaimed rows ending just above the total -- never arbitrary subsets -- and the
same run must fit the rounding window in every period (a period that cannot be evaluated rejects it). Longest fitting run wins; the number of fitting
runs is recorded so ambiguity is visible. Checks over solver links are scored separately.
Rows still unclaimed when the page ends get calc_parent_source="unresolved" (cross-page work).
calc_rule describes a row's link to ITS parent; total_status describes a total's OWN children
("unexplained" when no rule or solver run fits, "net_without_gross" for an orphan 純額 row).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .registry import NAMED_TOTALS, concept_of
from .validate import Cell, check_sum

NET_SUFFIX = "(純額)"          # label_norm is NFKC, so （純額） arrives as (純額)
SECTION_SUFFIX = "の部"
MEMO_PREFIXES = ("うち", "(うち")


# ----------------------------------------------------------------------------- presentation
def cluster_1d(values: list[float], tol: float) -> list[float]:
    """Cluster sorted values; consecutive gaps > tol start a new cluster. Returns cluster centers."""
    vs = sorted(values)
    groups: list[list[float]] = []
    for v in vs:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def assign_presentation(rows: list[dict], char_size: float) -> list[float]:
    """Indent levels learned from this page's label x0 values (tolerance relative to font size)."""
    centers = cluster_1d([r["label_x0"] for r in rows], tol=0.4 * char_size)
    for r in rows:
        r["presentation_level"] = min(range(len(centers)), key=lambda i: abs(centers[i] - r["label_x0"]))
    for i, r in enumerate(rows):
        r["presentation_parent"] = None
        for prev in reversed(rows[:i]):
            if prev["presentation_level"] < r["presentation_level"]:
                r["presentation_parent"] = prev["row_id"]
                break
    return centers


def assign_label_paths(rows: list[dict]) -> None:
    """label_path from label_norm along presentation parents; duplicates get '#2', '#3'..."""
    by_id = {r["row_id"]: r for r in rows}
    seen: dict[str, int] = {}
    for r in rows:
        parts, cur = [r["label_norm"]], r
        while cur.get("presentation_parent"):
            cur = by_id[cur["presentation_parent"]]
            parts.append(cur["label_norm"])
        path = "/".join(reversed(parts))
        seen[path] = seen.get(path, 0) + 1
        r["label_path"] = path if seen[path] == 1 else f"{path}#{seen[path]}"


# ----------------------------------------------------------------------------- calculation
@dataclass
class _Block:
    header: dict | None
    level: int
    members: list[dict] = field(default_factory=list)


def _has_values(r: dict) -> bool:
    return bool(r.get("cells"))


def _is_memo(r: dict) -> bool:
    return r["label_norm"].startswith(MEMO_PREFIXES)


def _total_stem(label: str) -> str | None:
    return label[:-2] if label.endswith("合計") and len(label) > 2 else None


def _header_stem(label: str) -> str:
    return label[: -len(SECTION_SUFFIX)] if label.endswith(SECTION_SUFFIX) else label


def _link(children: list[dict], parent: dict, source: str, rule: str | None) -> None:
    for c in children:
        c["calc_parent"], c["calc_parent_source"], c["calc_rule"] = parent["row_id"], source, rule


def _cell(r: dict, pid: str) -> Cell | None:
    c = r.get("cells", {}).get(pid)
    return None if c is None else Cell(c["reported_value"], bool(c["is_negative"]), bool(c["is_nil"]))


def _fits(total: dict, kids: list[dict], periods: list[dict], mode: str) -> bool:
    """One assignment must fit EVERY period. A period that cannot be evaluated (total or a child
    cell missing) rejects the run: a single period's arithmetic is not enough evidence."""
    if not periods:
        return False
    for p in periods:
        res = check_sum("", p["id"], "", _cell(total, p["id"]), [""] * len(kids),
                        [_cell(k, p["id"]) for k in kids], mode)
        if res.status not in ("exact", "within_tolerance"):
            return False
    return True


def assign_calc(rows: list[dict], periods: list[dict], mode: str = "truncate") -> None:
    for r in rows:
        r.update(calc_parent=None, calc_parent_source=None, calc_rule=None, closes_header=None, total_status=None)
    stack = [_Block(None, -1)]

    def close_open(block: _Block) -> None:
        for m in block.members:
            m["calc_parent_source"], m["calc_rule"] = "unresolved", "block_not_closed"

    for r in rows:
        label = r["label_norm"]
        if _is_memo(r):
            r["calc_parent_source"], r["calc_rule"] = "excluded", "memo"
            continue
        if not _has_values(r):                                         # header row
            while len(stack) > 1 and stack[-1].level >= r["presentation_level"]:
                close_open(stack.pop())
            stack.append(_Block(r, r["presentation_level"]))
            continue

        if label.endswith(NET_SUFFIX):                                 # gross / contra / net block
            members, stem = stack[-1].members, label[: -len(NET_SUFFIX)]
            gi = next((i for i in range(len(members) - 1, -1, -1) if members[i]["label_norm"] == stem), None)
            if gi is None:
                gi = next((i for i in range(len(members) - 1, -1, -1)
                           if members[i]["presentation_level"] < r["presentation_level"]), None)
            if gi is not None:
                _link(members[gi:], r, "rule", "net_block")
                del members[gi:]
            else:
                r["total_status"] = "net_without_gross"
            members.append(r)
            continue

        stem = _total_stem(label)
        if stem is not None:                                           # 合計 row
            idx = next((i for i in range(len(stack) - 1, 0, -1)
                        if _header_stem(stack[i].header["label_norm"]) == stem), None)
            if idx is not None:
                for b in stack[idx + 1:]:
                    close_open(b)
                del stack[idx + 1:]
                block = stack.pop()
                _link(block.members, r, "rule", "closing_total")
                r["closes_header"] = block.header["row_id"]
                stack[-1].members.append(r)
                continue
            concept = concept_of(label)
            members = stack[-1].members
            if concept in NAMED_TOTALS:
                named = [m for m in members if concept_of(m["label_norm"]) in NAMED_TOTALS[concept]]
                if named:
                    _link(named, r, "rule", "registry")
                    stack[-1].members = [m for m in members if m not in named] + [r]
                    continue
            fitting = [k for k in range(len(members), 0, -1) if _fits(r, members[-k:], periods, mode)]
            if fitting:
                k = fitting[0]
                _link(members[-k:], r, "solver", f"contiguous_run:{k}")
                r["solver_alternatives"] = len(fitting)
                del members[-k:]
            else:
                r["total_status"] = "unexplained"
            members.append(r)
            continue

        stack[-1].members.append(r)                                    # ordinary line item

    for b in stack[1:]:
        close_open(b)
    for m in stack[0].members:                                         # top level after closing
        if m["calc_parent_source"] is None and not any(x.get("calc_parent") == m["row_id"] for x in rows):
            m["calc_parent_source"], m["calc_rule"] = "unresolved", "top_level_line_item"

    has_kids = {r["calc_parent"] for r in rows if r.get("calc_parent")}
    for r in rows:
        if _is_memo(r):
            r["row_type"] = "memo"
        elif not _has_values(r):
            r["row_type"] = "header"
        elif r["row_id"] in has_kids or _total_stem(r["label_norm"]) or r["label_norm"].endswith(NET_SUFFIX):
            r["row_type"] = "total" if r["calc_parent"] is None and r["row_id"] in has_kids else "subtotal"
        else:
            r["row_type"] = "line_item"
