"""Synthetic EDINET filing built from the hand-verified ground truth of the fixture page.

Mimics the structure fetch_edinet.py expects: XBRL/PublicDoc/*_ixbrl.htm with ix:resources
contexts, a 【連結貸借対照表】 heading followed by the table (△ printed outside the ix tag, sign="-"),
a decoy non-consolidated 【貸借対照表】 table, a notes heading, a DEI fact, and a _cal.xml linkbase.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
GT = json.loads((ROOT / "gt" / "out" / "ground_truth.json").read_text(encoding="utf-8"))
CTX = {"prior": "Prior1YearInstant", "current": "CurrentYearInstant"}


def _elem(row) -> str | None:
    return f"jppfs_cor:{row['xbrl_candidate']}" if row.get("xbrl_candidate") and row["cells"] else None


def ixbrl_html(standard: str = "Japan GAAP", nil_tagged: bool = True, title: str = "連結貸借対照表",
               liab_total: str | None = None) -> str:
    rows_html = []
    for r in GT["rows"]:
        pad = 10.5 * r["presentation_level"]
        tds = [f'<td style="padding-left:{pad}pt"><p>{escape(r["label_ja"])}</p></td>']
        for pid in ("prior", "current"):
            c = r["cells"].get(pid)
            if c is None:
                tds.append("<td></td>")
            elif c["is_nil"]:
                if nil_tagged:
                    tds.append(f'<td><p>－<ix:nonFraction name="{_elem(r)}" contextRef="{CTX[pid]}" unitRef="JPY" '
                               f'decimals="-3" scale="3" xsi:nil="true"/></p></td>')
                else:
                    tds.append("<td><p>－</p></td>")
            else:
                mag = abs(c["reported_value"])
                sign = ' sign="-"' if c["is_negative"] else ""
                tds.append(f'<td><p>{"△" if c["is_negative"] else ""}<ix:nonFraction name="{_elem(r)}" '
                           f'contextRef="{CTX[pid]}" unitRef="JPY" decimals="-3" scale="3"{sign} '
                           f'format="ixt:num-dot-decimal">{mag:,}</ix:nonFraction></p></td>')
        rows_html.append("<tr>" + "".join(tds) + "</tr>")
    if liab_total:                       # optional 負債純資産合計 row (for the end-of-range check)
        rows_html.append(f'<tr><td><p>負債純資産合計</p></td><td></td><td><p><ix:nonFraction '
                         f'name="jppfs_cor:LiabilitiesAndNetAssets" contextRef="CurrentYearInstant" unitRef="JPY" '
                         f'decimals="-3" scale="3">{liab_total}</ix:nonFraction></p></td></tr>')
    contexts = "".join(
        f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">'
        f'E99999-000</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>{date}</xbrli:instant>'
        f'</xbrli:period></xbrli:context>'
        for cid, date in (("Prior1YearInstant", "2025-03-31"), ("CurrentYearInstant", "2026-03-31")))
    contexts += ('<xbrli:context id="CurrentYearInstant_NonConsolidatedMember"><xbrli:entity><xbrli:identifier '
                 'scheme="http://disclosure.edinet-fsa.go.jp">E99999-000</xbrli:identifier><xbrli:segment>'
                 '<xbrldi:explicitMember dimension="jppfs_cor:ConsolidatedOrNonConsolidatedAxis">'
                 'jppfs_cor:NonConsolidatedMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>'
                 '<xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period></xbrli:context>')
    decoy = "".join(
        f'<tr><td><p>項目{k}</p></td><td><p><ix:nonFraction name="jppfs_cor:Assets" '
        f'contextRef="CurrentYearInstant_NonConsolidatedMember" unitRef="JPY" decimals="-3" scale="3">{k},000'
        f'</ix:nonFraction></p></td></tr>' for k in range(1, 9))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:jppfs_cor="x" xmlns:jpdei_cor="y">
<head><title>t</title></head><body>
<div style="display:none"><ix:header><ix:resources>{contexts}</ix:resources></ix:header></div>
<p><ix:nonNumeric name="jpdei_cor:AccountingStandardsDEI" contextRef="CurrentYearInstant">{standard}</ix:nonNumeric></p>
<p>１【連結財務諸表等】</p>
<p>（１）【連結財務諸表】</p>
<p>①【{title}】</p>
<p>（単位：千円）</p>
<table><tr><td></td><td><p>前連結会計年度</p><p>(2025年３月31日)</p></td><td><p>当連結会計年度</p><p>(2026年３月31日)</p></td></tr>
{"".join(rows_html)}</table>
<p>②【連結損益計算書及び連結包括利益計算書】</p>
<table><tr><td><p>売上高</p></td><td><p>9,999</p></td></tr></table>
<p>（連結貸借対照表関係）</p>
<p>２【財務諸表等】</p>
<p>①【貸借対照表】</p>
<table>{decoy}</table>
</body></html>"""


