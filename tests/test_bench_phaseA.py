"""Phase A: split, EDINET fetcher (mocked HTTP), XBRL extraction, PDF page finding, GT build + alignment."""
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "tests"))

import bench_fixture as fx  # noqa: E402
import build_gt_xbrl as gtb  # noqa: E402
import fetch_edinet as fe  # noqa: E402
from split import split_of  # noqa: E402

FIXTURE_PDF = ROOT / "tests" / "fixtures" / "balance_sheet_after_correction.pdf"
KEY = "SECRET-KEY-abc123-DO-NOT-LEAK"


# --------------------------------------------------------------------------- split
def test_split_is_by_company_and_deterministic():
    assert split_of("E02144") == split_of(" e02144 ") in ("dev", "test")
    shares = sum(split_of(f"E{n:05d}") == "test" for n in range(1, 5001)) / 5000
    assert 0.67 < shares < 0.73


# --------------------------------------------------------------------------- extraction
def _zip_file(tmp_path, **kw):
    p = tmp_path / "doc_1.zip"
    p.write_bytes(fx.xbrl_zip(**kw))
    return p


def test_extract_consolidated_bs_from_ixbrl(tmp_path):
    x = fe.extract_xbrl(_zip_file(tmp_path))
    assert x["standard"] == "jgaap"
    bs = x["bs"]
    assert bs["found_by"] == "heading" and bs["scale"] == 3
    assert bs["instants"] == ["2025-03-31", "2026-03-31"]
    names = {f["name"] for tb in bs["tables"] for r in tb["rows"] for c in r["cells"] for f in c["facts"]}
    assert "jppfs_cor:CashAndDeposits" in names
    facts = [f for tb in bs["tables"] for r in tb["rows"] for c in r["cells"] for f in c["facts"]]
    assert all(not f["dims"] for f in facts)                         # decoy non-consolidated table not picked
    dep = next(f for f in facts if f["name"] == "jppfs_cor:AccumulatedDepreciationBuildingsAndStructures"
               and f["contextRef"] == "CurrentYearInstant")
    assert dep["value"] == -1_640_253_000 and dep["sign"] == "-"
    assert "rol_ConsolidatedBalanceSheet" in next(iter(x["cal"]))     # income-statement role not kept as BS


def test_ifrs_heading_found(tmp_path):
    x = fe.extract_xbrl(_zip_file(tmp_path, standard="IFRS", title="連結財政状態計算書"))
    assert x["standard"] == "ifrs" and x["bs"]["found_by"] == "heading"


def test_heading_mismatch_falls_back_to_consolidated_facts(tmp_path):
    # DEI says IFRS but the heading is the J-GAAP title: found via consolidated total facts instead
    x = fe.extract_xbrl(_zip_file(tmp_path, standard="IFRS"))
    assert x["standard"] == "ifrs" and x["bs"]["found_by"] == "fact_fallback"
    facts = [f for tb in x["bs"]["tables"] for r in tb["rows"] for c in r["cells"] for f in c["facts"]]
    assert facts and all(not f["dims"] for f in facts)


def test_find_bs_pages_skips_toc_and_ends_at_total(tmp_path):
    p = tmp_path / "r.pdf"
    p.write_bytes(fx.report_pdf())
    rng, why, diag = fe.find_bs_pages(p, "jgaap", ["5,135,249"])
    assert rng == (1, 2) and why is None and diag["end_total_value_on_last_page"] is True


def test_find_bs_pages_reports_missing_end(tmp_path):
    rng, why, _ = fe.find_bs_pages(FIXTURE_PDF, "jgaap")            # 決算短信 page: no 【】 heading
    assert rng is None and why == "bs_heading_not_found_in_pdf"


def test_sampler_reserves_slots_for_minimum_strata():
    s = fe.Sampler(3, {("standard", "ifrs"): 2}, industry_cap=10)
    j = {"standard": "jgaap", "unit": "百万円", "pages": "2", "industry": "化学"}
    i = j | {"standard": "ifrs"}
    assert s.offer(j) == (True, "accepted")
    assert s.offer(j) == (False, "slots_reserved_for_minimum_strata")
    assert s.offer(i)[0] and s.offer(i)[0] and not s.offer(i)[0]


def test_codelist_parsing_with_fullwidth_headers(tmp_path):
    p = tmp_path / "EdinetcodeDlInfo.csv"
    p.write_bytes(fx.codelist_bytes([("E00001", "上場", "有", "化学")]))
    c = fe.load_codelist(str(p))
    assert c["E00001"] == {"industry": "化学", "listed": "上場", "consolidated": "有", "name": "テストE00001",
                           "sec_code": "99990"}


