"""Per-dimension scoring and complete-page correctness (docparse.evaluate.score_dimensions).

Each test corrupts a copy of the reference ground truth in one way and checks that the page can no
longer score as perfect. The wrong-unit case also shows why the dimensions were added: the
historical cell metric (printed values keyed on path and date) still gives 100% there.
"""
import copy
import json
from pathlib import Path

import pytest

from docparse.evaluate import score_cells, score_dimensions, score_structure
from docparse.export import read_long, write_all

ROOT = Path(__file__).resolve().parents[1]
GT = json.loads((ROOT / "gt/out/ground_truth.json").read_text(encoding="utf-8"))


def perfect():
    d = copy.deepcopy(GT)
    d["status"] = "ok"
    return d


def row(d, label):
    return next(r for r in d["rows"] if r["label_norm"] == label)


def old_cell_f1(pred, tmp_path):
    write_all(GT, tmp_path / "gt", "gt")
    write_all(pred, tmp_path / "pred", "pred")
    return score_cells(read_long(tmp_path / "gt/gt.long.csv"), read_long(tmp_path / "pred/pred.long.csv"))["f1"]


def assert_not_perfect(res, dimension):
    assert res["page_correct"] is False
    assert res["overall_score"] < 1.0
    assert res[dimension] < 1.0, f"{dimension} should catch this error"


def test_perfect_copy_is_page_correct():
    res = score_dimensions(GT, perfect())
    assert res["page_correct"] and res["page_correct_incl_corrections"]
    assert res["overall_score"] == 1.0
    assert res["counts"]["gt_cells"] == 74 and res["differences"] == []


def test_wrong_unit_is_not_perfect_although_old_cell_f1_is(tmp_path):
    d = perfect()
    d["metadata"]["unit_ja"], d["metadata"]["unit_multiplier"] = "百万円", 10**6
    for r in d["rows"]:
        for c in r["cells"].values():
            if c["reported_value"] is not None:
                c["value"] = c["reported_value"] * 10**6
    assert old_cell_f1(d, tmp_path) == 1.0            # the historical metric is blind to the unit
    res = score_dimensions(GT, d)
    assert_not_perfect(res, "unit")
    assert res["scaled_values"] < 1.0
    assert res["values_printed"] == 1.0               # printed digits are still right, and reported so


def test_swapped_period_dates_are_not_perfect():
    d = perfect()
    d["periods"][0]["date"], d["periods"][1]["date"] = d["periods"][1]["date"], d["periods"][0]["date"]
    res = score_dimensions(GT, d)
    assert_not_perfect(res, "periods_columns")
    assert res["periods_cells"] < 1.0
    assert res["values_printed"] == 1.0               # column positions unchanged: the values are right


def test_swapped_value_columns_are_not_perfect():
    d = perfect()
    for r in d["rows"]:
        if r["cells"]:
            r["cells"] = {"prior": r["cells"].get("current"), "current": r["cells"].get("prior")}
            r["cells"] = {k: v for k, v in r["cells"].items() if v is not None}
    res = score_dimensions(GT, d)
    assert_not_perfect(res, "values_printed")
    assert res["periods_cells"] < 1.0


def test_wrong_sign_is_not_perfect(tmp_path):
    d = perfect()
    c = row(d, "貸倒引当金")["cells"]["prior"]
    assert c["is_negative"]
    c["is_negative"], c["reported_value"], c["value"] = False, -c["reported_value"], -c["value"]
    c["raw_text"] = c["raw_text"].lstrip("△")
    assert old_cell_f1(d, tmp_path) < 1.0
    res = score_dimensions(GT, d)
    assert_not_perfect(res, "values_printed")
    assert res["scaled_values"] < 1.0


def test_missing_cell_is_not_perfect():
    d = perfect()
    del row(d, "受取手形")["cells"]["current"]
    assert_not_perfect(score_dimensions(GT, d), "values_printed")


def test_missing_row_is_not_perfect():
    d = perfect()
    d["rows"] = [r for r in d["rows"] if r["label_norm"] != "仕掛品"]
    res = score_dimensions(GT, d)
    assert_not_perfect(res, "rows_matched")


def test_extra_row_and_extra_cell_are_not_perfect():
    d = perfect()
    extra = copy.deepcopy(row(d, "仕掛品"))
    extra["row_id"], extra["label_norm"], extra["label_ja"] = "r99", "架空資産", "架空資産"
    d["rows"].insert(10, extra)
    assert_not_perfect(score_dimensions(GT, d), "no_extra")
    d = perfect()
    hdr = row(d, "流動資産")
    hdr["cells"] = {"prior": copy.deepcopy(row(d, "仕掛品")["cells"]["prior"])}
    assert_not_perfect(score_dimensions(GT, d), "no_extra")


def test_wrong_hierarchy_is_not_perfect():
    d = perfect()
    r = row(d, "仕掛品")                                # one level deeper, under 商品及び製品
    parent = row(d, "商品及び製品")
    r["presentation_level"] += 1
    r["presentation_parent"] = parent["row_id"]
    r["label_path"] = parent["label_path"] + "/仕掛品"
    res = score_dimensions(GT, d)
    assert_not_perfect(res, "paths")
    assert res["structure_presentation_level"] < 1.0 and res["structure_presentation_parent"] < 1.0
    assert res["values_printed"] == 1.0                # values are scored independently of the path


def test_wrong_calc_parent_is_not_perfect():
    d = perfect()
    r = row(d, "仕掛品")
    r["calc_parent"] = row(d, "資産合計")["row_id"]
    assert_not_perfect(score_dimensions(GT, d), "structure_calc_parent")


def test_misread_label_keeps_values_scored():
    d = perfect()
    r = row(d, "仕掛品")
    r["label_norm"] = r["label_ja"] = "仕掛晶"
    r["label_path"] = r["label_path"].replace("仕掛品", "仕掛晶")
    res = score_dimensions(GT, d)
    assert_not_perfect(res, "labels")
    assert res["values_printed"] == 1.0 and res["rows_matched"] == 1.0


def test_failed_status_is_not_page_correct():
    d = perfect()
    d["status"] = "no_table"
    assert score_dimensions(GT, d)["page_correct"] is False


@pytest.mark.parametrize("field", ["presentation_level", "row_type"])
def test_structure_field_errors_counted(field):
    d = perfect()
    r = row(d, "仕掛品")
    r[field] = 9 if field == "presentation_level" else "subtotal"
    res = score_dimensions(GT, d)
    assert res[f"structure_{field}"] < 1.0 and not res["page_correct"]
    assert score_structure(GT, d)[field] < 1.0         # the historical structure metric agrees
