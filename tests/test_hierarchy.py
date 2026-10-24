"""calc_parent rules on synthetic rows: 合計 closing headers, 純額 blocks, registry totals, solver."""
from conftest import make_cells

from docparse.hierarchy import assign_calc
from docparse.normalize import label_norm
from docparse.validate import run_checks

P = [{"id": "prior", "date": "2025-03-31"}, {"id": "current", "date": "2026-03-31"}]


def R(i, label, level, **vals):
    return {"row_id": f"r{i:02d}", "label_ja": label, "label_norm": label_norm(label)[0],
            "presentation_level": level, "cells": make_cells(**vals) if vals else {}}


def ppe_rows(net_label="建物及び構築物（純額）", gross_label="建物及び構築物"):
    return [
        R(1, "有形固定資産", 2),
        R(2, gross_label, 3, prior=2121360, current=1880082),
        R(3, "減価償却累計額", 4, prior=-1923585, current=-1640253),
        R(4, net_label, 4, prior=197775, current=239828),
        R(5, "土地", 3, prior=768976, current=705403),
        R(6, "建設仮勘定", 3, prior="nil", current=4553),
        R(7, "有形固定資産合計", 3, prior=966751, current=949784),
    ]


def parents(rows):
    return {r["row_id"]: (r["calc_parent"], r["calc_parent_source"], r["calc_rule"]) for r in rows}


def test_net_block_gross_and_contra_roll_into_net():
    rows = ppe_rows()
    assign_calc(rows, P)
    p = parents(rows)
    assert p["r02"][:2] == ("r04", "rule") and p["r03"][:2] == ("r04", "rule")
    assert p["r02"][2] == "net_block"
    # the closing total takes the NET row, not the gross/contra rows
    assert {k for k, v in p.items() if v[0] == "r07"} == {"r04", "r05", "r06"}
    assert rows[6]["closes_header"] == "r01"
    assert [r["row_type"] for r in rows] == ["header", "line_item", "line_item", "subtotal", "line_item",
                                             "line_item", "total"]


def test_net_block_with_half_width_brackets():
    rows = ppe_rows(net_label="建物及び構築物 (純額)")
    assign_calc(rows, P)
    assert parents(rows)["r03"][0] == "r04"


def test_net_block_gross_found_by_indent_when_label_differs():
    rows = ppe_rows(gross_label="建物・構築物")          # stem of the net row does not match
    assign_calc(rows, P)
    p = parents(rows)
    assert p["r02"][0] == "r04" and p["r03"][0] == "r04"


def test_section_total_closes_の部_header_and_registry_total():
    rows = [
        R(1, "負債の部", 0), R(2, "流動負債", 1), R(3, "買掛金", 2, prior=100, current=90),
        R(4, "流動負債合計", 2, prior=100, current=90), R(5, "負債合計", 1, prior=100, current=90),
        R(6, "純資産の部", 0), R(7, "株主資本", 1), R(8, "資本金", 2, prior=50, current=50),
        R(9, "株主資本合計", 2, prior=50, current=50), R(10, "純資産合計", 1, prior=50, current=50),
        R(11, "負債純資産合計", 0, prior=150, current=140),
    ]
    assign_calc(rows, P)
    p = parents(rows)
    assert p["r04"][0] == "r05" and rows[4]["closes_header"] == "r01"     # 負債合計 closes 負債の部
    assert p["r09"][0] == "r10" and rows[9]["closes_header"] == "r06"
    assert p["r05"] == ("r11", "rule", "registry") and p["r10"] == ("r11", "rule", "registry")


def test_solver_only_for_unexplained_totals_and_reported_separately():
    rows = [R(1, "流動資産", 1), R(2, "現金及び預金", 2, prior=10, current=20),
            R(3, "売掛金", 2, prior=5, current=5), R(4, "小口合計", 2, prior=15, current=25),
            R(5, "流動資産合計", 2, prior=15, current=25)]
    assign_calc(rows, P)
    p = parents(rows)
    assert p["r02"][1] == "solver" and p["r03"][1] == "solver"           # no header named 小口
    assert rows[3]["solver_alternatives"] >= 1
    for r in rows:
        r["label_path"] = r["label_norm"]
    checks, summary = run_checks(rows, P, "consolidated_balance_sheet")
    assert summary["solver"]["checks"] == 2                              # scored separately


def test_solver_requires_same_run_to_fit_both_periods():
    # prior: 10+5 = 15 fits; current: 20+5 = 25 but total says 40 -> no run fits both
    rows = [R(1, "流動資産", 1), R(2, "現金及び預金", 2, prior=10, current=20),
            R(3, "売掛金", 2, prior=5, current=5), R(4, "小口合計", 2, prior=15, current=40)]
    assign_calc(rows, P)
    assert rows[1]["calc_parent"] is None and rows[3]["total_status"] == "unexplained"


def test_solver_rejects_run_when_a_period_cannot_be_evaluated():
    # prior fits, but a child has no current cell -> current not evaluable -> rejected
    rows = [R(1, "流動資産", 1), R(2, "現金及び預金", 2, prior=10, current=20),
            R(3, "売掛金", 2, prior=5), R(4, "小口合計", 2, prior=15, current=25)]
    assign_calc(rows, P)
    assert rows[2]["calc_parent"] is None and rows[3]["total_status"] == "unexplained"


def test_memo_rows_never_summed():
    rows = [R(1, "流動資産", 1), R(2, "売掛金", 2, prior=10, current=10),
            R(3, "（うち関係会社）", 3, prior=4, current=4), R(4, "流動資産合計", 2, prior=10, current=10)]
    assign_calc(rows, P)
    assert rows[2]["calc_parent"] is None and rows[2]["row_type"] == "memo"
    assert rows[1]["calc_parent"] == "r04"


def test_unclosed_block_is_unresolved_not_guessed():
    rows = [R(1, "負債の部", 0), R(2, "流動負債", 1), R(3, "買掛金", 2, prior=100, current=90)]
    assign_calc(rows, P)                                                  # page ends mid-statement
    assert rows[2]["calc_parent"] is None and rows[2]["calc_parent_source"] == "unresolved"
