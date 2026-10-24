#!/usr/bin/env python3
"""JP-BS-Bench, Phase A: consolidated balance-sheet pages + XBRL from EDINET filings.

LOCAL mode (no API key): hand-downloaded filings from the public EDINET site, one XBRL zip + one
annual-report PDF per filing:

    python fetch_edinet.py --local downloads/ --out jpbs_local --pilot

API mode (optional, needs an EDINET API v2 key and access to api.edinet-fsa.go.jp):

    export EDINET_API_KEY=...            # never printed, never written to disk
    python fetch_edinet.py --out jpbs_raw --list-only      # step 1: candidate pool sizes (cheap)
    python fetch_edinet.py --out jpbs_raw                  # step 2: sample, download, extract, zip

What it does
  1. Lists all documents per submission date (documents.json, type=2), cached per day.
  2. Keeps 有価証券報告書 (docTypeCode 120, formCode 030000) of listed companies with consolidated
     statements, fiscal year ending in --years, XBRL and PDF available, not withdrawn.
     One filing per company (the latest).
  3. Excludes banks, insurers (and securities firms unless --keep-securities) by the EDINET code
     list industry; every exclusion is logged with its reason.
  4. Visits candidates in a fixed pseudo-random order (sha256 of seed + docID), downloads the
     XBRL zip (type=1) and PDF (type=2), extracts the consolidated balance sheet from the inline
     XBRL, finds its pages in the PDF, and fills stratified quotas (IFRS / J-GAAP, 千円 / 百万円,
     one- / two-page, industry cap).
  5. Writes, per accepted filing: bs_pages.pdf (balance-sheet pages only), ixbrl_bs.json (table
     rows exactly as in the inline XBRL, with every tagged fact), contexts.json, dei.json,
     cal_bs.json / pre_bs.json (calculation / presentation linkbase arcs for balance-sheet roles),
     meta.json. Plus manifest.csv, candidates_log.csv, exclusions_summary.json, run_info.json,
     and a zip of all of it (raw downloads are deleted unless --keep-raw).

Dependencies: requests, pymupdf, lxml (pip install requests pymupdf lxml).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
import requests
from lxml import etree

SCRIPT_VERSION = "jpbs-fetch-1.1"
API = "https://api.edinet-fsa.go.jp/api/v2"
CODELIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"

NS = {
    "ix": "http://www.xbrl.org/2013/inlineXBRL",
    "xbrli": "http://www.xbrl.org/2003/instance",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
    "link": "http://www.xbrl.org/2003/linkbase",
    "xlink": "http://www.w3.org/1999/xlink",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
XL = "{%s}" % NS["xlink"]
# EDINET files use the 2008 inline-XBRL namespace (seen on real filings); 2013 is the current spec.
IX_NAMESPACES = ("http://www.xbrl.org/2008/inlineXBRL", "http://www.xbrl.org/2013/inlineXBRL")
NONFRACTION_TAGS = tuple("{%s}nonFraction" % ns for ns in IX_NAMESPACES)
# Note markers printed inside cells, e.g. 「※1 65,180」, 「※2,※3」, 「※１、２」
NOTE_RE = re.compile(r"[※＊*]\s*[0-9０-９]{1,2}(?:\s*[,，、・]\s*[※＊*]?\s*[0-9０-９]{1,2})*")


def nonfractions(el):
    """ix:nonFraction elements under `el`, in document order, for either inline-XBRL namespace."""
    return el.iter(*NONFRACTION_TAGS)


def split_notes(text: str) -> tuple[str, list[str]]:
    """'※1 65,180' -> ('65,180', ['※1']). Marker numbers are kept as refs, never as digits."""
    refs = ["※" + n for m in NOTE_RE.finditer(text)
            for n in re.findall(r"\d+", unicodedata.normalize("NFKC", m.group(0)))]
    rest = re.sub(r"\s+", " ", NOTE_RE.sub(" ", text)).strip()
    return rest, refs

BS_TITLES = {"jgaap": ("連結貸借対照表",), "ifrs": ("連結財政状態計算書",)}
END_LABELS = ("負債純資産合計", "負債及び純資産合計", "負債・純資産合計", "負債純資産の部合計",
              "負債及び資本合計", "資本及び負債合計", "負債及び資本の合計", "資本合計及び負債合計", "負債・資本合計")
EXCLUDE_INDUSTRIES = ("銀行業", "保険業")
SECURITIES_INDUSTRY = "証券、商品先物取引業"
HEADING_RE = re.compile(r"【([^】]{2,60})】")
NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")


# ============================================================================ secrets & logging
class Secret:
    """Holds the API key; never shows it in repr/str."""

    def __init__(self, value: str):
        self._v = value

    def get(self) -> str:
        return self._v

    def __repr__(self):
        return "Secret(***)"

    __str__ = __repr__


_SECRETS: list[str] = []


def scrub(text: str) -> str:
    for s in _SECRETS:
        if s:
            text = text.replace(s, "***")
    return text


def log(msg: str) -> None:
    print(scrub(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"), flush=True)


def nfkc_nospace(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


# ============================================================================ HTTP client
class EdinetError(RuntimeError):
    pass


class EdinetClient:
    def __init__(self, key: Secret, cache: Path, sleep: float = 1.0, session=None, retries: int = 4):
        self.key, self.cache, self.sleep, self.retries = key, cache, sleep, retries
        self.s = session or requests.Session()
        (cache / "lists").mkdir(parents=True, exist_ok=True)
        (cache / "docs").mkdir(parents=True, exist_ok=True)
        self._last = 0.0

    def _get(self, url: str, params: dict, keyed: bool = True):
        p = dict(params)
        if keyed:
            p["Subscription-Key"] = self.key.get()
        err = None
        for attempt in range(self.retries):
            wait = self.sleep - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.s.get(url, params=p, timeout=120)
            except requests.RequestException as e:          # message may contain the full URL
                err = f"network error: {type(e).__name__}"
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                err = f"HTTP {r.status_code}"
                time.sleep(5 * 2 ** attempt)
                continue
            if r.status_code != 200:
                raise EdinetError(scrub(f"HTTP {r.status_code} for {url} (query hidden): {r.text[:200]}"))
            return r
        raise EdinetError(scrub(f"{err} for {url} after {self.retries} attempts"))

    def list_documents(self, date: dt.date) -> list[dict]:
        f = self.cache / "lists" / f"{date.isoformat()}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        r = self._get(f"{API}/documents.json", {"date": date.isoformat(), "type": 2})
        data = r.json()
        if "results" not in data:                           # API-level error (e.g. invalid key)
            raise EdinetError(scrub(f"documents.json {date}: {json.dumps(data, ensure_ascii=False)[:300]}"))
        res = data["results"] or []
        if date < dt.date.today():                          # past days are final; cache them
            f.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        return res

    def download(self, doc_id: str, typ: int) -> Path:
        ext = {1: "zip", 2: "pdf"}[typ]
        f = self.cache / "docs" / f"{doc_id}_{typ}.{ext}"
        if f.exists() and f.stat().st_size > 0:
            return f
        r = self._get(f"{API}/documents/{doc_id}", {"type": typ})
        ctype = r.headers.get("Content-Type", "")
        if "json" in ctype:
            raise EdinetError(scrub(f"download {doc_id} type {typ}: {r.text[:300]}"))
        if typ == 2 and not r.content.startswith(b"%PDF"):
            raise EdinetError(f"download {doc_id} type 2: not a PDF ({ctype})")
        if typ == 1 and not r.content.startswith(b"PK"):
            raise EdinetError(f"download {doc_id} type 1: not a zip ({ctype})")
        tmp = f.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.rename(f)
        return f


# ============================================================================ EDINET code list
def load_codelist(path: str | None, session=None) -> dict[str, dict]:
    """EdinetcodeDlInfo.csv (cp932; line 1 is a notice, line 2 the header). Columns are matched
    loosely (NFKC) so full-width headers such as 'ＥＤＩＮＥＴコード' work."""
    if path:
        raw = Path(path).read_bytes()
    else:
        try:
            r = (session or requests).get(CODELIST_URL, timeout=120)
            r.raise_for_status()
            raw = r.content
        except Exception as e:
            raise SystemExit(f"could not download the EDINET code list ({type(e).__name__}). Download it manually "
                             f"from {CODELIST_URL} (or the EDINET site: EDINETタクソノミ及びコードリスト) and pass "
                             f"--codelist Edinetcode.zip")
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            raw = z.read(name)
    text = raw.decode("cp932", errors="replace")
    lines = text.splitlines()
    rows = list(csv.reader(lines[1:]))
    header = [unicodedata.normalize("NFKC", h).strip() for h in rows[0]]

    def col(*keys):
        for i, h in enumerate(header):
            if all(k in h for k in keys):
                return i
        raise SystemExit(f"code list: no column matching {keys}; columns are {header}")

    i_code, i_ind, i_list, i_cons = col("EDINETコード"), col("業種"), col("上場区分"), col("連結")
    i_name, i_sec = col("提出者名"), col("証券コード")
    out = {}
    for r in rows[1:]:
        if len(r) <= max(i_code, i_ind, i_list, i_cons):
            continue
        out[r[i_code].strip()] = {"industry": r[i_ind].strip(), "listed": r[i_list].strip(),
                                  "consolidated": r[i_cons].strip(), "name": r[i_name].strip(),
                                  "sec_code": r[i_sec].strip()}
    return out


# ============================================================================ candidate selection
def candidate_filter(doc: dict, years: set[int], form_codes: set[str]) -> str | None:
    """Return None if `doc` is a candidate, else the exclusion reason."""
    if doc.get("docTypeCode") != "120":
        return "not_annual_report"
    if doc.get("formCode") not in form_codes:
        return "form_code"
    if doc.get("withdrawalStatus", "0") != "0":
        return "withdrawn"
    if doc.get("xbrlFlag") != "1" or doc.get("pdfFlag") != "1":
        return "no_xbrl_or_pdf"
    pe = doc.get("periodEnd") or ""
    if not pe or int(pe[:4]) not in years:
        return "fiscal_year_out_of_range"
    if not doc.get("edinetCode"):
        return "no_edinet_code"
    return None


def order_key(seed: str, doc_id: str) -> str:
    return hashlib.sha256(f"{seed}:{doc_id}".encode()).hexdigest()


# ============================================================================ XBRL extraction
def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _parse(data: bytes):
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    return etree.fromstring(data, parser)


def read_publicdoc(zip_path: Path) -> dict[str, bytes]:
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            low = n.lower()
            if "publicdoc/" in low and low.endswith((".htm", ".html", ".xhtml", ".xbrl", "_cal.xml", "_pre.xml")):
                out[n] = z.read(n)
    return out


def parse_contexts(roots) -> dict[str, dict]:
    ctx = {}
    for root in roots:
        for c in root.iter("{%s}context" % NS["xbrli"]):
            cid = c.get("id")
            if not cid or cid in ctx:
                continue
            inst = c.find(".//xbrli:instant", NS)
            start, end = c.find(".//xbrli:startDate", NS), c.find(".//xbrli:endDate", NS)
            dims = [(m.get("dimension"), (m.text or "").strip()) for m in c.iter("{%s}explicitMember" % NS["xbrldi"])]
            ctx[cid] = {"instant": inst.text.strip() if inst is not None and inst.text else None,
                        "start": start.text.strip() if start is not None and start.text else None,
                        "end": end.text.strip() if end is not None and end.text else None,
                        "dims": dims}
    return ctx


def parse_dei(roots) -> dict[str, str]:
    """All jpdei facts (local name -> text), from the instance and inline documents."""
    dei = {}
    for root in roots:
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            name = el.get("name") if _local(el.tag) in ("nonNumeric", "nonFraction") else None
            if name and name.startswith("jpdei_cor:"):
                dei.setdefault(name.split(":", 1)[1], "".join(el.itertext()).strip())
            elif el.tag.startswith("{") and "jpdei" in el.tag and len(el) == 0:
                dei.setdefault(_local(el.tag), (el.text or "").strip())
    return dei


def accounting_standard(dei: dict) -> str:
    v = nfkc_nospace(next((t for k, t in dei.items() if "AccountingStandards" in k), "")).lower()
    if "ifrs" in v:
        return "ifrs"
    if "us" in v and "gaap" in v:
        return "usgaap"
    if "japan" in v or "jgaap" in v or v == "":
        return "jgaap"
    return "other:" + v


def _cell_text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _indent(td) -> float:
    """Indent signal of a label cell: CSS padding/margin/text-indent (pt-ish) + leading spaces."""
    total = 0.0
    for el in [td, *td.iter()]:
        style = el.get("style") or ""
        for prop, val, unit in re.findall(r"(padding-left|margin-left|text-indent)\s*:\s*(-?[\d.]+)\s*(pt|px|em|mm)?", style):
            f = float(val) * {"pt": 1, "px": 0.75, "em": 10.5, "mm": 2.835, "": 1}[unit or ""]
            total += f
    txt = "".join(td.itertext())
    lead = len(txt) - len(txt.lstrip(" 　 "))
    return round(total + 10.5 * sum(1 for ch in txt[:lead] if ch == "　") + 2.6 * sum(1 for ch in txt[:lead] if ch != "　"), 2)


def _fact(nf, contexts) -> dict:
    nil = (nf.get("{%s}nil" % NS["xsi"]) or "").lower() == "true"
    text = "".join(nf.itertext()).strip()
    scale = int(nf.get("scale") or 0)
    value = None
    fmt = nf.get("format") or ""
    if not nil and fmt.split(":")[-1] == "fixed-zero":     # 「－」 tagged as an explicit zero
        value = 0
    elif not nil:
        digits = re.sub(r"[^\d.]", "", unicodedata.normalize("NFKC", text))
        if digits:
            num = float(digits) * 10 ** scale
            value = int(round(num)) if num == int(num) else num
            if nf.get("sign") == "-":
                value = -value
    c = contexts.get(nf.get("contextRef"), {})
    return {"name": nf.get("name"), "contextRef": nf.get("contextRef"), "unitRef": nf.get("unitRef"),
            "decimals": nf.get("decimals"), "scale": scale, "sign": nf.get("sign"), "format": fmt or None,
            "nil": nil, "text": text, "value": value, "instant": c.get("instant"), "dims": c.get("dims", [])}


def _tables_with_headings(root):
    """(table element, nearest preceding 【...】 heading outside tables), in document order."""
    out, heading, tables = [], None, set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el.tag) == "table":
            if not any(_local(a.tag) == "table" for a in el.iterancestors() if isinstance(a.tag, str)):
                out.append((el, heading))
                tables.add(el)
            continue
        if el.text and "【" in el.text:
            if any(a in tables for a in el.iterancestors()):
                continue
            m = HEADING_RE.search(nfkc_nospace(el.text).replace("[", "【").replace("]", "】"))
            m = m or HEADING_RE.search(el.text)
            if m:
                heading = nfkc_nospace(m.group(1))
    return out


def _is_bs_heading(h: str | None, std: str) -> bool:
    if not h or "関係" in h or "注記" in h:
        return False
    return any(nfkc_nospace(t) == h or h.endswith(nfkc_nospace(t)) for t in BS_TITLES["ifrs" if std == "ifrs" else "jgaap"])


def _consolidated_instant(f: dict) -> bool:
    return f["instant"] is not None and not f["dims"]


def extract_bs(files: dict[str, bytes], std: str, contexts: dict) -> dict | None:
    """Consolidated balance-sheet tables from the inline XBRL documents (rows as displayed)."""
    found = []
    for name in sorted(n for n in files if n.lower().endswith((".htm", ".html", ".xhtml"))):
        root = _parse(files[name])
        if root is None:
            continue
        pairs = _tables_with_headings(root)
        bs = [t for t, h in pairs if _is_bs_heading(h, std)]
        if bs:
            found.append((name, bs, "heading"))
            break
    if not found:                                     # fallback: tables holding consolidated BS totals
        hint = re.compile(r":(Assets|LiabilitiesAndNetAssets|AssetsIFRS|LiabilitiesAndEquityIFRS|EquityAndLiabilitiesIFRS)$")
        for name in sorted(n for n in files if n.lower().endswith((".htm", ".html", ".xhtml"))):
            root = _parse(files[name])
            if root is None:
                continue
            bs = []
            for t, _ in _tables_with_headings(root):
                for nf in nonfractions(t):
                    if hint.search(nf.get("name") or "") and _consolidated_instant(_fact(nf, contexts)):
                        bs.append(t)
                        break
            if bs:
                found.append((name, bs, "fact_fallback"))
                break
    if not found:
        return None
    name, tables, how = found[0]
    out_tables = []
    for ti, t in enumerate(tables):
        rows = []
        for tr in t.iter():
            if _local(tr.tag) != "tr":
                continue
            cells = [c for c in tr if isinstance(c.tag, str) and _local(c.tag) in ("td", "th")]
            if not cells:
                continue
            row = {"cells": [], "label": None, "label_cell": None, "lead_cols": None, "indent_css": None,
                   "label_notes": []}
            pos = 0
            for ci, td in enumerate(cells):
                facts = [_fact(nf, contexts) for nf in nonfractions(td)]
                text = _cell_text(td)
                value_text, notes = split_notes(text)
                span = int(td.get("colspan") or 1)
                row["cells"].append({"text": text, "value_text": value_text, "notes": notes, "colspan": span,
                                     "pos": pos, "indent": _indent(td) if not facts else None, "facts": facts})
                # label = first non-empty cell without facts; its grid position is the indent level
                # (EDINET indents by leaving leading columns empty, not with CSS padding)
                if row["label"] is None and text and not facts:
                    lbl, lnotes = split_notes(text)
                    row.update(label=lbl, label_cell=ci, lead_cols=pos, indent_css=_indent(td), label_notes=lnotes)
                pos += span
            if any(c["text"] for c in row["cells"]):
                rows.append(row)
        out_tables.append({"table_index": ti, "rows": rows})
    facts = [f for tb in out_tables for r in tb["rows"] for c in r["cells"] for f in c["facts"]]
    cons = [f for f in facts if _consolidated_instant(f)]
    if len(cons) < 6:
        return None
    scales = Counter(f["scale"] for f in cons)
    return {"source_file": name.split("/")[-1], "found_by": how, "tables": out_tables,
            "n_facts": len(facts), "n_consolidated_facts": len(cons),
            "scale": scales.most_common(1)[0][0], "instants": sorted({f["instant"] for f in cons})}


def parse_linkbase(data: bytes, kind: str) -> dict[str, list[dict]]:
    """Arcs of balance-sheet roles from a _cal.xml / _pre.xml linkbase. Element ids are the locator
    fragments (prefix_LocalName), comparable with fact names via name.replace(':', '_')."""
    root = _parse(data)
    out = {}
    link_tag, arc_tag = ("calculationLink", "calculationArc") if kind == "cal" else ("presentationLink", "presentationArc")
    for link in root.iter("{%s}%s" % (NS["link"], link_tag)):
        role = link.get(XL + "role") or ""
        if not re.search(r"BalanceSheet|FinancialPosition", role):
            continue
        locs = {l.get(XL + "label"): (l.get(XL + "href") or "").split("#")[-1]
                for l in link.iter("{%s}loc" % NS["link"])}
        arcs = []
        for a in link.iter("{%s}%s" % (NS["link"], arc_tag)):
            arcs.append({"from": locs.get(a.get(XL + "from")), "to": locs.get(a.get(XL + "to")),
                         "order": a.get("order"), "weight": a.get("weight"),
                         "preferredLabel": a.get("preferredLabel")})
        out.setdefault(role, []).extend(arcs)
    return out


def extract_xbrl(zip_path: Path) -> dict:
    files = read_publicdoc(zip_path)
    roots = []
    for n, b in files.items():
        if n.lower().endswith((".htm", ".html", ".xhtml", ".xbrl")):
            r = _parse(b)
            if r is not None:
                roots.append(r)
    contexts = parse_contexts(roots)
    dei = parse_dei(roots)
    std = accounting_standard(dei)
    bs = extract_bs(files, std, contexts)
    cal, pre = {}, {}
    for n, b in files.items():
        if n.lower().endswith("_cal.xml"):
            cal.update(parse_linkbase(b, "cal"))
        elif n.lower().endswith("_pre.xml"):
            pre.update(parse_linkbase(b, "pre"))
    industry_code = next((v for k, v in dei.items() if "IndustryCode" in k), None)
    return {"dei": dei, "standard": std, "industry_code_dei": industry_code, "contexts": contexts,
            "bs": bs, "cal": cal, "pre": pre}


# ============================================================================ PDF page finding
def find_bs_pages(pdf_path: Path, std: str, end_value_texts: list[str] | None = None, max_pages: int = 4):
    """(first, last) 0-based page indices of the consolidated balance sheet, or (None, reason).
    Start: first page with the 【title】 heading and >= 8 numbers (skips the table of contents).
    End: first page from the start that shows a liabilities+equity total label."""
    doc = pymupdf.open(pdf_path)
    texts = [nfkc_nospace(p.get_text()) for p in doc]
    titles = [nfkc_nospace(f"【{t}】") for t in BS_TITLES["ifrs" if std == "ifrs" else "jgaap"]]
    starts = [i for i, t in enumerate(texts) if any(x in t for x in titles) and len(NUM_RE.findall(t)) >= 8]
    if not starts:
        return None, "bs_heading_not_found_in_pdf", {}
    s = starts[0]
    for e in range(s, min(s + max_pages, len(texts))):
        if any(lbl in texts[e] for lbl in END_LABELS):
            check = None
            if end_value_texts:
                check = any(nfkc_nospace(v) in texts[e] for v in end_value_texts if v)
            return (s, e), None, {"end_total_value_on_last_page": check, "pdf_pages": len(texts)}
    return None, "bs_end_label_not_found", {"start": s}


def end_total_texts(bs: dict) -> list[str]:
    hint = re.compile(r":(LiabilitiesAndNetAssets|LiabilitiesAndEquityIFRS|EquityAndLiabilitiesIFRS)$")
    return [f["text"] for tb in bs["tables"] for r in tb["rows"] for c in r["cells"] for f in c["facts"]
            if hint.search(f["name"] or "") and _consolidated_instant(f)]


# ============================================================================ stratified sampler
@dataclass
class Sampler:
    total: int
    minimums: dict[tuple[str, str], int]
    industry_cap: int
    maximums: dict[tuple[str, str], int] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    accepted: int = 0

    def decide(self, strata: dict) -> tuple[bool, str]:
        if self.accepted >= self.total:
            return False, "quota_full"
        if self.counts[("industry", strata["industry"])] >= self.industry_cap:
            return False, "industry_cap"
        for k, n in self.maximums.items():
            if strata.get(k[0]) == k[1] and self.counts[k] >= n:
                return False, f"max_{k[0]}_{k[1]}"
        helps = [k for k, n in self.minimums.items() if strata.get(k[0]) == k[1] and self.counts[k] < n]
        deficit = sum(max(0, n - self.counts[k]) for k, n in self.minimums.items())
        free = self.total - self.accepted
        if not helps and free <= deficit:
            return False, "slots_reserved_for_minimum_strata"
        return True, "accepted"

    def restore(self, strata: dict) -> None:
        self.accepted += 1
        for dim, val in strata.items():
            self.counts[(dim, val)] += 1

    def offer(self, strata: dict) -> tuple[bool, str]:
        ok, why = self.decide(strata)
        if ok:
            self.restore(strata)
        return ok, why

    def could_accept(self, partial: dict) -> bool:
        """Would some page count make this filing acceptable? (decided before the PDF download)"""
        return any(self.decide(partial | {"pages": p})[0] for p in ("1", "2", "3+"))

    def shortfalls(self) -> dict:
        return {f"{k[0]}={k[1]}": n - self.counts[k] for k, n in self.minimums.items() if self.counts[k] < n}


# ============================================================================ main
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--local", default=None, metavar="DIR",
                    help="process hand-downloaded filings in DIR (XBRL zip + PDF per filing; no API key, no "
                         "download, no sampling). Same extraction, page finding and outputs as the API path.")
    ap.add_argument("--allow-missing-pdf", action="store_true",
                    help="--local: keep filings whose PDF is missing (xbrl_only; page checks skipped)")
    ap.add_argument("--start", default="2025-03-01", help="first submission date (YYYY-MM-DD)")
    ap.add_argument("--end", default=None, help="last submission date (default: yesterday)")
    ap.add_argument("--years", default="2025,2026", help="fiscal years (periodEnd year) to keep")
    ap.add_argument("--form-codes", default="030000", help="EDINET formCode(s) of domestic 有価証券報告書")
    ap.add_argument("--target", type=int, default=200)
    ap.add_argument("--min-ifrs", type=int, default=60)
    ap.add_argument("--max-ifrs", type=int, default=70, help="cap on IFRS filings (keeps --prioritize from "
                                                              "turning the sample mostly IFRS)")
    ap.add_argument("--min-thousand-yen", type=int, default=50)
    ap.add_argument("--min-one-page", type=int, default=40)
    ap.add_argument("--min-two-page", type=int, default=60)
    ap.add_argument("--industry-cap", type=int, default=20)
    ap.add_argument("--max-candidates", type=int, default=1500, help="stop after downloading this many")
    ap.add_argument("--seed", default="JP-BS-Bench/2026-10-24/sample-v1")
    ap.add_argument("--codelist", default=None, help="local EdinetcodeDlInfo.csv or Edinetcode.zip (default: download)")
    ap.add_argument("--prioritize", default=None,
                    help="codes to visit first: .txt/.csv (one EDINET code E12345 or securities code per line) "
                         "or PDFs such as JPX's IFRS lists (codes read from the text); several files comma-separated; "
                         "sampling stays stratified and --max-ifrs caps IFRS")
    ap.add_argument("--keep-securities", action="store_true", help="do not exclude securities firms")
    ap.add_argument("--keep-raw", action="store_true", help="keep full XBRL zips and PDFs in the cache")
    ap.add_argument("--list-only", action="store_true", help="only build the candidate pool and report sizes")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between API calls")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--pilot", action="store_true",
                    help="mark this run as the pilot: writes forced_dev.txt (its companies go to dev)")
    ap.add_argument("--force-resume-args", action="store_true",
                    help="resume although selection arguments differ from the stored run (not recommended)")
    return ap.parse_args(argv)


LOG_FIELDS = ["docID", "edinetCode", "filerName", "periodEnd", "industry", "decision", "reason", "standard",
              "unit", "pages", "end_total_check", "processed_at"]
MANIFEST_FIELDS = ["docID", "edinetCode", "secCode", "filerName", "periodEnd", "industry", "standard", "unit", "pages",
                   "pdf_page_range_0based", "bs_found_by", "end_total_value_on_last_page", "bs_pages_sha256", "pilot"]
SELECTION_ARGS = ("start", "end", "years", "form_codes", "target", "min_ifrs", "max_ifrs", "min_thousand_yen",
                  "min_one_page", "min_two_page", "industry_cap", "seed", "keep_securities", "pilot")


def append_row(path: Path, fields: list[str], row: dict) -> None:
    """Append one CSV row (header on first write) and fsync, so an interrupted run loses nothing."""
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: json.dumps(row[k], ensure_ascii=False) if isinstance(row.get(k), (list, dict)) else row.get(k)
                    for k in fields})
        f.flush()
        os.fsync(f.fileno())


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def file_sha(paths: str | None) -> str | None:
    if not paths:
        return None
    h = hashlib.sha256()
    for p in paths.split(","):
        h.update(Path(p.strip()).read_bytes())
    return h.hexdigest()[:16]


def load_prioritize(path: str | None) -> tuple[set[str], set[str]]:
    """EDINET codes (E12345) and securities codes from a .txt/.csv (one per line) or a PDF such as
    JPX's IFRS list (codes are pulled from the text). False positives only change visiting order."""
    codes, secs = set(), set()
    if not path:
        return codes, secs
    if "," in path:                                      # several files, e.g. JPX tekiyou.pdf,shinki.pdf
        for part in path.split(","):
            c, s_ = load_prioritize(part.strip())
            codes |= c
            secs |= s_
        return codes, secs
    if path.lower().endswith(".pdf"):
        text = "\n".join(p.get_text() for p in pymupdf.open(path))
        tokens = re.findall(r"(?<![0-9A-Za-z])(E\d{5}|[1-9]\d{2}[0-9A-Z])(?![0-9A-Za-z])", unicodedata.normalize("NFKC", text))
    else:
        tokens = [unicodedata.normalize("NFKC", l).strip().split(",")[0] for l in Path(path).read_text(encoding="utf-8-sig").splitlines()]
    for t in tokens:
        t = t.strip().upper()
        if re.fullmatch(r"E\d{5}", t):
            codes.add(t)
        elif re.fullmatch(r"[0-9][0-9A-Z]{3}[0-9]?", t):
            secs.add(t[:4])
    return codes, secs


