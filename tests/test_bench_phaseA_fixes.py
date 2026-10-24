"""Phase A review fixes: inline-XBRL conventions, crash resilience, resume, pilot -> dev, end-total check,
PDF prioritize list, IFRS cap, skipping the PDF download when no quota is open."""
import csv
import json
import sys
from pathlib import Path

import pymupdf
import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "tests"))

import bench_fixture as fx  # noqa: E402
import build_gt_xbrl as gtb  # noqa: E402
import fetch_edinet as fe  # noqa: E402
import split as sp  # noqa: E402

KEY = "SECRET-KEY-xyz789-DO-NOT-LEAK"
XHTML = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
         'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><body><table>{rows}</table></body></html>')
CTX = {"CurrentYearInstant": {"instant": "2026-03-31", "start": None, "end": None, "dims": []}}


def _cells(rows_html: str):
    root = fe._parse(XHTML.format(rows=rows_html).encode())
    return [td for td in root.iter("{http://www.w3.org/1999/xhtml}td")]


def _nf(td):
    return next(td.iter("{http://www.xbrl.org/2013/inlineXBRL}nonFraction"))


def _cell_dict(td):
    return {"text": fe._cell_text(td), "facts": [fe._fact(nf, CTX) for nf in
                                                 td.iter("{http://www.xbrl.org/2013/inlineXBRL}nonFraction")]}


# --------------------------------------------------------------------------- inline-XBRL conventions
def test_dash_tagged_as_fixed_zero():
    td = _cells('<tr><td><p><ix:nonFraction name="jppfs_cor:ConstructionInProgress" contextRef="CurrentYearInstant" '
                'unitRef="JPY" decimals="-6" scale="6" format="ixt:fixed-zero">－</ix:nonFraction></p></td></tr>')[0]
    f = fe._fact(_nf(td), CTX)
    assert f["value"] == 0 and f["text"] == "－" and not f["nil"]
    c = gtb.parse_cell(_cell_dict(td), 6)
    assert c["is_nil"] and c["reported_value"] is None                 # page convention: nil, not 0
    assert c["xbrl_convention"] == "dash_tagged_zero" and c["xbrl_consistent"]


def test_contra_with_triangle_outside_tag_and_positive_fact():
    td = _cells('<tr><td><p>△<ix:nonFraction name="jppfs_cor:AccumulatedDepreciationBuildingsAndStructures" '
                'contextRef="CurrentYearInstant" unitRef="JPY" decimals="-6" scale="6" '
                'format="ixt:num-dot-decimal">1,640</ix:nonFraction></p></td></tr>')[0]
    f = fe._fact(_nf(td), CTX)
    assert f["value"] == 1_640_000_000                                 # no sign attribute: stored positive
    assert fe._cell_text(td) == "△1,640"
    c = gtb.parse_cell(_cell_dict(td), 6)
    assert c["reported_value"] == -1640 and c["is_negative"]           # sign from the page
    assert c["xbrl_convention"] == "printed_negative_xbrl_positive" and c["xbrl_consistent"]


def test_sign_minus_attribute_gives_negative_fact():
    td = _cells('<tr><td><p>△<ix:nonFraction name="jppfs_cor:AllowanceForDoubtfulAccountsCA" '
                'contextRef="CurrentYearInstant" unitRef="JPY" decimals="-3" scale="3" sign="-" '
                'format="ixt:num-dot-decimal">2,979</ix:nonFraction></p></td></tr>')[0]
    assert fe._fact(_nf(td), CTX)["value"] == -2_979_000
    c = gtb.parse_cell(_cell_dict(td), 3)
    assert c["reported_value"] == -2979 and c["xbrl_convention"] == "printed_negative_xbrl_negative"


