"""Acceptance on fully correct pages AND on adversarially corrupted reads of the same pages.

Base pages (born-digital, ground truth known):
  reference         the reference page and its 3 geometry variants (gt/out/ground_truth.json)
  synthetic         the gt/synthetic_pages.py layout fixtures (ground truth from the generator spec)
Each base page is parsed clean, scored with docparse.evaluate.score_dimensions (page_correct), and
its acceptance decision recorded. Then, for every base page whose clean parse is page_correct, the
text layer the parser reads is corrupted one way at a time (a wrapper backend edits the spans, so the
whole real pipeline runs: merge, layout, hierarchy, checks, acceptance). Every corrupted output is
scored against the TRUE ground truth; a corrupted output that is not page_correct but is accepted is
an incorrect accepted page.

Corruptions (one per run; every applicable target on the page):
  unit_wrong              千円 -> 百万円 in the unit line
  unit_removed            unit line deleted
  period_dates_swapped    the date lines of the two column headers exchanged
  period_headers_swapped  whole column headers exchanged (labels and dates)
  value_columns_swapped   all values moved to the other column
  sign_dropped            △ removed from one value
  sign_added              △ added to one positive value
  digit_ones / digit_tens one digit of one value changed (+1 mod 10)
  consistent_value        one line item +1,000 and every total above it +1,000 (identities still hold)
  missing_cell            one value deleted
  missing_row             one row with values deleted (label and values)
  hierarchy_deeper / hierarchy_shallower   one line item's label moved one indent step
  label_typo              last character of one label replaced by 〇

The error model is "the reading is wrong" (as if OCR/VLM misread a clean page); on a real born-digital
page the text layer IS the page, so these runs measure what the acceptance rule can detect from the
output alone, not how often such errors happen. Dev data only: the reference page was used to build
the parser and the synthetic pages were written for this work.

usage: python stress/acceptance_eval.py <out_dir>
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gt"))
sys.path.insert(0, str(ROOT / "stress"))
from docparse.evaluate import score_dimensions  # noqa: E402
from docparse.extract import Span, TextLayerBackend  # noqa: E402
from docparse.normalize import parse_dates  # noqa: E402
from docparse.pipeline import parse_pdf  # noqa: E402
import synthetic_pages  # noqa: E402
from run_geometry import place  # noqa: E402
from run_stress import code_fingerprint  # noqa: E402

REF_PDF = ROOT / "tests/fixtures/balance_sheet_after_correction.pdf"
REF_GT = ROOT / "gt/out/ground_truth.json"
TOL = 1.0


# ----------------------------------------------------------------------------- span editing
def _inside(box, area, tol=TOL):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return area[0] - tol <= cx <= area[2] + tol and area[1] - tol <= cy <= area[3] + tol


def _rebuild(s: Span) -> Span | None:
    if not s.chars:
        return None
    s.text = "".join(c for c, _ in s.chars)
    b = [bb for _, bb in s.chars]
    s.bbox = (min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b))
    return s if s.text.strip() else None


def find_text(spans, text, area):
    """Positions [(span, char)] of the characters inside `area` whose text (spaces ignored, read left
    to right) equals `text`; None if they don't. Works across spans (a △ is often its own run)."""
    hits = sorted(((si, k) for si, s in enumerate(spans) for k, (c, bb) in enumerate(s.chars)
                   if not c.isspace() and _inside(bb, area)), key=lambda h: spans[h[0]].chars[h[1]][1][0])
    got = "".join(spans[si].chars[k][0] for si, k in hits)
    return hits if got == text.replace(" ", "") else None