def run(argv=None, session=None) -> dict:
    a = parse_args(argv)
    if a.local:                                         # hand-downloaded filings: no key, no API
        return run_local(a)
    key = os.environ.get("EDINET_API_KEY", "").strip()
    if not key:
        raise SystemExit("EDINET_API_KEY is not set (export it; it is never printed)")
    _SECRETS.append(key)
    secret = Secret(key)
    out = Path(a.out)
    cache = out / "_cache"
    filings_dir = out / "filings"
    filings_dir.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    client = EdinetClient(secret, cache, a.sleep, session=session)

    # ---- run state: a restart must use the same selection arguments (else the pool/order changes)
    state_f = out / "run_state.json"
    state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else None
    if a.end is None:
        a.end = state["selection"]["end"] if state else (dt.date.today() - dt.timedelta(days=1)).isoformat()
    selection = {k: getattr(a, k) for k in SELECTION_ARGS} | {"prioritize_sha256": file_sha(a.prioritize)}
    if state and not a.list_only:
        diff = {k: (state["selection"].get(k), v) for k, v in selection.items() if state["selection"].get(k) != v}
        if diff and not a.force_resume_args:
            raise SystemExit(f"{out} holds a run with different selection arguments {diff}. Re-run with the "
                             f"original arguments to resume, use a new --out, or pass --force-resume-args.")
    if not a.list_only and (state is None or a.force_resume_args):
        state_f.write_text(json.dumps({"script_version": SCRIPT_VERSION, "selection": selection,
                                       "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
    years = {int(y) for y in a.years.split(",")}
    forms = set(a.form_codes.split(","))
    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)

    # ---- code list (cached in the output folder so a resumed run sees the same list)
    cl_cache = cache / "codelist.bin"
    if a.codelist:
        shutil.copy(a.codelist, cl_cache)
    elif not cl_cache.exists():
        log("downloading EDINET code list")
        try:
            r = (session or requests).get(CODELIST_URL, timeout=120)
            r.raise_for_status()
            cl_cache.write_bytes(r.content)
        except Exception as e:
            raise SystemExit(f"could not download the EDINET code list ({type(e).__name__}). Download it manually "
                             f"from {CODELIST_URL} (or the EDINET site: EDINETタクソノミ及びコードリスト) and pass "
                             f"--codelist Edinetcode.zip")
    codes = load_codelist(str(cl_cache))
    log(f"code list: {len(codes)} filers")

    # ---- 1. candidate pool (daily lists are cached; a restart re-lists from the cache in seconds)
    excl = Counter()
    latest: dict[str, dict] = {}
    d = start
    n_days = (end - start).days + 1
    while d <= end:
        docs = client.list_documents(d)                 # a failure here stops the run: the pool must be complete
        for doc in docs:
            reason = candidate_filter(doc, years, forms)
            if reason:
                if reason != "not_annual_report":
                    excl[reason] += 1
                continue
            prev = latest.get(doc["edinetCode"])
            if prev is None or (doc["periodEnd"], doc.get("submitDateTime") or "") > \
                    (prev["periodEnd"], prev.get("submitDateTime") or ""):
                if prev is not None:
                    excl["older_filing_same_company"] += 1
                latest[doc["edinetCode"]] = doc
            else:
                excl["older_filing_same_company"] += 1
        if (d - start).days % 30 == 0:
            log(f"listed {(d - start).days + 1}/{n_days} days, {len(latest)} companies so far")
        d += dt.timedelta(days=1)

    excluded_industry: list[dict] = []
    pool = []
    for code, doc in latest.items():
        info = codes.get(code)
        if info is None:
            excl["not_in_code_list"] += 1
            continue
        if info["listed"] != "上場":
            excl["not_listed"] += 1
            continue
        if info["consolidated"] not in ("有", "あり"):
            excl["no_consolidated_statements"] += 1
            continue
        ind = info["industry"]
        if ind in EXCLUDE_INDUSTRIES or (ind == SECURITIES_INDUSTRY and not a.keep_securities):
            excl[f"industry:{ind}"] += 1
            excluded_industry.append({"docID": doc["docID"], "edinetCode": code, "industry": ind})
            continue
        pool.append(doc | {"_industry": ind})
    prio_codes, prio_secs = load_prioritize(a.prioritize)

    def prioritized(doc):
        return doc["edinetCode"] in prio_codes or (doc.get("secCode") or "")[:4] in prio_secs
    pool.sort(key=lambda doc: (not prioritized(doc), order_key(a.seed, doc["docID"])))
    pool_by_ind = Counter(doc["_industry"] for doc in pool)
    log(f"candidate pool: {len(pool)} filings ({sum(map(prioritized, pool))} prioritized); exclusions: {dict(excl)}")
    summary = {"pool_size": len(pool), "prioritized_in_pool": sum(map(prioritized, pool)),
               "prioritize_codes_loaded": len(prio_codes) + len(prio_secs),
               "pool_by_industry": dict(pool_by_ind.most_common()),
               "exclusions_before_download": dict(excl), "excluded_industry_filings": len(excluded_industry)}
    (out / "pool_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    write_csv(out / "excluded_industry.csv", excluded_industry)
    if a.list_only:
        log("list-only: done (see pool_summary.json)")
        return summary

    # ---- 2. resume state: processed docIDs and quota counts come from the files on disk
    mins = {("standard", "ifrs"): a.min_ifrs, ("unit", "千円"): a.min_thousand_yen,
            ("pages", "1"): a.min_one_page, ("pages", "2"): a.min_two_page}
    maxs = {("standard", "ifrs"): a.max_ifrs}
    sampler = Sampler(a.target, mins, a.industry_cap, maximums=maxs)
    log_f, man_f = out / "candidates_log.csv", out / "manifest.csv"
    logged = {r["docID"]: r for r in read_rows(log_f)}
    manifest = read_rows(man_f)
    for m in manifest:
        sampler.restore({"standard": m["standard"], "unit": m["unit"], "pages": m["pages"], "industry": m["industry"]})
        if m["docID"] not in logged:                    # crashed between the two appends: repair the log
            rec = {k: m.get(k) for k in LOG_FIELDS} | {"decision": "accepted", "reason": "accepted(recovered)"}
            append_row(log_f, LOG_FIELDS, rec)
            logged[m["docID"]] = rec
    done = set(logged)
    if done:
        log(f"resuming: {len(done)} filings already processed, {sampler.accepted} accepted")

    processed = len(done)
    for doc in pool:
        if sampler.accepted >= a.target or processed >= a.max_candidates:
            break
        did, code = doc["docID"], doc["edinetCode"]
        if did in done:
            continue
        processed += 1
        rec = {"docID": did, "edinetCode": code, "filerName": doc.get("filerName"), "periodEnd": doc["periodEnd"],
               "industry": doc["_industry"], "decision": None, "reason": None}
        try:
            rec |= process_candidate(doc, client, sampler, filings_dir, man_f, a)
        except Exception as e:                          # any failure of one filing: log it and move on
            rec.update(decision="error", reason=scrub(f"{type(e).__name__}: {e}")[:300])
        rec["processed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        append_row(log_f, LOG_FIELDS, rec)
        log(f"{processed:4d} {did} {rec['decision']:8} {rec['reason']}  [accepted {sampler.accepted}/{a.target}]")
        if not a.keep_raw:
            for f in (cache / "docs").glob(f"{did}_*"):
                try:
                    f.unlink()
                except OSError:
                    pass

    # ---- 3. outputs (rebuilt from the files, so they are complete after any number of restarts)
    log_rows = read_rows(log_f)
    manifest = read_rows(man_f)
    post = Counter(f"{r['decision']}:{r['reason']}" for r in log_rows)
    endchk = Counter(r.get("end_total_check") or "n/a" for r in log_rows if r["decision"] in ("accepted", "skipped")
                     or r["reason"] == "bs_end_total_not_on_page")
    if a.pilot:
        with open(out / "forced_dev.txt", "w", encoding="utf-8") as f:
            f.write("# Pilot filings: inspected during development, so their companies are forced into dev.\n")
            for m in manifest:
                f.write(f"{m['edinetCode']},{m['docID']}\n")
    summary |= {"processed": len(log_rows), "accepted": len(manifest), "post_download_outcomes": dict(post),
                "end_total_check_counts": dict(endchk),
                "end_total_not_on_page_excluded": post.get("excluded:bs_end_total_not_on_page", 0),
                "strata_counts": {f"{k[0]}={k[1]}": v for k, v in sorted(sampler.counts.items())},
                "minimum_shortfalls": sampler.shortfalls()}
    (out / "exclusions_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    run_info = {"script_version": SCRIPT_VERSION, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
                "selection": selection, "submission_dates": [start.isoformat(), end.isoformat()],
                "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "python": sys.version.split()[0], "pymupdf": pymupdf.VersionBind}
    (out / "run_info.json").write_text(json.dumps(run_info, ensure_ascii=False, indent=1), encoding="utf-8")
    assert_no_secret(out, key)
    if not a.no_zip:
        zpath = out.parent / f"{out.name}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(out.rglob("*")):
                if f.is_file() and "_cache" not in f.parts:
                    z.write(f, f.relative_to(out.parent))
        log(f"wrote {zpath} ({zpath.stat().st_size / 1e6:.1f} MB)")
    log(f"done: accepted {len(manifest)}, shortfalls {sampler.shortfalls() or 'none'}")
    return summary


def process_candidate(doc: dict, client: "EdinetClient", sampler: "Sampler", filings_dir: Path, man_f: Path, a) -> dict:
    """Download, extract, check and (maybe) accept one filing. Returns the log fields to record."""
    did = doc["docID"]
    zpath = client.download(did, 1)
    x = extract_xbrl(zpath)
    std = x["standard"]
    ind_dei = (x["industry_code_dei"] or "").lower()
    if std not in ("jgaap", "ifrs"):
        return {"decision": "excluded", "reason": f"standard:{std}"}
    if ind_dei.startswith(("bk", "in")):                # second safety net (DEI industry code)
        return {"decision": "excluded", "reason": f"dei_industry:{ind_dei}"}
    if x["bs"] is None:
        return {"decision": "excluded", "reason": "bs_not_found_in_ixbrl"}
    unit = {3: "千円", 6: "百万円", 0: "円"}.get(x["bs"]["scale"], f"scale{x['bs']['scale']}")
    partial = {"standard": std, "unit": unit, "industry": doc["_industry"]}
    if not sampler.could_accept(partial):               # saves the PDF download when no page count would fit
        return {"decision": "skipped", "reason": "no_open_quota_for_strata", **partial}
    ppath = client.download(did, 2)
    rng, why, pdf_diag = find_bs_pages(ppath, std, end_total_texts(x["bs"]))
    if rng is None:
        return {"decision": "excluded", "reason": why, **partial}
    chk = pdf_diag.get("end_total_value_on_last_page")
    chk_s = {True: "true", False: "false", None: "unknown"}[chk]
    if chk is False:                                     # the page range ends somewhere the total isn't shown
        return {"decision": "excluded", "reason": "bs_end_total_not_on_page", "end_total_check": chk_s, **partial}
    n_pages = rng[1] - rng[0] + 1
    strata = partial | {"pages": str(n_pages) if n_pages <= 2 else "3+"}
    ok, why = sampler.offer(strata)
    if ok:
        row = save_filing(filings_dir, doc, x, ppath, rng, pdf_diag, strata) | {"pilot": a.pilot}
        append_row(man_f, MANIFEST_FIELDS, row)
    return {"decision": "accepted" if ok else "skipped", "reason": why, "end_total_check": chk_s, **strata}


def bs_rows_view(bs: dict) -> list[dict]:
    """One line per displayed row: label, indent level (leading columns), printed values, notes, element."""
    out = []
    instants = bs.get("instants", [])
    for tb in bs["tables"]:
        for r in tb["rows"]:
            vals = {}
            for c in r["cells"]:
                for f in c["facts"]:
                    if _consolidated_instant(f):
                        vals[f["instant"]] = (c["value_text"], c["notes"], f["name"])
            if r.get("label") is None and not vals:
                continue
            lbl = r.get("label") or ""
            if not vals and ("単位" in lbl or re.search(r"会計年度|\d+年\d+月\d+日", lbl)):
                continue                                  # unit line / period header inside the table
            row = {"table": tb["table_index"], "lead_cols": r.get("lead_cols"), "label": r.get("label") or "",
                   "label_notes": "".join(r.get("label_notes") or [])}
            names = set()
            for k, inst in enumerate(instants):
                v = vals.get(inst)
                row[f"value_{inst}"] = v[0] if v else ""
                row[f"notes_{inst}"] = "".join(v[1]) if v else ""
                if v:
                    names.add(v[2])
            row["element"] = ",".join(sorted(names))
            out.append(row)
    return out


def save_filing(filings_dir: Path, doc: dict, x: dict, pdf_path: Path | None, rng, pdf_diag, strata) -> dict:
    d = filings_dir / doc["docID"]
    d.mkdir(parents=True, exist_ok=True)
    if pdf_path is not None and rng is not None:
        src = pymupdf.open(pdf_path)
        out = pymupdf.open()
        out.insert_pdf(src, from_page=rng[0], to_page=rng[1])
        out.save(d / "bs_pages.pdf", garbage=3, deflate=True)
    used = {f["contextRef"] for tb in x["bs"]["tables"] for r in tb["rows"] for c in r["cells"] for f in c["facts"]}
    (d / "ixbrl_bs.json").write_text(json.dumps(x["bs"], ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "contexts.json").write_text(json.dumps({k: v for k, v in x["contexts"].items() if k in used},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "dei.json").write_text(json.dumps(x["dei"], ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "cal_bs.json").write_text(json.dumps(x["cal"], ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "pre_bs.json").write_text(json.dumps(x["pre"], ensure_ascii=False, indent=1), encoding="utf-8")
    write_csv(d / "bs_rows.csv", bs_rows_view(x["bs"]))
    pages_pdf = d / "bs_pages.pdf"
    meta = {"docID": doc["docID"], "edinetCode": doc["edinetCode"], "secCode": doc.get("secCode"),
            "filerName": doc.get("filerName"), "periodStart": doc.get("periodStart"), "periodEnd": doc["periodEnd"],
            "submitDateTime": doc.get("submitDateTime"), "docDescription": doc.get("docDescription"),
            "industry": doc["_industry"], "industry_code_dei": x["industry_code_dei"], **strata,
            "pdf_page_range_0based": list(rng) if rng else None, **(pdf_diag or {}),
            "bs_source_file": x["bs"]["source_file"], "bs_found_by": x["bs"]["found_by"], "instants": x["bs"]["instants"],
            "bs_pages_sha256": hashlib.sha256(pages_pdf.read_bytes()).hexdigest()[:16] if pages_pdf.exists() else None,
            "source": doc.get("_source", "api")}
    meta.setdefault("end_total_value_on_last_page", None)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return {k: meta.get(k) for k in ("docID", "edinetCode", "secCode", "filerName", "periodEnd", "industry",
                                     "standard", "unit", "pages", "pdf_page_range_0based", "bs_found_by",
                                     "end_total_value_on_last_page", "bs_pages_sha256")}


# ============================================================================ local mode
DOCID_RE = re.compile(r"(?<![0-9A-Z])(S[0-9A-Z]{7})(?![0-9A-Z])")


def zip_identity(zpath: Path) -> dict:
    """docID / EDINET code / filer from a public-site zip (XbrlSearchDlInfo.csv + S100XXXX/ prefix)."""
    info = {"docID": None, "edinetCode": None, "filerName": None}
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        csvname = next((n for n in names if n.lower().endswith("xbrlsearchdlinfo.csv")), None)
        if csvname:
            text = z.read(csvname).decode("cp932", errors="replace")
            rows = list(csv.reader(text.splitlines()))
            if len(rows) >= 3:
                header = [unicodedata.normalize("NFKC", h).strip() for h in rows[1]]
                vals = dict(zip(header, rows[2]))
                info["docID"] = next((v for k, v in vals.items() if "書類管理番号" in k), None)
                info["edinetCode"] = next((v for k, v in vals.items() if "EDINET" in k and "コード" in k), None)
                info["filerName"] = next((v for k, v in vals.items() if k.startswith("提出者") and "法人番号" not in k), None)
        if not info["docID"]:
            m = next((DOCID_RE.match(n.split("/")[0]) for n in names if DOCID_RE.match(n.split("/")[0])), None)
            info["docID"] = m.group(1) if m else None
    if not info["docID"]:
        m = DOCID_RE.search(zpath.name.upper())
        info["docID"] = m.group(1) if m else zpath.stem
    return info


def pair_local(folder: Path) -> tuple[list[tuple[Path, Path | None, dict]], list[str]]:
    """Pair each XBRL zip with its PDF: docID in the PDF file name, else the single PDF next to a single zip."""
    zips = sorted(p for p in folder.rglob("*.zip") if not p.name.startswith("._"))
    pdfs = sorted(p for p in folder.rglob("*.pdf") if not p.name.startswith("._"))
    used, pairs, notes = set(), [], []
    for z in zips:
        ident = zip_identity(z)
        pdf = next((p for p in pdfs if ident["docID"] and ident["docID"] in p.name.upper()), None)
        if pdf is None:
            sib_z = [q for q in zips if q.parent == z.parent]
            sib_p = [q for q in pdfs if q.parent == z.parent]
            if len(sib_z) == 1 and len(sib_p) == 1:
                pdf = sib_p[0]
        if pdf is not None:
            used.add(pdf)
        else:
            notes.append(f"{z.name}: no PDF found (name it <docID>.pdf or put it alone with the zip in a folder)")
        pairs.append((z, pdf, ident))
    notes += [f"{p.name}: PDF without a matching zip" for p in pdfs if p not in used]
    return pairs, notes


def run_local(a) -> dict:
    """Same extraction, page finding and outputs as the API path, for hand-downloaded filings."""
    src, out = Path(a.local), Path(a.out)
    filings_dir = out / "filings"
    filings_dir.mkdir(parents=True, exist_ok=True)
    codes = load_codelist(a.codelist) if a.codelist else {}
    pairs, pair_notes = pair_local(src)
    for n in pair_notes:
        log(n)
    log_f, man_f = out / "candidates_log.csv", out / "manifest.csv"
    done = {r["docID"] for r in read_rows(log_f)}
    for zpath, pdf, ident in pairs:
        did = ident["docID"]
        if did in done:
            continue
        rec = {"docID": did, "edinetCode": ident["edinetCode"], "filerName": ident["filerName"],
               "decision": None, "reason": None}
        try:
            x = extract_xbrl(zpath)
            dei = x["dei"]
            code = ident["edinetCode"] or dei.get("EDINETCodeDEI")
            info = codes.get(code or "", {})
            industry = info.get("industry") or "unknown"
            period_end = dei.get("CurrentFiscalYearEndDateDEI") or (x["bs"]["instants"][-1] if x["bs"] else None)
            rec.update(edinetCode=code, filerName=ident["filerName"] or dei.get("FilerNameInJapaneseDEI"),
                       periodEnd=period_end, industry=industry)
            std = x["standard"]
            ind_dei = (x["industry_code_dei"] or "").lower()
            if industry in EXCLUDE_INDUSTRIES or (industry == SECURITIES_INDUSTRY and not a.keep_securities):
                rec.update(decision="excluded", reason=f"industry:{industry}")
            elif std not in ("jgaap", "ifrs"):
                rec.update(decision="excluded", reason=f"standard:{std}")
            elif ind_dei.startswith(("bk", "in")):
                rec.update(decision="excluded", reason=f"dei_industry:{ind_dei}")
            elif x["bs"] is None:
                rec.update(decision="excluded", reason="bs_not_found_in_ixbrl")
            else:
                unit = {3: "千円", 6: "百万円", 0: "円"}.get(x["bs"]["scale"], f"scale{x['bs']['scale']}")
                strata = {"standard": std, "unit": unit, "pages": None, "industry": industry}
                doc = {"docID": did, "edinetCode": code, "secCode": dei.get("SecurityCodeDEI"),
                       "filerName": rec["filerName"], "periodEnd": period_end, "_industry": industry, "_source": "local"}
                rng, why, pdf_diag = (None, "pdf_missing", {})
                if pdf is not None:
                    rng, why, pdf_diag = find_bs_pages(pdf, std, end_total_texts(x["bs"]))
                chk = pdf_diag.get("end_total_value_on_last_page")
                rec["end_total_check"] = {True: "true", False: "false", None: "unknown"}[chk]
                if rng is not None and chk is False:
                    rec.update(decision="excluded", reason="bs_end_total_not_on_page", **strata)
                elif rng is None and not (why == "pdf_missing" and a.allow_missing_pdf):
                    rec.update(decision="excluded", reason=why, **strata)
                else:
                    if rng is not None:
                        n_pages = rng[1] - rng[0] + 1
                        strata["pages"] = str(n_pages) if n_pages <= 2 else "3+"
                    row = save_filing(filings_dir, doc, x, pdf if rng else None, rng, pdf_diag, strata) | {"pilot": a.pilot}
                    append_row(man_f, MANIFEST_FIELDS, row)
                    rec.update(decision="accepted" if rng else "xbrl_only",
                               reason="accepted" if rng else "pdf_missing", **strata)
        except Exception as e:                                # one bad filing never stops the run
            rec.update(decision="error", reason=scrub(f"{type(e).__name__}: {e}")[:300])
        rec["processed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        append_row(log_f, LOG_FIELDS, rec)
        log(f"{did} {rec['decision']:9} {rec['reason']}")
    manifest = read_rows(man_f)
    log_rows = read_rows(log_f)
    if a.pilot:
        with open(out / "forced_dev.txt", "w", encoding="utf-8") as f:
            f.write("# Hand-picked dev filings: inspected during development, so their companies are forced into dev.\n")
            for m in manifest:
                f.write(f"{m['edinetCode']},{m['docID']}\n")
    summary = {"mode": "local", "zips": len(pairs), "pairing_notes": pair_notes, "processed": len(log_rows),
               "accepted": sum(r["decision"] == "accepted" for r in log_rows),
               "xbrl_only": sum(r["decision"] == "xbrl_only" for r in log_rows),
               "outcomes": dict(Counter(f"{r['decision']}:{r['reason']}" for r in log_rows))}
    (out / "exclusions_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "run_info.json").write_text(json.dumps({
        "script_version": SCRIPT_VERSION, "mode": "local",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}, indent=1), encoding="utf-8")
    if not a.no_zip:
        zp = out.parent / f"{out.name}.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(out.rglob("*")):
                if f.is_file() and "_cache" not in f.parts:
                    z.write(f, f.relative_to(out.parent))
        log(f"wrote {zp}")
    log(f"done: {summary['accepted']} accepted, {summary['xbrl_only']} xbrl-only, of {len(pairs)} zips")
    return summary


def write_csv(path: Path, rows: list[dict]) -> None:
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys or ["empty"])
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in r.items()})


def assert_no_secret(out: Path, key: str) -> None:
    for f in out.rglob("*"):
        if f.is_file() and "_cache" not in f.parts and f.suffix in (".json", ".csv", ".txt"):
            if key in f.read_text(encoding="utf-8", errors="ignore"):
                raise SystemExit(f"refusing to finish: API key found in {f.name}")


if __name__ == "__main__":
    try:
        run()
    except EdinetError as e:
        print(scrub(f"EDINET error: {e}"), file=sys.stderr)
        sys.exit(2)