def test_untagged_dash_and_nil_tag():
    tds = _cells('<tr><td><p>－</p></td><td><p>―<ix:nonFraction name="x:Y" contextRef="CurrentYearInstant" '
                 'unitRef="JPY" scale="3" xsi:nil="true"/></p></td></tr>')
    assert gtb.parse_cell(_cell_dict(tds[0]), 3)["xbrl_convention"] == "dash_untagged"
    assert gtb.parse_cell(_cell_dict(tds[1]), 3)["xbrl_convention"] == "dash_tagged_nil"


# --------------------------------------------------------------------------- fake EDINET
class _Resp:
    def __init__(self, status=200, content=b"", ctype="application/json"):
        self.status_code, self.content, self.headers = status, content, {"Content-Type": ctype}

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)


DOCS = [("S1", "E00001", "化学"), ("S2", "E00002", "機械"), ("S3", "E00003", "電気機器"),
        ("S4", "E00004", "食料品"), ("S5", "E00005", "化学")]


class FakeAPI:
    def __init__(self, zips=None, interrupt_on_doc_number=None):
        self.calls, self.zips, self.seen = [], zips or {}, []
        self.interrupt = interrupt_on_doc_number
        base = {"docTypeCode": "120", "formCode": "030000", "withdrawalStatus": "0", "xbrlFlag": "1", "pdfFlag": "1",
                "periodStart": "2025-04-01", "periodEnd": "2026-03-31", "submitDateTime": "2026-06-26 09:00"}
        self.results = [base | {"docID": d, "edinetCode": c, "secCode": f"{1000 + i}0", "filerName": f"社{i}"}
                        for i, (d, c, _) in enumerate(DOCS)]

    def get(self, url, params=None, timeout=None):
        assert params.get("Subscription-Key") == KEY
        self.calls.append((url, dict(params)))
        if url.endswith("/documents.json"):
            res = self.results if params["date"] == "2026-06-26" else []
            return _Resp(200, json.dumps({"metadata": {}, "results": res}).encode())
        did = url.rsplit("/", 1)[-1]
        if did not in self.seen:
            self.seen.append(did)
            if self.interrupt and len(self.seen) == self.interrupt:
                raise KeyboardInterrupt("simulated Colab disconnect")
        if params["type"] == 1:
            return _Resp(200, self.zips.get(did) or fx.xbrl_zip(), "application/octet-stream")
        return _Resp(200, fx.report_pdf(), "application/pdf")

    def downloads(self):
        return [u.rsplit("/", 1)[-1] for u, p in self.calls if "/documents/" in u]


def _codelist(tmp_path):
    p = tmp_path / "codes.csv"
    p.write_bytes(fx.codelist_bytes([(c, "上場", "有", ind) for _, c, ind in DOCS]))
    return p


def _args(tmp_path, out="raw", target="2", extra=()):
    return ["--out", str(tmp_path / out), "--start", "2026-06-26", "--end", "2026-06-26",
            "--codelist", str(_codelist(tmp_path)), "--target", target, "--min-ifrs", "0", "--min-thousand-yen", "0",
            "--min-one-page", "0", "--min-two-page", "0", "--sleep", "0", "--no-zip", *extra]


