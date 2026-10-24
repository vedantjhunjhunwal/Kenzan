"""JP-BS-Bench, Phase A step 2: ground truth from inline XBRL, aligned to the PDF pages.

Ground truth comes from the filing's inline-XBRL table (the HTML the PDF is rendered from), not from
any parser of the PDF:
  rows / labels / order / indent  the balance-sheet <tr> rows as filed
  values                          the printed cell text (sign as printed: △ -> negative), cross-checked
                                  against the tagged XBRL fact (|fact| must equal printed x 10^scale)
  calc_parent                     the company's XBRL calculation linkbase (balance-sheet role)
  presentation level / parent     indent of the label cell (CSS padding + leading spaces)

Alignment: every row with values must be found on the PDF pages -- label as a substring of the page
text (in order) and every printed value in the page's numeric-token sequence (difflib alignment).
This matcher is independent of the docparse pipeline. A filing is KEPT if >= 95% of rows align.
Filings whose text layer fails the quality gate cannot be checked this way; they are kept in a
separate bucket ("unverifiable_text_layer") instead of being dropped, so the filter cannot remove
exactly the pages that are hard for the text-layer route.

usage: python bench/build_gt_xbrl.py <raw_dir (with filings/)> <out_dir>
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from docparse.export import write_all  # noqa: E402
from docparse.hierarchy import assign_label_paths, cluster_1d  # noqa: E402
from docparse.normalize import DASH_GLYPHS, label_norm  # noqa: E402
from docparse.router import decide as gate  # noqa: E402
from docparse.validate import run_checks  # noqa: E402

KEEP_THRESHOLD = 0.95
NEG_RE = re.compile(r"[△▲]")
DIGITS_RE = re.compile(r"\d[\d,]*")


def nfkc_nospace(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


NOTE_RE = re.compile(r"[※＊*]\s*[0-9０-９]{1,2}(?:\s*[,，、・]\s*[※＊*]?\s*[0-9０-９]{1,2})*")


def split_notes(text: str) -> tuple[str, list[str]]:
    """'※1 65,180' -> ('65,180', ['※1']); same rule as fetch_edinet.split_notes."""
    refs = ["※" + n for m in NOTE_RE.finditer(text)
            for n in re.findall(r"\d+", unicodedata.normalize("NFKC", m.group(0)))]
    return re.sub(r"\s+", " ", NOTE_RE.sub(" ", text)).strip(), refs


def _cons(f: dict) -> bool:
    return f.get("instant") is not None and not f.get("dims")


# ----------------------------------------------------------------------------- table -> rows
def table_rows(bs: dict) -> tuple[list[dict], list[str], dict]:
    """Flatten the balance-sheet tables into data rows. Returns (rows, instants, diagnostics)."""
    grid_rows = []
    for tb in bs["tables"]:
        for r in tb["rows"]:
            pos, cells = 0, []
            for c in r["cells"]:
                cells.append(c | {"pos": pos})
                pos += c.get("colspan", 1)
            grid_rows.append(cells)
    votes: dict[int, Counter] = {}
    for cells in grid_rows:
        for c in cells:
            for f in c["facts"]:
                if _cons(f):
                    votes.setdefault(c["pos"], Counter())[f["instant"]] += 1
    pos_instant = {p: v.most_common(1)[0][0] for p, v in votes.items()}
    instants = sorted(set(pos_instant.values()))
    rows, diag = [], Counter()
    for cells in grid_rows:
        # label = first NON-EMPTY cell outside the value columns; EDINET indents by leaving leading
        # columns empty (colspan), so the label's grid position is the primary indent signal
        label_cell = next((c for c in cells if c["pos"] not in pos_instant and c["text"].strip()
                           and not c["facts"]), None)
        label = (label_cell["text"] if label_cell else "").strip()
        values = {}
        for c in cells:
            inst = pos_instant.get(c["pos"])
            if inst is None or not c["text"].strip():
                continue
            values[inst] = c
        text_all = " ".join(c["text"] for c in cells)
        if not label:
            diag["skipped_unlabelled_row"] += 1          # column headers, spacer rows
            continue
        if "単位" in text_all and not any(f for c in cells for f in c["facts"]):
            diag["skipped_unit_row"] += 1
            continue
        if any(re.search(r"\d+年|会計年度", c["text"]) for c in values.values()) and \
                not any(f for c in cells for f in c["facts"]):
            diag["skipped_header_row"] += 1
            continue
        lbl, lnotes = split_notes(label_cell["text"])
        rows.append({"label_raw": lbl, "label_notes": lnotes, "lead_cols": label_cell["pos"],
                     "indent_css": label_cell.get("indent") or 0.0, "values": values})
    return rows, instants, dict(diag)


def parse_cell(c: dict, scale: int) -> dict:
    """Printed cell -> GT cell. Sign and magnitude as printed; XBRL fact as cross-check."""
    full = c["text"].strip()
    text, notes = split_notes(full)                    # 「※1 65,180」 -> 「65,180」 + ['※1']
    t = unicodedata.normalize("NFKC", text).replace(" ", "")
    facts = [f for f in c["facts"] if _cons(f)]
    f = facts[0] if facts else None
    # magnitude from the tagged fact's own text when there is one (cannot absorb a note number)
    src = unicodedata.normalize("NFKC", f["text"]).replace(" ", "") if f and DIGITS_RE.search(f["text"] or "") else t
    m = DIGITS_RE.search(src)
    if m is None:
        if t and all(ch in DASH_GLYPHS or ch in "-" for ch in t):
            # Printed 「－」 is nil in the ground truth. XBRL may tag it as an explicit zero (fixed-zero),
            # as xsi:nil, or not at all; the convention is recorded and counted, never "corrected".
            conv = "dash_untagged" if f is None else ("dash_tagged_nil" if f["nil"] or f["value"] is None
                                                      else "dash_tagged_zero" if f["value"] == 0 else "dash_tagged_nonzero")
            return {"raw_text": text, "footnote_refs": notes, "reported_value": None, "value": None,
                    "decimals": None, "is_negative": False, "is_nil": True, "xbrl_name": f["name"] if f else None, "xbrl_value": f["value"] if f else None,
                    "xbrl_tagged": f is not None, "xbrl_consistent": conv != "dash_tagged_nonzero",
                    "xbrl_convention": conv}
        return None
    mag = int(m.group(0).replace(",", ""))
    neg = bool(NEG_RE.search(t)) or bool(re.match(r"^[-－−]\d", t))
    rep = -mag if neg else mag
    sc = f["scale"] if f else scale
    xv = f["value"] if f else None
    # Sign is taken from the page (△). XBRL stores contra items such as 減価償却累計額 either as negative
    # values (sign="-") or as positive values with the subtraction in the calculation weight.
    if xv is None:
        conv = "untagged_number" if f is None else "tagged_without_value"
    elif not neg:
        conv = "positive" if xv >= 0 else "negative_in_xbrl_printed_positive"
    else:
        conv = "printed_negative_xbrl_negative" if xv < 0 else ("printed_negative_xbrl_positive" if xv > 0
                                                                else "printed_negative_zero")
    return {"raw_text": text, "footnote_refs": notes, "reported_value": rep, "value": rep * 10 ** sc,
            "decimals": int(f["decimals"]) if f and f.get("decimals") not in (None, "INF") else -sc,
            "is_negative": neg, "is_nil": False, "xbrl_name": f["name"] if f else None, "xbrl_value": xv,
            "xbrl_tagged": f is not None,
            "xbrl_consistent": None if xv is None else abs(xv) == mag * 10 ** sc,
            "sign_matches_xbrl": None if xv is None or mag == 0 else (xv < 0) == neg,
            "xbrl_convention": conv}


def cal_parent_map(cal: dict, row_elements: set[str]) -> tuple[dict[str, tuple[str, float]], str | None]:
    """child element id -> (parent element id, weight) from the consolidated balance-sheet role."""
    best, best_role = {}, None
    roles = sorted(cal, key=lambda r: ("Consolidated" not in r, r))
    for role in roles:
        m = {a["to"]: (a["from"], float(a["weight"] or 1)) for a in cal[role] if a["to"] and a["from"]}
        overlap = len(set(m) & row_elements)
        if overlap > len(set(best) & row_elements):
            best, best_role = m, role
    return best, best_role


def build_filing(fdir: Path) -> tuple[dict, dict]:
    bs = json.loads((fdir / "ixbrl_bs.json").read_text(encoding="utf-8"))
    cal = json.loads((fdir / "cal_bs.json").read_text(encoding="utf-8"))
    meta = json.loads((fdir / "meta.json").read_text(encoding="utf-8"))
    scale = int(bs["scale"])
    raw_rows, instants, diag = table_rows(bs)
    ids = {instants[-1]: "current"}
    if len(instants) >= 2:
        ids[instants[-2]] = "prior"
    for k, inst in enumerate(reversed(instants[:-2]), 2):
        ids[inst] = f"prior{k}"
    periods = [{"id": ids[i], "context": "instant", "date": i} for i in instants]

    rows = []
    for n, rr in enumerate(raw_rows, 1):
        norm, refs = label_norm(rr["label_raw"])
        refs = rr.get("label_notes", []) + refs
        cells = {}
        for inst, c in rr["values"].items():
            pc = parse_cell(c, scale)
            if pc is not None:
                cells[ids[inst]] = pc | {"is_corrected": False, "confidence": None, "bbox": None}
        names = Counter(c["xbrl_name"] for c in cells.values() if c.get("xbrl_name"))
        rows.append({"row_id": f"r{n:02d}", "label_ja": rr["label_raw"].strip(" 　 "), "label_norm": norm,
                     "label_path": None, "label_en": None, "row_type": None, "presentation_level": None,
                     "presentation_parent": None, "calc_parent": None, "calc_parent_source": None, "calc_rule": None,
                     "total_status": None, "closes_header": None, "footnote_refs": refs,
                     "xbrl_element": names.most_common(1)[0][0] if names else None, "xbrl_verified": True,
                     "human_verification": "pending_spot_check", "lead_cols": rr["lead_cols"],
                     "indent_css": rr["indent_css"], "cells": cells})

    # presentation tree from indent
    # indent key: leading empty columns first (EDINET layout), CSS padding second (other layouts)
    css_levels = cluster_1d([r["indent_css"] for r in rows], tol=1.0)
    for r in rows:
        r["indent_key"] = (r["lead_cols"], min(range(len(css_levels)), key=lambda i: abs(css_levels[i] - r["indent_css"])))
    keys = sorted({r["indent_key"] for r in rows})
    centers = keys
    for r in rows:
        r["presentation_level"] = keys.index(r["indent_key"])
    for i, r in enumerate(rows):
        r["presentation_parent"] = next((p["row_id"] for p in reversed(rows[:i])
                                         if p["presentation_level"] < r["presentation_level"]), None)
    presentation_reliable = len(centers) >= 3

    # calculation tree from the linkbase
    elem_rows: dict[str, list[dict]] = {}
    for r in rows:
        if r["xbrl_element"]:
            elem_rows.setdefault(r["xbrl_element"].replace(":", "_"), []).append(r)
    pmap, role = cal_parent_map(cal, set(elem_rows))
    calc_missing = 0
    for r in rows:
        if not r["xbrl_element"]:
            continue
        eid = r["xbrl_element"].replace(":", "_")
        cur, hops = eid, 0
        while cur in pmap:
            parent, w = pmap[cur]
            hops += 1
            if parent in elem_rows:
                prow = min(elem_rows[parent], key=lambda p: abs(int(p["row_id"][1:]) - int(r["row_id"][1:])))
                r.update(calc_parent=prow["row_id"], calc_parent_source="xbrl_cal" if hops == 1 else "xbrl_cal_ancestor",
                         calc_weight=w)
                break
            cur = parent
        else:
            if eid in pmap:
                calc_missing += 1
    has_kids = {r["calc_parent"] for r in rows if r["calc_parent"]}
    for r in rows:
        if not r["cells"]:
            r["row_type"] = "header"
        elif r["row_id"] in has_kids:
            r["row_type"] = "total" if r["calc_parent"] is None else "subtotal"
        else:
            r["row_type"] = "line_item"
    assign_label_paths(rows)

    std = meta.get("standard", "jgaap")
    stype = "consolidated_balance_sheet" if std == "jgaap" else "consolidated_statement_of_financial_position"
    checks, summary = run_checks(rows, periods, stype)
    cells = [c for r in rows for c in r["cells"].values()]
    doc_meta = {
        "company": meta.get("filerName"), "company_code": meta.get("edinetCode"), "docID": meta.get("docID"),
        "statement_type": stype, "accounting_standard": std, "unit_ja": meta.get("unit"),
        "unit_multiplier": 10 ** scale, "currency": "JPY", "decimals": -scale,
        "source": {"file": "bs_pages.pdf", "pdf_page_range_0based": meta.get("pdf_page_range_0based")},
        "gt_provenance": {"values_and_rows": "inline XBRL table", "calc_parent": f"XBRL calculation linkbase ({role})",
                          "presentation": "inline XBRL label-cell indent",
                          "presentation_reliable": presentation_reliable},
    }
    doc = {"status": "ok", "reason": None, "metadata": doc_meta, "periods": periods, "rows": rows,
           "checks": checks, "check_summary": summary}
    stats = {"rows": len(rows), "value_rows": sum(1 for r in rows if r["cells"]), "cells": len(cells),
             "untagged_cells": sum(1 for c in cells if not c["xbrl_tagged"]),
             "xbrl_value_mismatch": sum(1 for c in cells if c.get("xbrl_consistent") is False),
             "sign_differs_from_xbrl": sum(1 for c in cells if c.get("sign_matches_xbrl") is False),
             **{f"conv:{k}": v for k, v in sorted(Counter(c.get("xbrl_convention") for c in cells).items())},
             "calc_parent_from_cal": sum(1 for r in rows if r["calc_parent_source"] == "xbrl_cal"),
             "calc_parent_ancestor": sum(1 for r in rows if r["calc_parent_source"] == "xbrl_cal_ancestor"),
             "calc_parent_not_displayed": calc_missing, "cal_role": role,
             "presentation_levels": len(centers), "presentation_reliable": presentation_reliable,
             "gt_checks_failed": summary["template"]["failed"] + summary["dynamic"]["failed"],
             "gt_checks_evaluable": summary["template"]["evaluable"] + summary["dynamic"]["evaluable"], **diag}
    return doc, stats


# ----------------------------------------------------------------------------- alignment with PDF
_NUM_TOKEN = re.compile(r"^[△▲]?\d{1,3}(,\d{3})*$|^[△▲]?\d+$")


def _norm_val(s: str) -> str:
    t = nfkc_nospace(s).replace("▲", "△")
    if t and all(ch in DASH_GLYPHS or ch == "-" for ch in t):
        return "－"
    return t


def pdf_value_tokens(doc: pymupdf.Document) -> list[str]:
    toks = []
    for page in doc:
        words = page.get_text("words")
        if not words:
            continue
        hs = sorted(w[3] - w[1] for w in words)
        tol = 0.5 * hs[len(hs) // 2]
        lines: list[list] = []
        for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
            yc = (w[1] + w[3]) / 2
            if lines and abs(yc - lines[-1][0]) <= tol:
                lines[-1][1].append(w)
            else:
                lines.append([yc, [w]])
        for _, ws in lines:
            ws.sort(key=lambda w: w[0])
            merged = []
            for w in ws:
                t = nfkc_nospace(w[4])
                if merged and merged[-1] in ("△", "▲") and t[:1].isdigit():
                    merged[-1] += t
                else:
                    merged.append(t)
            for t in merged:
                v = _norm_val(t)
                if _NUM_TOKEN.match(v) or v == "－":
                    toks.append(v)
    return toks


def align(doc_gt: dict, pdf_path: Path) -> dict:
    pdf = pymupdf.open(pdf_path)
    gates = [gate(p) for p in pdf]
    if any(g.route != "born_digital" for g in gates):
        return {"status": "unverifiable_text_layer", "match_rate": None,
                "reasons": [r for g in gates for r in g.reasons if g.route != "born_digital"]}
    page_text = "".join(nfkc_nospace(p.get_text()) for p in pdf)
    order = [p["id"] for p in doc_gt["periods"]]
    seq, owner = [], []
    for r in doc_gt["rows"]:
        for pid in order:
            c = r["cells"].get(pid)
            if c is not None:
                seq.append(_norm_val(c["raw_text"]))
                owner.append(r["row_id"])
    toks = pdf_value_tokens(pdf)
    sm = difflib.SequenceMatcher(a=seq, b=toks, autojunk=False)
    matched = set()
    for blk in sm.get_matching_blocks():
        matched.update(range(blk.a, blk.a + blk.size))
    ok_vals: dict[str, bool] = {}
    for i, rid in enumerate(owner):
        ok_vals[rid] = ok_vals.get(rid, True) and i in matched
    # Labels are searched in order, but only rows WITH values move the search pointer: a header label
    # such as 流動資産 is a substring of a later 流動資産合計 and would otherwise jump the pointer past
    # every row in between. Header rows are checked anywhere at/after the pointer, without moving it.
    pos, aligned, unaligned, headers_found = 0, 0, [], 0
    value_rows = [r for r in doc_gt["rows"] if r["cells"]]
    header_rows = [r for r in doc_gt["rows"] if not r["cells"]]
    for r in doc_gt["rows"]:
        k = page_text.find(r["label_norm"], pos) if r["label_norm"] else -1
        label_ok = k >= 0
        if not r["cells"]:
            headers_found += label_ok
            continue
        if label_ok:
            pos = k + len(r["label_norm"])
        if label_ok and ok_vals.get(r["row_id"], False):
            aligned += 1
        else:
            unaligned.append({"row": r["row_id"], "label": r["label_ja"], "label_found": label_ok,
                              "values_found": ok_vals.get(r["row_id"], False)})
    rate = aligned / len(value_rows) if value_rows else 0.0
    return {"status": "kept" if rate >= KEEP_THRESHOLD else "excluded_alignment", "match_rate": rate,
            "aligned_rows": aligned, "value_rows": len(value_rows), "unaligned": unaligned[:20],
            "header_rows_found": headers_found, "header_rows": len(header_rows),
            "pdf_value_tokens": len(toks), "gt_values": len(seq)}


# ----------------------------------------------------------------------------- driver
def main(raw_dir: str, out_dir: str) -> list[dict]:
    raw, out = Path(raw_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = []
    for fdir in sorted((raw / "filings").iterdir()):
        if not (fdir / "ixbrl_bs.json").exists():
            continue
        rec = {"docID": fdir.name}
        try:
            meta = json.loads((fdir / "meta.json").read_text(encoding="utf-8"))
            rec |= {k: meta.get(k) for k in ("edinetCode", "standard", "unit", "pages", "industry")}
            doc, stats = build_filing(fdir)
            al = align(doc, fdir / "bs_pages.pdf")
            doc["metadata"]["alignment"] = al
            write_all(doc, out / fdir.name, "ground_truth")
            rec |= stats | {"alignment_status": al["status"], "match_rate": al["match_rate"],
                            "aligned_rows": al.get("aligned_rows"), "value_rows": al.get("value_rows")}
        except Exception as e:                       # one bad filing never stops the build
            rec |= {"alignment_status": "build_error", "error": f"{type(e).__name__}: {e}"[:300]}
        report.append(rec)
        print(f"{rec['docID']}: {rec['alignment_status']}  match={rec.get('match_rate')}", flush=True)
    keys = []
    for r in report:
        keys += [k for k in r if k not in keys]
    with open(out / "gt_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(report)
    st = Counter(r["alignment_status"] for r in report)
    rates = [r["match_rate"] for r in report if r.get("match_rate") is not None]
    summary = {"filings": len(report), "status_counts": dict(st),
               "mean_match_rate": sum(rates) / len(rates) if rates else None,
               "kept_rate": st["kept"] / len(report) if report else None}
    (out / "gt_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(summary)
    return report


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