def replace(spans, loc, new):
    """Replace the characters at `loc` by `new`, spread over the same horizontal extent (right edge
    kept, so right-aligned numbers stay in their column)."""
    first = spans[loc[0][0]].chars[loc[0][1]][1]
    last = spans[loc[-1][0]].chars[loc[-1][1]][1]
    x0, x1, y0, y1 = first[0], last[2], first[1], first[3]
    w = (x1 - x0) / max(len(new), 1)
    drop = set(loc)
    si0, k0 = loc[0]
    for si, s in enumerate(spans):
        kept = [(c, bb) for k, (c, bb) in enumerate(s.chars) if (si, k) not in drop]
        if si == si0:
            at = sum(1 for k in range(k0) if (si, k) not in drop)
            kept[at:at] = [(ch, (x0 + n * w, y0, x0 + (n + 1) * w, y1)) for n, ch in enumerate(new)]
        s.chars = kept
    return [x for x in (_rebuild(s) for s in spans) if x is not None]


def move_chars(spans, area, dx):
    for s in spans:
        if any(_inside(bb, area) for _, bb in s.chars):
            s.chars = [(c, (bb[0] + dx, bb[1], bb[2] + dx, bb[3]) if _inside(bb, area) else bb) for c, bb in s.chars]
            _rebuild(s)
    return spans


def delete_chars(spans, area):
    for s in spans:
        s.chars = [(c, bb) for c, bb in s.chars if not _inside(bb, area)]
    return [x for x in (_rebuild(s) for s in spans) if x is not None]


class CorruptedTextLayer:
    name = "text_layer_corrupted"

    def __init__(self, edit):
        self.edit = edit

    def read(self, page):
        content = TextLayerBackend().read(page)
        before = _signature(content.spans)
        content.spans = self.edit(copy.deepcopy(content.spans))
        self.applied = _signature(content.spans) != before
        return content


def _signature(spans):
    return [(c, tuple(round(v, 2) for v in bb)) for s in spans for c, bb in s.chars]


# ----------------------------------------------------------------------------- corruptions
def _fmt(v: int) -> str:
    return ("△" if v < 0 else "") + f"{abs(v):,}"


