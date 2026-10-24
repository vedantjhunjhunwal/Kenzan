"""Layout is learned per page (geometry variants), and failures are recorded, never fatal."""
import shutil

import pymupdf
import pytest
from conftest import FIXTURES, GT_STEM

from docparse.evaluate import evaluate
from docparse.extract import OCRNoTextError, OCRSetupError, TesseractBackend
from docparse.pipeline import parse_document, parse_pdf

PDF = FIXTURES / "balance_sheet_after_correction.pdf"
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="fixture PDF missing")


def _place(tmp_path, kind: str):
    """Re-place the original page as vector content (text + drawings kept)."""
    src = pymupdf.open(PDF)
    w, h = src[0].rect.width, src[0].rect.height
    out = pymupdf.open()
    if kind == "scale80":
        page, rect = out.new_page(width=w, height=h), pymupdf.Rect(0, 0, 0.8 * w, 0.8 * h)
    elif kind == "shift40":
        page, rect = out.new_page(width=w, height=h), pymupdf.Rect(40, 0, w + 40, h)
    elif kind == "larger_page":                                   # A4 content centred on A3
        W, H = 841.89, 1190.55
        page = out.new_page(width=W, height=H)
        rect = pymupdf.Rect((W - w) / 2, (H - h) / 2, (W + w) / 2, (H + h) / 2)
    else:
        raise ValueError(kind)
    page.show_pdf_page(rect, src, 0)
    path = tmp_path / f"{kind}.pdf"
    out.save(path)
    return path


@pytest.mark.parametrize("kind", ["scale80", "shift40", "larger_page"])
def test_geometry_variants_score_100(tmp_path, kind):
    rep = evaluate(str(_place(tmp_path, kind)), str(GT_STEM), str(tmp_path / "eval"))
    assert rep["status"] == "ok" and rep["route"]["route"] == "born_digital"
    assert rep["cells"]["f1"] == 1.0, rep["cells"]["differences"]
    s = rep["structure"]
    assert (s["presentation_level"], s["presentation_parent"], s["calc_parent"], s["row_type"]) == (1, 1, 1, 1)
    assert rep["corrections"]["accuracy"] == 1.0, rep["corrections"]["differences"]
    assert rep["validation"]["status_agreement_with_gt"] == 1.0
    assert rep["acceptance"]["decision"] == "accept", rep["acceptance"]["reasons"]


class _EmptyOCR:
    name = "stub_empty_ocr"

    def read(self, page):
        raise OCRNoTextError(f"OCR returned zero words for page {page.number}")


def test_bad_page_is_recorded_and_batch_continues(tmp_path):
    doc = pymupdf.open()
    doc.insert_pdf(pymupdf.open(PDF))
    doc.new_page()                                                # blank page -> visual route -> no words
    doc.insert_pdf(pymupdf.open(PDF))
    path = tmp_path / "three_pages.pdf"
    doc.save(path)
    res = parse_document(str(path), ocr=_EmptyOCR())
    assert [r["status"] for r in res] == ["ok", "failed", "ok"]
    assert "OCRNoTextError" in res[1]["reason"] and res[1]["rows"] == []
    assert res[2]["check_summary"]["template"]["failed"] == 0


def test_failed_page_scores_zero_instead_of_crashing(tmp_path):
    blank = pymupdf.open()
    blank.new_page()
    p = tmp_path / "blank.pdf"
    blank.save(p)
    rep = evaluate(str(p), str(GT_STEM), str(tmp_path / "eval"))
    assert rep["status"] == "failed" and rep["cells"]["recall"] == 0.0


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_missing_language_model_is_a_setup_error():
    with pytest.raises(OCRSetupError):
        TesseractBackend(lang="xx_no_such_model").check_setup()


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_tesseract_blank_page_raises_no_text():
    b = TesseractBackend()
    try:
        b.check_setup()
    except OCRSetupError:
        pytest.skip("jpn model not set up")
    blank = pymupdf.open()
    blank.new_page()
    with pytest.raises(OCRNoTextError):
        b.read(blank[0])


def test_warnings_are_counted_in_metadata():
    doc = parse_pdf(str(PDF))
    assert doc["metadata"]["warnings"] == {}                      # this page has no dash-minus etc.
