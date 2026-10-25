"""Round-2 changes: deskew, robust line height, content-based statement type, acceptance rule."""
import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw
from conftest import FIXTURES, make_span

from docparse.acceptance import decide
from docparse.deskew import deskew, estimate_skew
from docparse.extract import Span
from docparse.merge import merge_tokens
from docparse.pipeline import parse_pdf
from docparse.registry import infer_statement_type, sections_present

PDF = FIXTURES / "balance_sheet_after_correction.pdf"


# --------------------------------------------------------------------------- deskew
def _table_image(w=1600, h=2000):
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    for k in range(40):                                   # text-like rows + a few rules
        y = 100 + 45 * k
        for x in range(150, 1450, 60):
            d.rectangle([x, y, x + 40, y + 22], fill=0)
        if k % 6 == 0:
            d.line([120, y + 32, 1480, y + 32], fill=0, width=3)
    return img


@pytest.mark.parametrize("angle", [1.3, -0.7])
def test_estimate_skew_recovers_rotation(angle):
    rotated = _table_image().rotate(angle, resample=Image.BICUBIC, fillcolor=255)
    assert abs(estimate_skew(rotated) + angle) <= 0.1      # correction = minus the applied rotation


def test_level_page_is_not_resampled():
    img = _table_image()
    out, applied = deskew(img)
    assert applied == 0.0 and out is img


# --------------------------------------------------------------------------- robust line height
def test_degenerate_box_does_not_split_a_line():
    s = 9.0
    spans = [Span("長期貸付金", (100, 603, 145, 612), s, source="ocr"),
             Span("ー", (389.8, 607.2, 396.2, 607.4), 0.2, source="ocr"),       # 0.2pt-high dash box
             Span("62,", (495.8, 603.9, 506.4, 611.1), s, source="ocr"),
             Span("000", (509.8, 604.0, 522.0, 610.0), s, source="ocr")]
    spans += [Span("その他", (100, 615 + 12 * k, 127, 624 + 12 * k), s, source="ocr") for k in range(4)]
    lines = merge_tokens(spans)
    first = [t.text for t in lines[0]]
    assert first == ["長期貸付金", "ー", "62,000"]
    assert len(lines) == 5


def test_text_layer_lines_unchanged_by_robust_height():
    spans = [make_span("前連結会計年度", 305, 122), make_span("(2025年３月31日)", 298, 131.9),
             make_span("現金及び預金", 88, 172.3), make_span("2,002,429", 0, 172.3, right=397.5)]
    assert [[t.text for t in l] for l in merge_tokens(spans)] == \
        [["前連結会計年度"], ["(2025年３月31日)"], ["現金及び預金", "2,002,429"]]


# --------------------------------------------------------------------------- statement type fallback
def test_infer_statement_type_needs_two_distinct_markers():
    assert infer_statement_type(["資産の部", "流動資産", "現金及び預金"]) == "balance_sheet_unspecified"
    assert infer_statement_type(["流動資産", "現金及び預金"]) is None
    assert infer_statement_type(["売上高", "営業利益"]) is None


@pytest.mark.skipif(not PDF.exists(), reason="fixture PDF missing")
def test_title_removed_page_falls_back_to_content(tmp_path):
    doc = pymupdf.open(PDF)
    page = doc[0]
    for r in page.search_for("連結貸借対照表"):
        page.add_redact_annot(r)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    p = tmp_path / "no_title.pdf"
    doc.save(p)
    out = parse_pdf(str(p))
    assert out["metadata"]["statement_title_ja"] is None
    assert out["metadata"]["statement_type"] == "balance_sheet_unspecified"
    assert out["metadata"]["statement_type_source"] == "content"
    assert out["check_summary"]["coverage"] == 12 / 26
    assert out["acceptance"]["decision"] == "accept"


# --------------------------------------------------------------------------- acceptance rule
PERIODS = [{"id": "prior", "date": "2025-03-31"}, {"id": "current", "date": "2026-03-31"}]