def corruptions(clean: dict):
    """Yield (type, target, edit function) for every applicable target on a correctly parsed page."""
    lay = clean["metadata"]["layout"]
    rights = lay["column_right_edges"]
    d = rights[1] - rights[0] if len(rights) == 2 else None
    steps = [b - a for a, b in zip(lay["indent_levels_x0"], lay["indent_levels_x0"][1:])]
    step = min(steps) if steps else 9.0
    pid_col = {p["id"]: k for k, p in enumerate(clean["periods"])}
    rows = clean["rows"]
    by_id = {r["row_id"]: r for r in rows}

    def val_area(c):
        return c["bbox"]

    def unit_wrong(sp):
        s = next((s for s in sp if "千円" in s.text and "単位" in s.text), None)
        if s is None:
            return sp
        k = s.text.find("千円")
        a = s.chars[k][1]
        b = s.chars[k + 1][1]
        return replace(sp, find_text(sp, "千円", (a[0], a[1], b[2], b[3])), "百万円")
    yield "unit_wrong", "unit", unit_wrong
    yield "unit_removed", "unit", lambda sp: [s for s in sp if "単位" not in s.text]
    if d:
        line_text = defaultdict(str)
        for h in lay["header_cells"]:
            line_text[h["line"]] += h["text"]
        date_lines = {ln for ln, t in line_text.items() if parse_dates(t)}   # not the 年度 label line
        dated = [h for h in lay["header_cells"] if h["line"] in date_lines and h["colspan"] == 1]
        allh = [h for h in lay["header_cells"] if h["colspan"] == 1]

        def swap_headers(cells):
            def edit(sp):
                moves = [(h["bbox"], d if h["columns"] == [0] else -d) for h in cells]
                marked = []
                for s in sp:                                  # decide every char once, then move
                    new = []
                    for c, bb in s.chars:
                        dx = next((m for a, m in moves if _inside(bb, a, 0.5)), 0.0)
                        new.append((c, (bb[0] + dx, bb[1], bb[2] + dx, bb[3])))
                    s.chars = new
                    marked.append(_rebuild(s))
                return [x for x in marked if x is not None]
            return edit
        yield "period_dates_swapped", "headers", swap_headers(dated)
        yield "period_headers_swapped", "headers", swap_headers(allh)

        def swap_values(sp):
            moves = [(c["bbox"], d if pid_col[pid] == 0 else -d) for r in rows for pid, c in r["cells"].items()]
            out = []
            for s in sp:
                new = []
                for ch, bb in s.chars:
                    dx = next((m for a, m in moves if _inside(bb, a, 0.5)), 0.0)
                    new.append((ch, (bb[0] + dx, bb[1], bb[2] + dx, bb[3])))
                s.chars = new
                out.append(_rebuild(s))
            return [x for x in out if x is not None]
        yield "value_columns_swapped", "values", swap_values
    for r in rows:
        for pid, c in r["cells"].items():
            tgt = f"{r['label_ja']}@{pid}"
            raw = c["raw_text"]
            if c["is_nil"]:
                continue
            loc = lambda sp, raw=raw, c=c: find_text(sp, raw, val_area(c))
            if c["is_negative"] and raw.startswith(("△", "▲")):
                yield "sign_dropped", tgt, lambda sp, loc=loc, raw=raw: (
                    lambda l: replace(sp, l, raw[1:]) if l else sp)(loc(sp))
            elif not c["is_negative"]:
                yield "sign_added", tgt, lambda sp, loc=loc, raw=raw: (
                    lambda l: replace(sp, l, "△" + raw) if l else sp)(loc(sp))
            digits = [k for k, ch in enumerate(raw) if ch.isdigit()]
            for name, pos in (("digit_ones", -1), ("digit_tens", -2)):
                if len(digits) >= -pos:
                    k = digits[pos]
                    new = raw[:k] + str((int(raw[k]) + 1) % 10) + raw[k + 1:]
                    yield name, tgt, lambda sp, loc=loc, new=new: (lambda l: replace(sp, l, new) if l else sp)(loc(sp))
            yield "missing_cell", tgt, lambda sp, c=c: delete_chars(sp, val_area(c))
            if r["row_type"] == "line_item":
                chain, cur = [(r, c)], r
                while cur.get("calc_parent") and pid in by_id[cur["calc_parent"]]["cells"]:
                    cur = by_id[cur["calc_parent"]]
                    chain.append((cur, cur["cells"][pid]))
                if all((x["reported_value"] or 0) + 1000 != 0 and
                       ((x["reported_value"] or 0) + 1000 < 0) == bool(x["is_negative"]) for _, x in chain):
                    def edit(sp, chain=chain):
                        for _, x in chain:
                            l = find_text(sp, x["raw_text"], x["bbox"])
                            if l:
                                sp = replace(sp, l, _fmt(x["reported_value"] + 1000))
                        return sp
                    yield "consistent_value", tgt, edit
    for r in rows:
        boxes = r["row_span"]["line_bboxes"] if r.get("row_span") else [r["label_bbox"]]
        lab = r["label_bbox"]
        if r["cells"]:
            yield "missing_row", r["label_ja"], lambda sp, boxes=boxes: _del_all(sp, boxes)
        if r["row_type"] == "line_item" and lab:
            yield "hierarchy_deeper", r["label_ja"], lambda sp, lab=lab: move_chars(sp, lab, step)
            yield "hierarchy_shallower", r["label_ja"], lambda sp, lab=lab: move_chars(sp, lab, -step)
        if lab and r["label_ja"]:
            last = r["label_ja"][-1]
            yield "label_typo", r["label_ja"], lambda sp, lab=lab, last=last: (
                lambda l: replace(sp, l, "〇") if l else sp)(_find_last(sp, last, lab))


def _del_all(sp, boxes):
    for b in boxes:
        sp = delete_chars(sp, b)
    return sp


def _find_last(sp, ch, area):
    hits = [(si, k) for si, s in enumerate(sp) for k, (c, bb) in enumerate(s.chars)
            if c == ch and _inside(bb, area)]
    return [max(hits, key=lambda h: sp[h[0]].chars[h[1]][1][2])] if hits else None