def cal_xml() -> str:
    by_id = {r["row_id"]: r for r in GT["rows"]}
    locs, arcs, seen = [], [], set()
    for r in GT["rows"]:
        if not r.get("calc_parent") or not _elem(r):
            continue
        p = by_id[r["calc_parent"]]
        for e in (_elem(p), _elem(r)):
            lid = e.replace(":", "_")
            if lid not in seen:
                seen.add(lid)
                locs.append(f'<link:loc xlink:type="locator" xlink:href="jppfs_cor_2025-11-01.xsd#{lid}" xlink:label="{lid}"/>')
        arcs.append(f'<link:calculationArc xlink:type="arc" xlink:arcrole="http://www.xbrl.org/2003/arcrole/summation-item" '
                    f'xlink:from="{_elem(p).replace(":", "_")}" xlink:to="{_elem(r).replace(":", "_")}" weight="1" order="1"/>')
    return f"""<?xml version="1.0" encoding="utf-8"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">
<link:calculationLink xlink:type="extended" xlink:role="http://disclosure.edinet-fsa.go.jp/role/jppfs/rol_ConsolidatedBalanceSheet">
{"".join(locs)}{"".join(arcs)}
</link:calculationLink>
<link:calculationLink xlink:type="extended" xlink:role="http://disclosure.edinet-fsa.go.jp/role/jppfs/rol_ConsolidatedStatementOfIncome">
</link:calculationLink>
</link:linkbase>"""


def xbrl_zip(standard: str = "Japan GAAP", with_bs: bool = True, title: str = "連結貸借対照表",
             liab_total: str | None = None) -> bytes:
    buf = io.BytesIO()
    base = "XBRL/PublicDoc/"
    with zipfile.ZipFile(buf, "w") as z:
        full = ixbrl_html(standard, title=title, liab_total=liab_total)
        html = full if with_bs else full.split(f"<p>①【{title}】</p>")[0] + "</body></html>"
        z.writestr(base + "0105010_honbun_jpcrp030000-asr-001_E99999-000_2026-03-31_01_2026-06-26_ixbrl.htm", html)
        z.writestr(base + "jpcrp030000-asr-001_E99999-000_2026-03-31_01_2026-06-26_cal.xml", cal_xml())
        z.writestr("XBRL/AuditDoc/ignored.htm", "<html/>")
    return buf.getvalue()


def report_pdf() -> bytes:
    """A small 有報-like PDF: TOC page, two balance-sheet pages, then the income statement."""
    doc = pymupdf.open()

    def page(lines):
        p = doc.new_page()
        y = 60
        for t in lines:
            p.insert_text((50, y), t, fontname="japan", fontsize=9)
            y += 13

    page(["目次", "①【連結貸借対照表】 ・・・ 80", "②【連結損益計算書】 ・・・ 82"])
    rows = [r for r in GT["rows"] if r["cells"]]
    page(["①【連結貸借対照表】", "（単位：千円）"] +
         [f"{r['label_ja']}   {r['cells'].get('prior', {}).get('raw_text', '')}   {r['cells'].get('current', {}).get('raw_text', '')}"
          for r in rows[:30]])
    page([f"{r['label_ja']}   {r['cells'].get('prior', {}).get('raw_text', '')}   {r['cells'].get('current', {}).get('raw_text', '')}"
          for r in rows[30:]] + ["負債純資産合計   7,534,110   5,135,249"])
    page(["②【連結損益計算書】", "売上高   1,000,000   2,000,000"] + [f"費用{k}   {k},000   {k},100" for k in range(1, 9)])
    return doc.tobytes()


CODELIST_HEADER = ["ＥＤＩＮＥＴコード", "提出者種別", "上場区分", "連結の有無", "資本金", "決算日", "提出者名",
                   "提出者名（英字）", "提出者名（ヨミ）", "所在地", "提出者業種", "証券コード", "提出者法人番号"]


def codelist_bytes(entries: list[tuple[str, str, str, str]]) -> bytes:
    """entries: (edinetCode, listed, consolidated, industry) -> cp932 CSV like EdinetcodeDlInfo.csv."""
    lines = ["ダウンロード実行日,2026年10月24日現在,件数,5件", ",".join(f'"{h}"' for h in CODELIST_HEADER)]
    for code, listed, cons, ind in entries:
        vals = [code, "内国法人・組合", listed, cons, "100", "3月31日", f"テスト{code}", "Test", "テスト", "東京都",
                ind, "99990", "1234567890123"]
        lines.append(",".join(f'"{v}"' for v in vals))
    return ("\r\n".join(lines) + "\r\n").encode("cp932")
