from docparse import corrections
from docparse.extract import Segment
from docparse.router import assess
from docparse.validate import Cell, check_sum, window


# --------------------------------------------------------------------------- underline vs rule
def _rows(*bboxes):
    return [{"row_id": f"r{i}", "cells": {"current": {"bbox": list(b)}}} for i, b in enumerate(bboxes)]


VALUE = (490.9, 578.8, 522.6, 587.8)      # a 7-glyph number, right-aligned at 522.6
OTHER = [(481.9, 100 + 12 * k, 522.6, 109 + 12 * k) for k in range(6)]


def test_glyph_width_underline_is_a_correction():
    rows = _rows(VALUE, *OTHER)
    segs = [Segment(490.9, 588.0, 522.5, 588.5, 0.5, "rect")]
    corrections.mark(rows, segs, 9.0)
    assert rows[0]["cells"]["current"]["is_corrected"] is True


def test_column_width_rule_is_not_a_correction():
    rows = _rows(VALUE, *OTHER)
    segs = [Segment(400.4, 588.0, 524.8, 588.7, 0.7, "rect")]          # rule under the whole column
    corrections.mark(rows, segs, 9.0)
    assert rows[0]["cells"]["current"]["is_corrected"] is False


def test_rule_above_next_row_not_attributed_to_cell_above_it():
    rows = _rows(VALUE, *OTHER)
    segs = [Segment(400.4, 590.4, 524.8, 591.1, 0.7, "rect")]
    corrections.mark(rows, segs, 9.0)
    assert rows[0]["cells"]["current"]["is_corrected"] is False


def test_no_vector_data_means_unknown_not_false():
    rows = _rows(VALUE)
    corrections.mark(rows, None, 9.0)
    assert rows[0]["cells"]["current"]["is_corrected"] is None


# --------------------------------------------------------------------------- router gate
def test_router_accepts_clean_japanese_text():
    chars = "連結貸借対照表 資産の部 現金及び預金 2,002,429 608,281 △2,979 －" * 3
    d = assess(chars, ["2,002,429", "608,281", "△2,979", "－"], 0.0)
    assert d.route == "born_digital"


def test_router_rejects_garbage_text_layer():
    chars = "" * 20 + "12"                      # private-use glyph codes
    d = assess(chars, ["12"], 0.0)
    assert d.route == "visual" and any("decode" in r for r in d.reasons)


def test_router_rejects_unparseable_numbers():
    chars = "資産の部 現金及び預金 " * 5
    d = assess(chars, ["2.0O2,4Z9", "6O8,28l", "1,O19,663"], 0.0)
    assert d.route == "visual"


def test_router_rejects_scan_with_ocr_overlay():
    chars = "連結貸借対照表 資産の部 現金及び預金 2,002,429 608,281" * 3
    d = assess(chars, ["2,002,429", "608,281"], 0.95)
    assert d.route == "visual"


# --------------------------------------------------------------------------- tolerance window
def test_sign_aware_window():
    assert window(9, 1, False) == (-1, 8)
    assert window(1, 1, False) == (-1, 0)
    assert window(5, 0, False) == (0, 4)
    assert window(1, 2, True) == (-1, 1)


def test_negative_zero_child_counts_as_negative():
    total = Cell(5, False)
    kids = [Cell(5, False), Cell(0, True)]        # 5 + △0
    r = check_sum("x", "p", "t", total, ["a", "b"], kids)
    assert (r.p, r.q) == (1, 1) and r.status == "exact"


def test_nil_child_excluded_from_counts():
    r = check_sum("x", "p", "t", Cell(7), ["a", "b"], [Cell(6), Cell(None, is_nil=True)])
    assert (r.p, r.q) == (1, 0) and r.window == (0, 0) and r.status == "fail"


def test_missing_row_is_not_evaluable_never_pass():
    r = check_sum("x", "p", "t", None, ["a"], [Cell(1)])
    assert r.status == "not_evaluable"