# ----------------------------------------------------------------------------- runner
def base_pages(tmp: Path) -> list[tuple[str, str, dict]]:
    gt_ref = json.loads(REF_GT.read_text(encoding="utf-8"))
    pages = [("reference/original", str(REF_PDF), gt_ref)]
    for kind in ("scale80", "shift40", "larger_page"):
        p = tmp / f"{kind}.pdf"
        place(str(REF_PDF), kind, str(p))
        pages.append((f"reference/{kind}", str(p), gt_ref))
    for v in synthetic_pages.VARIANTS:
        p = tmp / f"{v}.pdf"
        pages.append((f"synthetic/{v}", str(p), synthetic_pages.draw(p, v)))
    return pages


def main(out_dir: str) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    clean_rows, runs, not_applied = [], [], []
    with tempfile.TemporaryDirectory() as td:
        for name, pdf, gt in base_pages(Path(td)):
            clean = parse_pdf(pdf)
            sd = score_dimensions(gt, clean)
            clean_rows.append({"page": name, "page_correct": sd["page_correct"],
                               "decision": clean["acceptance"]["decision"],
                               "reasons": clean["acceptance"]["reasons"], "overall_score": sd["overall_score"]})
            if not sd["page_correct"]:
                continue
            for ctype, target, edit in corruptions(clean):
                backend = CorruptedTextLayer(edit)
                pred = parse_pdf(pdf, safe=True, force_route="visual", ocr=backend)
                if not backend.applied:              # the edit found nothing to change: not a run
                    not_applied.append({"page": name, "type": ctype, "target": target})
                    continue
                s = score_dimensions(gt, pred)
                runs.append({"page": name, "type": ctype, "target": target, "page_correct": s["page_correct"],
                             "decision": pred["acceptance"]["decision"],
                             "reasons": [r.split()[0] for r in pred["acceptance"]["reasons"]],
                             "failed_dims": sorted({x["dimension"] for x in s["differences"]})})
    by_type = defaultdict(lambda: {"runs": 0, "incorrect": 0, "incorrect_accepted": 0, "no_op": 0,
                                   "no_op_accepted": 0, "examples_accepted": []})
    for r in runs:
        b = by_type[r["type"]]
        b["runs"] += 1
        if r["page_correct"]:
            b["no_op"] += 1
            b["no_op_accepted"] += r["decision"] == "accept"
        else:
            b["incorrect"] += 1
            if r["decision"] == "accept":
                b["incorrect_accepted"] += 1
                if len(b["examples_accepted"]) < 3:
                    b["examples_accepted"].append(f"{r['page']}: {r['target']} ({', '.join(r['failed_dims'])})")
    # the view that measures detection: runs on pages whose clean parse is correct AND accepted (a page
    # that is in review anyway can't show whether a corruption would have been caught)
    accepted_clean = {c["page"] for c in clean_rows if c["page_correct"] and c["decision"] == "accept"}
    on_accepted = defaultdict(lambda: {"runs": 0, "accepted": 0, "caught_by": defaultdict(int)})
    for r in runs:
        if r["page"] in accepted_clean and not r["page_correct"]:
            b = on_accepted[r["type"]]
            b["runs"] += 1
            b["accepted"] += r["decision"] == "accept"
            for reason in (r["reasons"] if r["decision"] == "review" else []):
                b["caught_by"][reason] += 1
    correct = [c for c in clean_rows if c["page_correct"]]
    incorrect_runs = [r for r in runs if not r["page_correct"]]
    summary = {
        "fingerprint": code_fingerprint(),
        "seconds": round(time.time() - t0, 1),
        "correct_pages": len(correct),
        "correct_pages_accepted": sum(c["decision"] == "accept" for c in correct),
        "correct_pages_sent_to_review": sum(c["decision"] == "review" for c in correct),
        "incorrect_clean_pages": len(clean_rows) - len(correct),
        "incorrect_clean_pages_accepted": sum(c["decision"] == "accept" for c in clean_rows if not c["page_correct"]),
        "corrupted_runs": len(runs),
        "corrupted_incorrect": len(incorrect_runs),
        "corrupted_incorrect_accepted": sum(r["decision"] == "accept" for r in incorrect_runs),
        "corrupted_no_op": len(runs) - len(incorrect_runs),
        "edits_not_applied": len(not_applied),
        "pages_accepted_clean": len(accepted_clean),
        "runs_on_pages_accepted_clean": sum(b["runs"] for b in on_accepted.values()),
        "accepted_on_pages_accepted_clean": sum(b["accepted"] for b in on_accepted.values()),
    }
    res = {"summary": summary, "clean_pages": clean_rows, "by_type": dict(by_type),
           "by_type_on_pages_accepted_clean": {t: {**b, "caught_by": dict(b["caught_by"])}
                                               for t, b in on_accepted.items()}, "runs": runs,
           "not_applied": not_applied}
    (out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "results.md").write_text(to_markdown(res), encoding="utf-8")
    return res