def _doc(checks, rows=None, coverage=0.5, status="ok", unit="千円", periods=None):
    rows = rows if rows is not None else [{"row_id": "r1", "label_norm": "資産の部", "cells": {}},
                                          {"row_id": "r2", "label_norm": "流動資産合計", "cells": {}}]
    return {"status": status, "reason": None, "rows": rows, "checks": checks,
            "check_summary": {"coverage": coverage}, "periods": PERIODS if periods is None else periods,
            "metadata": {"unit_ja": unit, "route": {"backend": "text_layer"}}}


def _chk(cid, status, sections=("資産",), template=True):
    return {"check_id": cid, "period": "2026-03-31", "status": status, "is_template": template,
            "sections": list(sections)}


def test_clean_page_is_accepted():
    assert decide(_doc([_chk("T01", "exact"), _chk("T06", "within_tolerance")]))["decision"] == "accept"


def test_any_failed_check_means_review():
    d = decide(_doc([_chk("T01", "exact"), _chk("D:x", "fail", (), template=False)]))
    assert d["decision"] == "review" and d["reasons"][0].startswith("A2")


def test_not_evaluable_check_for_present_section_means_review():
    d = decide(_doc([_chk("T01", "not_evaluable")]))
    assert d["decision"] == "review" and any(r.startswith("A3") for r in d["reasons"])


def test_checks_for_absent_sections_are_not_required():
    checks = [_chk("T01", "exact"), _chk("T09", "not_evaluable", ("負債",)),
              _chk("T13", "not_evaluable", ("資産", "負債", "純資産"))]
    assert decide(_doc(checks))["decision"] == "accept"


def test_coverage_na_means_review():
    d = decide(_doc([], coverage=None))
    assert d["decision"] == "review" and any(r.startswith("A4") for r in d["reasons"])


def test_solver_links_mean_review_unless_allowed():
    rows = [{"row_id": "r1", "label_norm": "資産の部", "cells": {}},
            {"row_id": "r2", "label_norm": "その他", "cells": {}, "calc_parent_source": "solver"}]
    assert decide(_doc([_chk("T01", "exact")], rows=rows))["decision"] == "review"
    assert decide(_doc([_chk("T01", "exact")], rows=rows), allow_solver=True)["decision"] == "accept"


def test_failed_page_is_review():
    assert decide(_doc([], status="failed"))["decision"] == "review"


def test_sections_from_labels():
    assert sections_present(["資産の部", "負債純資産合計"]) == {"資産", "負債", "純資産"}


# --------------------------------------------------------------------------- amendment A6-A8 (2026-10-25)
def test_a6_missing_unit_means_review():
    d = decide(_doc([_chk("T01", "exact")], unit=None))
    assert d["decision"] == "review" and any(r.startswith("A6") for r in d["reasons"])


@pytest.mark.parametrize("periods,why", [
    ([{"id": "prior", "date": "2025-03-31"}, {"id": "current", "date": None}], "without a parsed"),
    ([{"id": "prior", "date": "2026-03-31"}, {"id": "current", "date": "2026-03-31"}], "same period-end"),
    ([{"id": "prior", "date": "2026-03-31"}, {"id": "current", "date": "2025-03-31"}], "前 column"),
    ([{"id": "col0", "date": "2025-03-31"}, {"id": "col1", "date": "2027-03-31"},
      {"id": "col2", "date": "2026-03-31"}], "one direction"),
])
def test_a7_bad_period_columns_mean_review(periods, why):
    d = decide(_doc([_chk("T01", "exact")], periods=periods))
    assert d["decision"] == "review" and any(r.startswith("A7") and why in r for r in d["reasons"])


def test_a8_vlm_without_confirmed_cross_check_is_review():
    doc = _doc([_chk("T01", "exact")])
    doc["metadata"]["route"] = {"backend": "vlm", "backend_diagnostics": {"cross_check": {"ok": False,
                                                                                       "problems": ["unit"]}}}
    d = decide(doc)
    assert d["decision"] == "review" and any(r.startswith("A8") for r in d["reasons"])
    doc["metadata"]["route"]["backend_diagnostics"]["cross_check"] = {"ok": True, "problems": []}
    assert decide(doc)["decision"] == "accept"