def _log(tmp_path, out="raw"):
    with open(tmp_path / out / "candidates_log.csv", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _manifest(tmp_path, out="raw"):
    with open(tmp_path / out / "manifest.csv", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- crash resilience
def test_any_exception_in_one_filing_is_logged_and_run_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    real = fe.extract_xbrl
    order = []

    def flaky(zpath):
        did = zpath.name.split("_")[0]
        order.append(did)
        k = len(order)
        if k == 1:
            etree.fromstring(b"<unclosed", etree.XMLParser())          # lxml XMLSyntaxError
        if k == 2:
            return {}["missing"]                                        # KeyError
        if k == 3:
            return None.attr                                            # AttributeError
        return real(zpath)
    monkeypatch.setattr(fe, "extract_xbrl", flaky)
    s = fe.run(_args(tmp_path, target="1"), session=FakeAPI())
    log = _log(tmp_path)
    errs = [r["reason"].split(":")[0] for r in log if r["decision"] == "error"]
    assert errs == ["XMLSyntaxError", "KeyError", "AttributeError"]
    assert s["accepted"] == 1 and log[-1]["decision"] == "accepted"
    assert all(KEY not in r["reason"] for r in log)


# --------------------------------------------------------------------------- resume
def test_interrupted_run_resumes_without_reprocessing(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    api1 = FakeAPI(interrupt_on_doc_number=3)                          # dies while starting the 3rd filing
    with pytest.raises(KeyboardInterrupt):
        fe.run(_args(tmp_path, target="3"), session=api1)
    first_two = api1.seen[:2]
    assert [r["docID"] for r in _log(tmp_path)] == first_two
    assert len(_manifest(tmp_path)) == 2
    api2 = FakeAPI()
    s = fe.run(_args(tmp_path, target="3"), session=api2)
    assert not set(first_two) & set(api2.downloads())                  # not downloaded again
    ids = [r["docID"] for r in _log(tmp_path)]
    assert len(ids) == len(set(ids)) == 3 and s["accepted"] == 3
    assert len({m["docID"] for m in _manifest(tmp_path)}) == 3


def test_crash_between_manifest_and_log_is_repaired(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    fe.run(_args(tmp_path, target="1"), session=FakeAPI())
    logf = tmp_path / "raw" / "candidates_log.csv"
    lines = logf.read_text(encoding="utf-8-sig").splitlines()
    logf.write_text("﻿" + lines[0] + "\n", encoding="utf-8")       # accepted row lost from the log only
    api = FakeAPI()
    fe.run(_args(tmp_path, target="1"), session=api)
    assert api.downloads() == []
    assert _log(tmp_path)[0]["reason"] == "accepted(recovered)"


def test_resume_refuses_changed_selection_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    fe.run(_args(tmp_path, target="1"), session=FakeAPI())
    with pytest.raises(SystemExit, match="different selection arguments"):
        fe.run(_args(tmp_path, target="2"), session=FakeAPI())


def test_resume_reuses_stored_end_date(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    args = _args(tmp_path, target="1")
    fe.run(args, session=FakeAPI())
    i = args.index("--end")
    no_end = args[:i] + args[i + 2:]                                   # restart on a later day, no --end
    fe.run(no_end, session=FakeAPI())                                  # no "different arguments" error
    st = json.loads((tmp_path / "raw" / "run_state.json").read_text(encoding="utf-8"))
    assert st["selection"]["end"] == "2026-06-26"


# --------------------------------------------------------------------------- pilot -> dev
def test_pilot_companies_are_forced_into_dev(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    fe.run(_args(tmp_path, out="pilot", target="2", extra=("--pilot",)), session=FakeAPI())
    run_list = tmp_path / "pilot" / "forced_dev.txt"
    pilot_codes = [l.split(",")[0] for l in run_list.read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
    assert len(pilot_codes) == 2 and all(m["pilot"] == "True" for m in _manifest(tmp_path, "pilot"))
    monkeypatch.setattr(sp, "FORCED_DEV_FILE", tmp_path / "forced_dev.txt")
    assert sp.force_dev(str(run_list)) == 2 and sp.force_dev(str(run_list)) == 0     # append-only, idempotent
    forced = sp.load_forced_dev(tmp_path / "forced_dev.txt")
    assert all(sp.split_of(c, forced) == "dev" for c in pilot_codes)
    test_code = next(f"E{n:05d}" for n in range(1, 999) if sp.hash_split(f"E{n:05d}") == "test")
    assert sp.split_of(test_code, {test_code}) == "dev" and sp.split_of(test_code, set()) == "test"


# --------------------------------------------------------------------------- end-total check
def test_end_total_not_on_page_is_excluded_and_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    zips = {"S1": fx.xbrl_zip(liab_total="9,999,999"), "S2": fx.xbrl_zip(liab_total="5,135,249"),
            "S3": fx.xbrl_zip(liab_total="9,999,999"), "S4": fx.xbrl_zip(liab_total="5,135,249"),
            "S5": fx.xbrl_zip(liab_total="5,135,249")}
    s = fe.run(_args(tmp_path, target="5"), session=FakeAPI(zips=zips))
    log = {r["docID"]: r for r in _log(tmp_path)}
    assert log["S1"]["reason"] == "bs_end_total_not_on_page" and log["S1"]["end_total_check"] == "false"
    assert log["S2"]["decision"] == "accepted" and log["S2"]["end_total_check"] == "true"
    assert s["end_total_not_on_page_excluded"] == 2 and s["end_total_check_counts"]["true"] == 3
    m = {r["docID"]: r for r in _manifest(tmp_path)}
    assert m["S2"]["end_total_value_on_last_page"] == "True"


# --------------------------------------------------------------------------- prioritize, quotas
def test_prioritize_reads_jpx_style_pdf(tmp_path):
    doc = pymupdf.open()
    p = doc.new_page()
    for k, line in enumerate(["IFRSを適用している会社一覧", "コード 会社名 市場区分", "7203 トヨタ自動車 プライム",
                              "130A サンプル プライム", "E01234"]):
        p.insert_text((50, 60 + 14 * k), line, fontname="japan", fontsize=9)
    doc.save(tmp_path / "tekiyou.pdf")
    codes, secs = fe.load_prioritize(str(tmp_path / "tekiyou.pdf"))
    assert {"7203", "130A"} <= secs and codes == {"E01234"}
    (tmp_path / "extra.txt").write_text("6758\n", encoding="utf-8")
    _, both = fe.load_prioritize(f"{tmp_path / 'tekiyou.pdf'},{tmp_path / 'extra.txt'}")
    assert {"7203", "130A", "6758"} <= both


def test_ifrs_cap_stops_prioritized_ifrs_from_dominating():
    s = fe.Sampler(10, {("standard", "ifrs"): 2}, industry_cap=99, maximums={("standard", "ifrs"): 3})
    ifrs = {"standard": "ifrs", "unit": "百万円", "pages": "2", "industry": "化学"}
    assert [s.offer(ifrs)[0] for _ in range(5)] == [True, True, True, False, False]
    assert s.offer(ifrs)[1] == "max_standard_ifrs"


def test_no_pdf_download_when_no_quota_is_open(tmp_path, monkeypatch):
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    args = _args(tmp_path, target="1")
    args[args.index("--min-ifrs") + 1] = "1"                           # the only slot is reserved for IFRS
    api = FakeAPI()
    fe.run(args, session=api)
    assert all(r["reason"] == "no_open_quota_for_strata" for r in _log(tmp_path))
    assert not [c for c in api.calls if "/documents/" in c[0] and c[1].get("type") == 2]   # no PDF downloaded


# --------------------------------------------------------------------------- alignment: header substring trap
def test_header_label_substring_does_not_skip_rows(tmp_path):
    """A header 流動資産 that is absent from the page must not match inside 流動資産合計 and skip the rows."""
    import shutil as _sh
    d = tmp_path / "f"
    d.mkdir()
    x = fe.extract_xbrl(_zip(tmp_path))
    (d / "ixbrl_bs.json").write_text(json.dumps(x["bs"], ensure_ascii=False), encoding="utf-8")
    (d / "cal_bs.json").write_text(json.dumps(x["cal"], ensure_ascii=False), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"docID": "X", "standard": "jgaap", "unit": "千円"}), encoding="utf-8")
    pdf = pymupdf.open(stream=fx.report_pdf())                         # value rows only, no header rows
    pdf.select([1, 2])
    pdf.save(d / "bs_pages.pdf")
    doc, _ = gtb.build_filing(d)
    al = gtb.align(doc, d / "bs_pages.pdf")
    assert al["match_rate"] == 1.0 and al["header_rows_found"] < al["header_rows"]


def _zip(tmp_path):
    p = tmp_path / "doc_1.zip"
    p.write_bytes(fx.xbrl_zip())
    return p


def test_check_pilot_on_synthetic_pilot(tmp_path, monkeypatch):
    import check_pilot as cp
    monkeypatch.setenv("EDINET_API_KEY", KEY)
    zips = {d: fx.xbrl_zip(liab_total="5,135,249") for d, _, _ in DOCS}
    args = [a for a in _args(tmp_path, out="jpbs_pilot", target="3", extra=("--pilot",)) if a != "--no-zip"]
    fe.run(args, session=FakeAPI(zips=zips))
    res = cp.check(str(tmp_path / "jpbs_pilot.zip"), str(tmp_path / "report.md"))
    assert res["run"]["problems"] == [] and res["run"]["pilot_all_in_forced_dev"]
    assert set(res["run"]["pilot_split_with_forced_list"].values()) == {"dev"}
    f = res["filings"][0]
    assert f["found_by"] == "heading" and f["extra_pages"] == [] and f["fact_vs_printed_mismatch"] == 0
    assert f["first_page_has_title"] and f["last_page_has_total_label"] and f["match_rate"] == 1.0
    assert "printed_negative_xbrl_negative" in res["run"]["conventions_total"]


# --------------------------------------------------------------------------- triage into dev / sealed test
def _dl_zip(path, doc_id, code):
    import io as _io
    import zipfile as _zf
    buf = _io.BytesIO()
    with _zf.ZipFile(buf, "w") as z:
        z.writestr("XbrlSearchDlInfo.csv", ("ダウンロードデータ作成日,2026年10月25日現在,件数,1件,\r\n"
                                            "書類管理番号,書類名,EDINET(ファンド)コード,提出者(ファンド),提出者法人番号\r\n"
                                            f"{doc_id},有価証券報告書,{code},テスト社,1\r\n").encode("cp932"))
        z.writestr(f"{doc_id}/XBRL/PublicDoc/x.htm", "<html/>")
    path.write_bytes(buf.getvalue())


def test_triage_seals_test_filings_unopened(tmp_path, monkeypatch):
    import triage as tr
    test_code = next(f"E{n:05d}" for n in range(1, 999) if sp.hash_split(f"E{n:05d}") == "test")
    dev_code = next(f"E{n:05d}" for n in range(1, 999) if sp.hash_split(f"E{n:05d}") == "dev")
    dl = tmp_path / "downloads"
    dl.mkdir()
    _dl_zip(dl / "a.zip", "S100TEST", test_code)
    _dl_zip(dl / "b.zip", "S100DEVV", dev_code)
    doc = pymupdf.open()
    doc.new_page()
    doc.save(dl / "S100TEST.pdf")
    monkeypatch.setattr(tr, "SEALED_RECORD", tmp_path / "SEALED_TEST.json")
    monkeypatch.setattr(tr, "FREEZE_LOG", tmp_path / "FREEZE_LOG.txt")
    monkeypatch.setattr(sp, "FORCED_DEV_FILE", tmp_path / "none.txt")
    s = tr.triage(str(dl), str(tmp_path / "realdocs"))
    assert [e["docID"] for e in s["dev"]] == ["S100DEVV"]
    assert [e["docID"] for e in s["test_sealed"]] == ["S100TEST"] and s["test_sealed"][0]["has_pdf"]
    sealed = tmp_path / "realdocs" / "sealed" / "S100TEST"
    assert sorted(p.name for p in sealed.iterdir()) == ["S100TEST.pdf", "S100TEST_xbrl.zip"]
    assert not (dl / "a.zip").exists() and (dl / "b.zip").exists()       # test moved out, dev copied
    rec = json.loads((tmp_path / "SEALED_TEST.json").read_text(encoding="utf-8"))
    assert set(rec["sealed"]["S100TEST"]["sha256"]) == {"S100TEST.pdf", "S100TEST_xbrl.zip"}
    assert "SEALED" in (tmp_path / "FREEZE_LOG.txt").read_text(encoding="utf-8")