def to_markdown(res: dict) -> str:
    s = res["summary"]
    L = ["# Acceptance on correct and corrupted pages", "",
         f"Pipeline fingerprint `{s['fingerprint']}`. Generated by `stress/acceptance_eval.py`; dev data only.", "",
         "## Clean pages", "", "| Page | Page correct | Decision | Reasons |", "|---|---|---|---|"]
    for c in res["clean_pages"]:
        L.append(f"| {c['page']} | {'yes' if c['page_correct'] else 'no'} | {c['decision']} | "
                 f"{'; '.join(r[:90] for r in c['reasons']) or '-'} |")
    L += ["", f"Correct pages accepted (coverage): **{s['correct_pages_accepted']}/{s['correct_pages']}**; "
              f"correct pages sent to review: **{s['correct_pages_sent_to_review']}/{s['correct_pages']}**; "
              f"incorrect clean parses accepted: **{s['incorrect_clean_pages_accepted']}/{s['incorrect_clean_pages']}**.",
          "", f"## Detection: corrupted reads of the {s['pages_accepted_clean']} pages accepted when clean", "",
          "| Corruption | Runs | Incorrect and accepted | Rate | Caught by (reasons on the review runs) |",
          "|---|--:|--:|--:|---|"]
    for t, b in res["by_type_on_pages_accepted_clean"].items():
        cb = ", ".join(f"{k} {v}" for k, v in sorted(b["caught_by"].items())) or "–"
        L.append(f"| {t} | {b['runs']} | {b['accepted']} | {b['accepted'] / b['runs']:.1%} | {cb} |")
    n, a = s["runs_on_pages_accepted_clean"], s["accepted_on_pages_accepted_clean"]
    L += [f"| **all** | {n} | {a} | {a / n:.1%} | |", "",
          "## All corrupted reads of the correct pages (including the page that is in review anyway)", "",
          "| Corruption | Runs | Output incorrect | Incorrect and accepted | Rate | No-op (output still correct) |",
          "|---|--:|--:|--:|--:|--:|"]
    for t, b in res["by_type"].items():
        rate = f"{b['incorrect_accepted'] / b['incorrect']:.1%}" if b["incorrect"] else "n/a"
        L.append(f"| {t} | {b['runs']} | {b['incorrect']} | {b['incorrect_accepted']} | {rate} | {b['no_op']} |")
    tot = s["corrupted_incorrect"]
    L += [f"| **all** | {s['corrupted_runs']} | {tot} | {s['corrupted_incorrect_accepted']} | "
          f"{(s['corrupted_incorrect_accepted'] / tot if tot else 0):.1%} | {s['corrupted_no_op']} |", "",
          "Examples of incorrect pages that were accepted:", ""]
    for t, b in res["by_type"].items():
        for e in b["examples_accepted"]:
            L.append(f"- {t}: {e}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    r = main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "stress/out_acceptance_eval"))
    print(json.dumps(r["summary"], indent=1))
    for t, b in r["by_type"].items():
        print(f"{t:24s} runs {b['runs']:4d} incorrect {b['incorrect']:4d} accepted-incorrect {b['incorrect_accepted']:4d}")
