"""VLM backend plumbing, tested with a FAKE runner (no model, no GPU).

These tests show that (a) a correct table from a VLM goes through the shared pipeline to the same
cells, structure and ACCEPT decision as the born-digital page, (b) one wrong digit fails a check and
goes to review, and (c) malformed responses and cache misses fail the page instead of being
repaired. They are not evidence about any real model's accuracy.
"""
import io
import json
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from docparse import vlm
from docparse.evaluate import evaluate
from docparse.pipeline import parse_pdf

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests/fixtures/balance_sheet_after_correction.pdf"
GT = ROOT / "gt/out/ground_truth"


def _scan_pdf(tmp_path, dpi=100) -> Path:
    page = pymupdf.open(FIX)[0]
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    buf = io.BytesIO()
    Image.frombytes("L", (pix.width, pix.height), pix.samples).save(buf, format="PNG")
    out = pymupdf.open()
    p = out.new_page(width=page.rect.width, height=page.rect.height)
    p.insert_image(p.rect, stream=buf.getvalue())
    path = tmp_path / "scan.pdf"
    out.save(path)
    return path


def _gt_table() -> dict:
    g = json.loads((ROOT / "gt/out/ground_truth.json").read_text(encoding="utf-8"))
    rows = [{"label": r["label_ja"], "indent": r["presentation_level"],
             "values": [r["cells"][p]["raw_text"] if p in r["cells"] else None for p in ("prior", "current")]}
            for r in g["rows"]]
    return {"heading_lines": ["- 4 -", "（訂正後）", "３．連結財務諸表及び主な注記", "（１）連結貸借対照表", "（単位：千円）"],
            "columns": ["前連結会計年度(2025年３月31日)", "当連結会計年度(2026年３月31日)"], "rows": rows}


class FakeRunner:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def __call__(self, image, prompt, settings):
        self.calls += 1
        return self.text, {"model": "fake"}


def _backend(tmp_path, text):
    return vlm.VLMBackend(cache_dir=tmp_path / "cache", model="fake/model", runner=FakeRunner(text))


def test_correct_vlm_table_is_verified_and_accepted(tmp_path):
    pdf = _scan_pdf(tmp_path)
    be = _backend(tmp_path, json.dumps(_gt_table(), ensure_ascii=False))
    pred = parse_pdf(str(pdf), force_route="visual", ocr=be)
    assert pred["metadata"]["route"]["backend"] == "vlm"
    assert pred["acceptance"]["decision"] == "accept", pred["acceptance"]["reasons"]
    rep = evaluate(str(pdf), str(GT), str(tmp_path / "eval"), route="visual", backend=be)
    assert rep["cells"]["f1"] == 1.0
    assert rep["structure"]["calc_parent"] == 1.0 and rep["structure"]["presentation_parent"] == 1.0


def test_one_wrong_digit_fails_a_check_and_goes_to_review(tmp_path):
    t = _gt_table()
    row = next(r for r in t["rows"] if r["label"] == "受取手形")
    row["values"][0] = "20,559"                          # 20,659 misread
    pred = parse_pdf(str(_scan_pdf(tmp_path)), force_route="visual",
                     ocr=_backend(tmp_path, json.dumps(t, ensure_ascii=False)))
    assert pred["acceptance"]["decision"] == "review"
    assert any(r.startswith("A2") for r in pred["acceptance"]["reasons"])


@pytest.mark.parametrize("text", [
    "Here is the table: {}",                                           # prose around JSON
    json.dumps({"heading_lines": [], "columns": ["a"], "rows": [{"label": "x", "indent": 0, "values": ["12a"]}]}),
    json.dumps({"heading_lines": [], "columns": ["a", "b"], "rows": [{"label": "x", "indent": 0, "values": ["1"]}]}),
    json.dumps({"heading_lines": [], "columns": ["a"], "rows": [{"label": "x", "indent": "0", "values": ["1"]}]}),
    json.dumps({"heading_lines": [], "columns": ["a"], "rows": [], "note": "x"}),
])
def test_malformed_response_fails_the_page_never_repaired(tmp_path, text):
    with pytest.raises(vlm.VLMSchemaError):
        vlm.parse_response(text)
    pred = parse_pdf(str(_scan_pdf(tmp_path)), safe=True, force_route="visual", ocr=_backend(tmp_path, text))
    assert pred["status"] == "failed" and pred["acceptance"]["decision"] == "review"


def test_fenced_json_is_accepted_as_is():
    t = {"heading_lines": [], "columns": ["a"], "rows": [{"label": "x", "indent": 0, "values": ["△1,234"]}]}
    assert vlm.parse_response("```json\n" + json.dumps(t) + "\n```") == t


def test_cache_roundtrip_and_offline_miss(tmp_path):
    pdf = _scan_pdf(tmp_path)
    text = json.dumps(_gt_table(), ensure_ascii=False)
    be = _backend(tmp_path, text)
    parse_pdf(str(pdf), force_route="visual", ocr=be)
    parse_pdf(str(pdf), force_route="visual", ocr=be)
    assert be.runner.calls == 1                                       # second read is a cache hit
    offline = vlm.VLMBackend(cache_dir=tmp_path / "cache", model="fake/model")
    assert parse_pdf(str(pdf), force_route="visual", ocr=offline)["acceptance"]["decision"] == "accept"
    other = vlm.VLMBackend(cache_dir=tmp_path / "cache", model="fake/model", settings={"max_side": 1000})
    pred = parse_pdf(str(pdf), safe=True, force_route="visual", ocr=other)   # different settings: no reuse
    assert pred["status"] == "failed" and "VLMCacheMiss" in pred["reason"]


def test_cache_key_is_the_embedded_image_bytes(tmp_path):
    pdf = _scan_pdf(tmp_path)
    img, how = vlm.page_image(pymupdf.open(pdf)[0])
    assert how.startswith("embedded:")
    _, how2 = vlm.page_image(pymupdf.open(FIX)[0])                      # born-digital: rendered
    assert how2 == "render:300dpi"
