"""Multiline labels, row/column spans and the uncertain-layout decision (docparse.layout, A11).

Fixtures are drawn by gt/synthetic_pages.py (one layout pattern per page, ground truth from the same
spec). They are synthetic: they show the rules do what they say, not that real filings look like this.
"""
import json
import sys
from pathlib import Path

import pytest

from docparse.evaluate import score_dimensions
from docparse.layout import BodyRow, reconstruct_wrapped_labels
from docparse.merge import Token
from docparse.pipeline import parse_pdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt"))
import synthetic_pages  # noqa: E402

WRAPPED = {"受取手形、売掛金及び契約資産", "建物及び構築物（純額）", "退職給付に係る資産", "投資その他の資産合計"}


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    d = tmp_path_factory.mktemp("synthetic")
    out = {}
    for v in synthetic_pages.VARIANTS:
        gt = synthetic_pages.draw(d / f"{v}.pdf", v)
        out[v] = (parse_pdf(str(d / f"{v}.pdf")), gt)
    return out


@pytest.mark.parametrize("variant", ["wrap_last", "wrap_first", "wrap_last_hanging", "span_header"])
def test_supported_layouts_are_reconstructed_and_accepted(pages, variant):
    pred, gt = pages[variant]
    res = score_dimensions(gt, pred)
    assert res["page_correct"], res["differences"][:5]
    assert pred["acceptance"]["decision"] == "accept", pred["acceptance"]["reasons"]
    assert pred["metadata"]["layout"]["support"] == "supported"
    spans = {r["label_ja"]: r["row_span"] for r in pred["rows"]}
    for label in WRAPPED:
        assert spans[label]["lines"] == 2 and len(spans[label]["line_bboxes"]) == 2
        assert spans[label]["value_line"] == (0 if variant == "wrap_first" else 1)
    assert all(s["lines"] == 1 for lbl, s in spans.items() if lbl not in WRAPPED)


def test_wrapped_label_feeds_the_rules(pages):
    pred, _ = pages["wrap_last"]
    rows = {r["label_ja"]: r for r in pred["rows"]}
    net = rows["建物及び構築物（純額）"]                 # the joined label still triggers the 純額 rule
    assert rows["減価償却累計額"]["calc_parent"] == net["row_id"]
    assert rows["減価償却累計額"]["calc_rule"] == "net_block"
    ioa = rows["投資その他の資産合計"]                   # and the template check finds the joined total
    t04 = [c for c in pred["checks"] if c["check_id"].startswith("T04")]
    assert t04 and all(c["status"] == "exact" and c["total_row"] == ioa["row_id"] for c in t04)


def test_spanning_header_is_a_colspan_cell_not_period_text(pages):
    pred, _ = pages["span_header"]
    cells = [h for h in pred["metadata"]["layout"]["header_cells"] if h["colspan"] > 1]
    assert [(h["text"], h["columns"]) for h in cells] == [("前期及び当期の比較", [0, 1])]
    assert [p["label_ja"] for p in pred["periods"]] == ["前連結会計年度", "当連結会計年度"]


def test_spanning_header_with_a_date_goes_to_review(pages):
    pred, gt = pages["span_header_date"]
    assert pred["acceptance"]["decision"] == "review"
    assert any(r.startswith("A11") and "spanning_header_with_date" in r for r in pred["acceptance"]["reasons"])
    assert pred["metadata"]["layout"]["support"] == "uncertain"


def test_ambiguous_wrap_is_not_guessed(pages):
    pred, gt = pages["ambiguous_wrap"]
    assert pred["acceptance"]["decision"] == "review"
    assert any(r.startswith("A11") and "ambiguous_wrap" in r for r in pred["acceptance"]["reasons"])
    assert "wrapped_labels" not in pred["metadata"]["layout"]      # nothing joined on a guess
    assert not score_dimensions(gt, pred)["page_correct"]


def test_reference_page_is_unchanged():
    pred = parse_pdf(str(ROOT / "tests/fixtures/balance_sheet_after_correction.pdf"))
    lay = pred["metadata"]["layout"]
    assert lay["support"] == "supported" and "wrapped_labels" not in lay and "uncertain" not in lay
    assert all(r["row_span"]["lines"] == 1 for r in pred["rows"])
    assert all(h["colspan"] == 1 for h in lay["header_cells"])
    assert pred["acceptance"]["decision"] == "accept"


# ---- unit level: BodyRow lists without a PDF -------------------------------------------------
def _row(text, x0, y, value=None):
    lab = [Token(text, (x0, y, x0 + 9 * len(text), y + 9), 9.0)]
    vals = {0: Token(value, (400 - 5 * len(value), y, 400, y + 9), 9.0)} if value else {}
    return BodyRow(lab, vals, y, y + 9, [[x0, y, 400, y + 9]], 0 if vals else None)


def test_header_with_indented_child_is_kept():
    body = [_row("流動資産", 60, 100), _row("現金及び預金", 73, 112, "1,000"), _row("その他", 73, 124, "5")]
    rows, joins, unc = reconstruct_wrapped_labels(body, 9.0, 12.0)
    assert len(rows) == 3 and not joins and not unc


def test_label_only_row_without_children_is_uncertain():
    body = [_row("現金及び預金", 73, 100, "1,000"), _row("注記参照", 120, 112), _row("その他", 73, 124, "5")]
    rows, joins, unc = reconstruct_wrapped_labels(body, 9.0, 12.0)
    assert not joins and [u["kind"] for u in unc] == ["label_only_row_without_children"]


def test_three_line_label_is_joined():
    body = [_row("現金", 73, 100, "1"), _row("あいうえおかきくけ", 73, 116), _row("こさしすせ", 73, 127),
            _row("たちつ", 73, 138, "9"), _row("その他", 73, 154, "5")]
    rows, joins, unc = reconstruct_wrapped_labels(body, 9.0, 16.0)
    assert [r.label_text for r in rows] == ["現金", "あいうえおかきくけこさしすせたちつ", "その他"]
    assert rows[1].lines == 3 and rows[1].value_line == 2 and not unc


def test_row_with_lost_values_is_not_joined():
    # noisy scans: OCR loses both values of a 純額 row; at row pitch it must not be glued to its neighbour
    body = [_row("建物", 73, 100, "500"), _row("減価償却累計額", 86, 112, "△200"),
            _row("建物(純額)", 86, 124), _row("土地", 73, 136, "400")]
    rows, joins, unc = reconstruct_wrapped_labels(body, 9.0, 12.0)
    assert len(rows) == 4 and not joins
    assert [u["kind"] for u in unc] == ["ambiguous_wrap"]
