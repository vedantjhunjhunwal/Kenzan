"""△ as a separate glyph run must end up in the right column, attached to its number."""
from conftest import make_span

from docparse.extract import Span
from docparse.layout import analyze
from docparse.merge import merge_tokens

PRIOR_R, CURRENT_R = 400.0, 520.0


def _page(rows, triangle_separate=True):
    spans = [make_span("前期", 330, 100), make_span("(2025年３月31日)", 300, 110),
             make_span("当期", 455, 100), make_span("(2026年３月31日)", 425, 110),
             make_span("（単位：千円）", 460, 85)]
    y = 130.0
    for label, prior, current in rows:
        spans.append(make_span(label, 80, y))
        for val, right in ((prior, PRIOR_R), (current, CURRENT_R)):
            if val is None:
                continue
            if triangle_separate and val.startswith("△"):
                digits = make_span(val[1:], 0, y, right=right)
                spans.append(digits)
                spans.append(make_span("△", 0, y, right=digits.bbox[0]))   # own span, touching
            else:
                spans.append(make_span(val, 0, y, right=right))
        y += 12
    return spans


ROWS = [("建物及び構築物", "2,121,360", "1,880,082"),
        ("減価償却累計額", "△1,923,585", "△1,640,253"),
        ("建物及び構築物（純額）", "197,775", "239,828"),
        ("建設仮勘定", "－", "4,553")]


def test_triangle_span_merges_and_lands_in_current_column():
    lay = analyze(merge_tokens(_page(ROWS)))
    assert [round(c.right) for c in lay.columns] == [PRIOR_R, CURRENT_R]
    contra = lay.body[1]
    texts = {lay.columns[i].period["id"]: t.text for i, t in contra.values.items()}
    assert texts == {"prior": "△1,923,585", "current": "△1,640,253"}


def test_periods_units_learned_from_header():
    lay = analyze(merge_tokens(_page(ROWS)))
    assert [(p["id"], p["date"]) for p in lay.periods] == [("prior", "2025-03-31"), ("current", "2026-03-31")]
    assert lay.unit == ("千円", 1000)


def test_nil_dash_assigned_to_its_column():
    lay = analyze(merge_tokens(_page(ROWS)))
    row = lay.body[3]
    assert {lay.columns[i].period["id"]: t.text for i, t in row.values.items()} == {"prior": "－", "current": "4,553"}


def test_ocr_comma_space_fragments_merge():
    # OCR word boxes for "2, 002, 429": three words separated by narrow gaps
    s = 9.0
    words = [Span("2,", (360, 50, 368, 59), s, source="ocr"), Span("002,", (370, 50, 388, 59), s, source="ocr"),
             Span("429", (390, 50, 403, 59), s, source="ocr")]
    lines = merge_tokens(words)
    assert [t.text for t in lines[0]] == ["2,002,429"]


def test_distant_numbers_do_not_merge():
    words = [make_span("現金", 80, 50), make_span("608,281", 0, 50, right=400), make_span("1,123", 0, 50, right=520)]
    assert [t.text for t in merge_tokens(words)[0]] == ["現金", "608,281", "1,123"]
