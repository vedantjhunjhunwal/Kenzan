from docparse.hierarchy import assign_label_paths
from docparse.normalize import label_norm, parse_dates, parse_unit, parse_value


def test_delta_zero_keeps_sign():
    pv = parse_value("△0")
    assert pv.reported_value == 0 and pv.is_negative and not pv.is_nil
    assert pv.raw_text == "△0"


def test_triangle_negative_and_black_triangle():
    assert parse_value("△1,923,585").reported_value == -1923585
    assert parse_value("▲2,979").reported_value == -2979


def test_nil_dash_is_null_not_zero():
    for g in ["－", "―", "—", "ー", "−", "-"]:
        pv = parse_value(g)
        assert pv.is_nil and pv.reported_value is None, g
    assert parse_value("0").reported_value == 0 and not parse_value("0").is_nil


def test_dash_before_digits_is_negative_with_warning():
    pv = parse_value("－1,234")
    assert pv.reported_value == -1234 and pv.is_negative and not pv.is_nil
    assert "dash_minus" in pv.warnings


def test_full_width_digits_and_commas():
    assert parse_value("１，２３４").reported_value == 1234
    assert parse_value("2, 002, 429").reported_value == 2002429      # OCR comma-space


def test_bad_grouping_is_flagged_not_dropped():
    pv = parse_value("2,02,429")
    assert pv.reported_value == 202429 and "bad_digit_grouping" in pv.warnings


def test_labels_are_not_values():
    for s in ["現金及び預金", "1年内返済予定の長期借入金", "※1"]:
        assert parse_value(s) is None, s


def test_full_and_half_width_brackets_give_same_label_norm():
    a, _ = label_norm("建物及び構築物（純額）")
    b, _ = label_norm("建物及び構築物 (純額)")
    assert a == b == "建物及び構築物(純額)"


def test_full_and_half_width_brackets_give_same_label_path():
    def rows(net_label):
        return [
            {"row_id": "r1", "label_norm": label_norm("有形固定資産")[0], "presentation_parent": None},
            {"row_id": "r2", "label_norm": label_norm("建物及び構築物")[0], "presentation_parent": "r1"},
            {"row_id": "r3", "label_norm": label_norm(net_label)[0], "presentation_parent": "r2"},
        ]
    a, b = rows("建物及び構築物（純額）"), rows("建物及び構築物　(純額)")
    assign_label_paths(a)
    assign_label_paths(b)
    assert a[2]["label_path"] == b[2]["label_path"] == "有形固定資産/建物及び構築物/建物及び構築物(純額)"


def test_footnote_refs_split_from_label():
    norm, refs = label_norm("受取手形及び売掛金※１,※２")
    assert norm == "受取手形及び売掛金" and refs == ["※1", "※2"]


def test_dates_western_and_era():
    assert parse_dates("(2025 年３月 31 日)") == ["2025-03-31"]
    assert parse_dates("（令和７年３月31日）") == ["2025-03-31"]
    assert parse_dates("（平成31年３月31日）") == ["2019-03-31"]
    assert parse_dates("令和元年５月１日") == ["2019-05-01"]


def test_unit_line():
    assert parse_unit("（単位：千円）") == ("千円", 1000)
    assert parse_unit("(単位 : 百万円)") == ("百万円", 1_000_000)