# --------------------------------------------------------------------------- end to end, mocked API
class _Resp:
    def __init__(self, status=200, content=b"", ctype="application/json"):
        self.status_code, self.content, self.headers = status, content, {"Content-Type": ctype}

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)


class FakeEdinet:
    """Answers documents.json and documents/{id} like EDINET API v2; records every request."""

    def __init__(self):
        self.calls = []
        base = {"docTypeCode": "120", "formCode": "030000", "withdrawalStatus": "0", "xbrlFlag": "1",
                "pdfFlag": "1", "periodStart": "2025-04-01", "periodEnd": "2026-03-31", "secCode": "99990"}
        self.lists = {
            "2025-06-27": [base | {"docID": "S1OLD", "edinetCode": "E00001", "periodEnd": "2025-03-31",
                                   "submitDateTime": "2025-06-27 09:00", "filerName": "化学A"}],
            "2026-06-26": [base | {"docID": "S1NEW", "edinetCode": "E00001", "submitDateTime": "2026-06-26 09:00",
                                   "filerName": "化学A"},
                           base | {"docID": "S2BNK", "edinetCode": "E00002", "filerName": "銀行B"},
                           base | {"docID": "S4NOBS", "edinetCode": "E00004", "filerName": "機械D"},
                           base | {"docID": "S5UNL", "edinetCode": "E00005", "filerName": "非上場E"},
                           base | {"docID": "S6Q", "edinetCode": "E00006", "docTypeCode": "140"}],
        }

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        assert params.get("Subscription-Key") == KEY
        if url.endswith("/documents.json"):
            res = self.lists.get(params["date"], [])
            return _Resp(200, json.dumps({"metadata": {"status": "200"}, "results": res}).encode())
        doc_id = url.rsplit("/", 1)[-1]
        if params["type"] == 1:
            return _Resp(200, fx.xbrl_zip(with_bs=doc_id != "S4NOBS"), "application/octet-stream")
        return _Resp(200, fx.report_pdf(), "application/pdf")


def test_end_to_end_mocked(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    cl = tmp_path / "codes.csv"
    cl.write_bytes(fx.codelist_bytes([("E00001", "上場", "有", "化学"), ("E00002", "上場", "有", "銀行業"),
                                      ("E00004", "上場", "有", "機械"), ("E00005", "非上場", "有", "化学")]))
    api = FakeEdinet()
    out = tmp_path / "jpbs_raw"
    summary = fe.run(["--out", str(out), "--start", "2025-06-27", "--end", "2026-06-26", "--codelist", str(cl),
                      "--target", "5", "--min-ifrs", "0", "--min-thousand-yen", "0", "--min-one-page", "0",
                      "--min-two-page", "0", "--sleep", "0"], session=api)
    ex = summary["exclusions_before_download"]
    assert ex["older_filing_same_company"] == 1 and ex["industry:銀行業"] == 1 and ex["not_listed"] == 1
    assert summary["accepted"] == 1
    assert summary["post_download_outcomes"]["excluded:bs_not_found_in_ixbrl"] == 1
    man = (out / "manifest.csv").read_text(encoding="utf-8-sig")
    assert "S1NEW" in man and "S1OLD" not in man
    fdir = out / "filings" / "S1NEW"
    assert pymupdf.open(fdir / "bs_pages.pdf").page_count == 2
    meta = json.loads((fdir / "meta.json").read_text(encoding="utf-8"))
    assert meta["unit"] == "千円" and meta["pages"] == "2" and meta["standard"] == "jgaap"
    assert not any((out / "_cache" / "docs").iterdir())                       # raw downloads deleted
    zpath = tmp_path / "jpbs_raw.zip"
    assert zpath.exists()
    with zipfile.ZipFile(zpath) as z:
        assert not any("_cache" in n for n in z.namelist())
        for n in z.namelist():
            assert KEY.encode() not in z.read(n)
    printed = capsys.readouterr()
    assert KEY not in printed.out and KEY not in printed.err


def test_prioritize_by_securities_code(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    cl = tmp_path / "codes.csv"
    cl.write_bytes(fx.codelist_bytes([("E00001", "上場", "有", "化学"), ("E00004", "上場", "有", "機械")]))
    pr = tmp_path / "ifrs.txt"
    pr.write_text("9999\n", encoding="utf-8")                           # matches secCode 99990
    s = fe.run(["--out", str(tmp_path / "o"), "--start", "2026-06-26", "--end", "2026-06-26", "--codelist", str(cl),
                "--prioritize", str(pr), "--list-only", "--sleep", "0"], session=FakeEdinet())
    assert s["prioritized_in_pool"] == 2 and s["pool_size"] == 2


def test_api_error_message_never_contains_key(tmp_path, monkeypatch):
    class Bad:
        def get(self, url, params=None, timeout=None):
            return _Resp(401, f'{{"StatusCode":401,"message":"invalid key {params["Subscription-Key"]}"}}'.encode())
    fe._SECRETS.append(KEY)
    c = fe.EdinetClient(fe.Secret(KEY), tmp_path, sleep=0, session=Bad())
    with pytest.raises(fe.EdinetError) as e:
        c.list_documents(fe.dt.date(2026, 6, 26))
    assert KEY not in str(e.value) and "***" in str(e.value)
    assert repr(fe.Secret(KEY)) == "Secret(***)"


# --------------------------------------------------------------------------- GT build + alignment
def _filing_dir(tmp_path) -> Path:
    """A filing folder as fetch_edinet writes it, with the real fixture page as bs_pages.pdf."""
    x = fe.extract_xbrl(_zip_file(tmp_path))
    d = tmp_path / "raw" / "filings" / "S1NEW"
    d.mkdir(parents=True)
    shutil.copy(FIXTURE_PDF, d / "bs_pages.pdf")
    (d / "ixbrl_bs.json").write_text(json.dumps(x["bs"], ensure_ascii=False), encoding="utf-8")
    (d / "cal_bs.json").write_text(json.dumps(x["cal"], ensure_ascii=False), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"docID": "S1NEW", "edinetCode": "E00001", "standard": "jgaap",
                                             "unit": "千円", "pages": "1", "industry": "化学",
                                             "pdf_page_range_0based": [0, 0]}, ensure_ascii=False), encoding="utf-8")
    return d


def test_gt_from_xbrl_reproduces_hand_ground_truth(tmp_path):
    doc, stats = gtb.build_filing(_filing_dir(tmp_path))
    hand = {r["label_path"]: r for r in fx.GT["rows"]}
    auto = {r["label_path"]: r for r in doc["rows"]}
    assert set(auto) == set(hand)
    for path, h in hand.items():
        a = auto[path]
        for pid, hc in h["cells"].items():
            ac = a["cells"][pid]
            assert (ac["reported_value"], ac["is_nil"], ac["is_negative"]) == \
                (hc["reported_value"], hc["is_nil"], hc["is_negative"]), path
        hp = fx.GT["rows"][int(h["calc_parent"][1:]) - 1]["label_path"] if h["calc_parent"] else None
        ap = doc["rows"][int(a["calc_parent"][1:]) - 1]["label_path"] if a["calc_parent"] else None
        assert hp == ap, path
        assert a["row_type"] == h["row_type"], path
    assert stats["xbrl_value_mismatch"] == 0 and stats["sign_differs_from_xbrl"] == 0
    assert stats["gt_checks_failed"] == 0 and stats["presentation_levels"] == 5


def test_alignment_keeps_matching_page_and_rejects_wrong_page(tmp_path):
    d = _filing_dir(tmp_path)
    doc, _ = gtb.build_filing(d)
    al = gtb.align(doc, d / "bs_pages.pdf")
    assert al["status"] == "kept" and al["match_rate"] == 1.0
    wrong = tmp_path / "wrong.pdf"
    wrong.write_bytes(fx.report_pdf())                               # different numbers
    pdf = pymupdf.open(wrong)
    pdf.select([3])
    pdf.save(tmp_path / "wrong1.pdf")
    al2 = gtb.align(doc, tmp_path / "wrong1.pdf")
    assert al2["status"] == "excluded_alignment" and al2["match_rate"] < 0.2


def test_alignment_unverifiable_when_text_layer_fails(tmp_path):
    d = _filing_dir(tmp_path)
    doc, _ = gtb.build_filing(d)
    src = pymupdf.open(d / "bs_pages.pdf")
    pix = src[0].get_pixmap(dpi=100)
    scan = pymupdf.open()
    p = scan.new_page(width=src[0].rect.width, height=src[0].rect.height)
    p.insert_image(p.rect, pixmap=pix)
    scan.save(tmp_path / "scan.pdf")
    assert gtb.align(doc, tmp_path / "scan.pdf")["status"] == "unverifiable_text_layer"


def test_gt_driver_writes_report(tmp_path):
    _filing_dir(tmp_path)
    rep = gtb.main(str(tmp_path / "raw"), str(tmp_path / "gt"))
    assert rep[0]["alignment_status"] == "kept"
    assert (tmp_path / "gt" / "S1NEW" / "ground_truth.long.csv").exists()
    assert json.loads((tmp_path / "gt" / "gt_summary.json").read_text())["kept_rate"] == 1.0
