"""VLM backend for the visual (scan) route: an open vision-language model proposes the table, and
the existing pipeline (layout, hierarchy, identity checks, acceptance rule) verifies it.

Flow for one page:
  1. page image  the scan's embedded image exactly as stored in the PDF (no re-render, no deskew);
                 a page without a single full-page image is rendered at 300 dpi (grey).
  2. response    looked up in the cache by (sha256 of the image bytes, model, settings, prompt);
                 on a miss, a runner (e.g. TransformersRunner on a GPU) is called and its raw text
                 is cached. With no runner, a miss raises VLMCacheMiss -> the page fails -> review.
  3. parse       strict JSON (one object, optionally inside one ```json fence); anything else, or
                 any field of the wrong type, or a value the shared number parser rejects, raises
                 VLMSchemaError. Responses are rejected, never repaired.
  4. layout      the table is laid out as synthetic positioned spans (labels indented by level,
                 values right-aligned in columns, period headers over their columns) so that
                 everything downstream is shared with the text-layer and OCR routes.

The model never sees the checks, and nothing here retries or re-prompts on a failed check: the
VLM's read is verified, not optimised against the verifier.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
from pathlib import Path

import pymupdf
from PIL import Image

import difflib

from .extract import PageContent, Span, TesseractBackend
from .normalize import label_norm, parse_dates, parse_unit, parse_value

DEFAULT_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
# Fixed before any VLM output was seen (logged in stress/FREEZE_LOG.txt). Part of the cache key.
DEFAULT_SETTINGS = {"max_side": 2200, "max_new_tokens": 6144, "do_sample": False, "quant": "4bit"}
PROMPT_VERSION = "v1"
PROMPT = """You are reading one page of a Japanese financial statement (for example 連結貸借対照表).
Transcribe the table exactly as printed. Output ONLY one JSON object and nothing else, with this shape:
{"heading_lines": ["..."], "columns": ["...", "..."], "rows": [{"label": "...", "indent": 0, "values": ["...", "..."]}]}

Rules:
- heading_lines: every text line printed above the table's column headers, top to bottom, verbatim
  (the statement title, marks such as 【訂正後】, and the unit line such as (単位：千円)).
- columns: the header text of each value column, left to right, verbatim, joined onto one line
  (for example "前連結会計年度(2025年3月31日)").
- rows: every table row from top to bottom, including section headings that have no values
  (such as 資産の部 or 流動資産) and total rows (…合計).
- label: the row label verbatim, without leading spaces; keep note markers such as ※1 as printed.
- indent: the row's visible indentation: 0 for the leftmost labels, one more for each further step.
- values: exactly one entry per column, the printed text verbatim: the digits with their commas, the
  minus mark △ or ▲ if one is printed, "－" if a dash is printed, and null if the cell is blank.
  Do not calculate, correct, convert units, or fill in anything that is not printed."""

FULL_PAGE_IMAGE = 0.95          # an image covering this share of the page counts as "the scan"


class VLMSchemaError(ValueError):
    """The model's response is not a valid table; the page fails (never repaired)."""


class VLMCacheMiss(RuntimeError):
    """No cached response for this image/model/settings and no runner configured."""


# ----------------------------------------------------------------------------- image + cache keys
def page_image(page: pymupdf.Page) -> tuple[bytes, str]:
    """(image bytes, how they were obtained). Embedded full-page image stream as stored, else a render."""
    doc, area = page.parent, abs(page.rect)
    for info in page.get_images(full=True):
        xref = info[0]
        rects = page.get_image_rects(xref)
        if rects and abs(rects[0]) >= FULL_PAGE_IMAGE * area:
            img = doc.extract_image(xref)
            return img["image"], f"embedded:{img['ext']}"
    pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
    return pix.tobytes("png"), "render:300dpi"


def pdf_image_sha256(path: str | Path, page_index: int = 0) -> str:
    """Identity of a scanned page: sha256 of the image bytes the VLM is shown (the PDF file's own
    hash is not stable: pymupdf writes a random trailer /ID on every save)."""
    return hashlib.sha256(page_image(pymupdf.open(path)[page_index])[0]).hexdigest()


def settings_key(model: str, settings: dict) -> str:
    blob = json.dumps({"model": model, "settings": settings, "prompt_version": PROMPT_VERSION,
                       "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest()}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def cache_file(cache_dir: str | Path, model: str, settings: dict, image_sha: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", model)
    return Path(cache_dir) / slug / f"{image_sha[:20]}_{settings_key(model, settings)}.json"


# ----------------------------------------------------------------------------- strict parsing
_FENCE = re.compile(r"\A\s*```(?:json)?\s*\n(.*)\n\s*```\s*\Z", re.S)


def parse_response(text: str) -> dict:
    """Exactly one JSON object (optionally inside a single ```json fence) -> validated table."""
    m = _FENCE.match(text)
    body = m.group(1) if m else text
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as e:
        raise VLMSchemaError(f"not a single JSON object: {e.msg} at char {e.pos}") from None
    return validate_table(obj)


def validate_table(obj) -> dict:
    if not isinstance(obj, dict):
        raise VLMSchemaError("top level is not an object")
    extra = set(obj) - {"heading_lines", "columns", "rows"}
    if extra:
        raise VLMSchemaError(f"unexpected keys {sorted(extra)}")
    heads, cols, rows = obj.get("heading_lines"), obj.get("columns"), obj.get("rows")
    if not (isinstance(heads, list) and all(isinstance(h, str) for h in heads)):
        raise VLMSchemaError("heading_lines must be a list of strings")
    if not (isinstance(cols, list) and len(cols) >= 1 and all(isinstance(c, str) and c.strip() for c in cols)):
        raise VLMSchemaError("columns must be a non-empty list of non-empty strings")
    if not (isinstance(rows, list) and rows):
        raise VLMSchemaError("rows must be a non-empty list")
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or set(r) != {"label", "indent", "values"}:
            raise VLMSchemaError(f"row {i}: must have exactly label, indent, values")
        if not (isinstance(r["label"], str) and r["label"].strip()):
            raise VLMSchemaError(f"row {i}: empty label")
        if not (isinstance(r["indent"], int) and not isinstance(r["indent"], bool) and 0 <= r["indent"] <= 8):
            raise VLMSchemaError(f"row {i}: indent must be an integer 0..8")
        vals = r["values"]
        if not (isinstance(vals, list) and len(vals) == len(cols)):
            raise VLMSchemaError(f"row {i}: values must have one entry per column ({len(cols)})")
        for v in vals:
            if v is None or v == "":
                continue
            if not isinstance(v, str) or parse_value(v) is None:
                raise VLMSchemaError(f"row {i}: value {v!r} is not a printed number or dash")
    return obj


# ----------------------------------------------------------------------------- table -> spans
SIZE = 9.0          # synthetic font size (pt); every tolerance downstream is relative to it
PITCH = 13.0        # synthetic row pitch
COL_STEP = 110.0    # distance between value-column right edges
LEFT = 40.0


def table_to_content(table: dict, page_index: int, width: float, height: float, diagnostics: dict) -> PageContent:
    ncol = len(table["columns"])
    rights = [width - 55.0 - (ncol - 1 - k) * COL_STEP for k in range(ncol)]
    label_limit = rights[0] - COL_STEP + 2 * SIZE        # labels must end left of the first column
    spans: list[Span] = []
    src = "vlm"

    def add(text, x0, y, x1):
        spans.append(Span(text, (x0, y, x1, y + SIZE), SIZE, source=src, conf=1.0))

    y = 40.0
    for h in table["heading_lines"]:
        h = h.strip()
        if h:
            add(h, LEFT, y, LEFT + min(len(h) * SIZE, label_limit - LEFT))
            y += PITCH
    y += 4.0
    for k, c in enumerate(table["columns"]):              # period header over its column
        add(re.sub(r"\s+", "", c), rights[k] - 100.0, y, rights[k])
    y += 2 * PITCH
    for r in table["rows"]:
        x0 = LEFT + r["indent"] * SIZE
        label = r["label"].strip()
        add(label, x0, y, min(x0 + len(label) * SIZE, label_limit))
        for k, v in enumerate(r["values"]):
            if v is None or v == "":
                continue
            v = v.strip()
            add(v, rights[k] - max(len(v), 1) * 0.55 * SIZE, y, rights[k])
        y += PITCH
    return PageContent(page_index, width, height, spans, None, src, diagnostics)


# ----------------------------------------------------------------------------- second reading (A8)
# Amendment A6-A8 (2026-10-25, stress/FREEZE_LOG.txt): the VLM's read is cross-checked against an
# independent Tesseract reading of the same page, and row indentation is taken from the page
# geometry (Tesseract label x0), not from the model's "indent" field.
_SECOND: dict[str, dict] = {}            # in-process cache by image sha256 (OCR is deterministic)


def second_reading(page: pymupdf.Page, image_sha: str, ocr=None) -> dict:
    if image_sha in _SECOND:
        return _SECOND[image_sha]
    from .layout import analyze
    from .merge import merge_tokens
    try:
        content = (ocr or TesseractBackend()).read(page)
    except Exception as e:                                   # noqa: BLE001
        return {"status": "unavailable", "reason": type(e).__name__}
    lay = analyze(merge_tokens(content.spans))
    band_unit, band_text = None, ""
    try:                                                     # header band read on its own (psm 6)
        band_text = header_band_text(page, lay.body[0].y0 if lay.body else 0.25 * page.rect.height)
        u = parse_unit(band_text)
        band_unit = u[0] if u else None
    except Exception:                                        # noqa: BLE001
        pass
    rows = []
    for br in lay.body:
        vals = {}
        for ci, tok in br.values.items():
            pv = parse_value(tok.text)
            if pv is not None and pv.reported_value is not None:
                vals[ci] = abs(pv.reported_value)
        rows.append({"label_norm": label_norm(br.label_text)[0],
                     "x0": br.label_tokens[0].x0 if br.label_tokens else None, "values": vals})
    out = {"status": "ok", "unit": band_unit or (lay.unit[0] if lay.unit else None),
           "unit_source": "header band" if band_unit else ("full page" if lay.unit else None),
           "dates": [c.period.get("date") if c.period else None for c in lay.columns],
           "n_columns": len(lay.columns), "char_size": lay.char_size, "rows": rows,
           "deskew_deg": content.diagnostics.get("deskew_deg")}
    _SECOND[image_sha] = out
    return out


def header_band_text(page: pymupdf.Page, body_top_pt: float, dpi: int = 300) -> str:
    """OCR of the strip above the table body alone (the full-page pass garbles the small unit line:
    on the clean reference page it read 「（単位：千円）」 as noise). Same render, deskew and
    model as the full-page pass; psm 6; fixed before any held-out VLM output."""
    import os
    import subprocess
    import tempfile
    from .deskew import deskew as _deskew
    be = TesseractBackend()
    be.check_setup()
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    img, _ = _deskew(Image.frombytes("L", (pix.width, pix.height), pix.samples))
    cut = min(img.height, int((body_top_pt + 4) * dpi / 72))
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "band.png")
        img.crop((0, 0, img.width, cut)).save(f)
        out = subprocess.run(["tesseract", f, "stdout", "-l", be.lang, "--psm", "6"], capture_output=True,
                             text=True, env=be._env())
    return out.stdout


def align_rows(vlm_labels: list[str], ocr_labels: list[str]) -> dict[int, int]:
    """VLM row index -> OCR row index: label-sequence alignment, then pairing by order inside
    'replace' gaps of equal length (a misread label still pairs with its own row)."""
    m: dict[int, int] = {}
    sm = difflib.SequenceMatcher(a=vlm_labels, b=ocr_labels, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                m[i1 + k] = j1 + k
    return m


def derive_levels(table: dict, second: dict) -> tuple[list[int] | None, str]:
    """Levels for every VLM row from the OCR label x0 of its aligned row, or None (with why)."""
    if second.get("status") != "ok":
        return None, "no second reading"
    from .hierarchy import cluster_1d
    m = align_rows([label_norm(r["label"])[0] for r in table["rows"]], [r["label_norm"] for r in second["rows"]])
    xs = [second["rows"][m[i]]["x0"] if i in m else None for i in range(len(table["rows"]))]
    missing = sum(x is None for x in xs)
    if missing:
        return None, f"{missing} of {len(xs)} rows not matched to a page row"
    centers = cluster_1d(xs, tol=0.4 * second["char_size"])
    return [min(range(len(centers)), key=lambda k: abs(centers[k] - x)) for x in xs], "page geometry"


def cross_check(table: dict, second: dict, levels_why: str, levels_ok: bool) -> dict:
    problems: list[str] = []
    if second.get("status") != "ok":
        return {"ok": False, "problems": [f"second reading unavailable ({second.get('reason')})"]}
    vu = parse_unit(" ".join(table["heading_lines"]))
    vu = vu[0] if vu else None
    if vu is None or second["unit"] is None:
        problems.append(f"unit not read by both (VLM {vu}, OCR {second['unit']})")
    elif vu != second["unit"]:
        problems.append(f"unit differs (VLM {vu}, OCR {second['unit']})")
    vdates = [(parse_dates(c) or [None])[-1] for c in table["columns"]]
    if None in vdates or None in second["dates"] or vdates != second["dates"]:
        problems.append(f"column dates differ or missing (VLM {vdates}, OCR {second['dates']})")
    # column assignment of values: each VLM column must overlap most with the same OCR column
    ncol = len(table["columns"])
    vcols = [{abs(pv.reported_value) for r in table["rows"] if r["values"][k]
              for pv in [parse_value(r["values"][k])] if pv is not None and pv.reported_value is not None}
             for k in range(ncol)]
    ocols = [{r["values"][j] for r in second["rows"] if j in r["values"]} for j in range(second["n_columns"])]
    overlap = [[len(v & o) for o in ocols] for v in vcols]
    if second["n_columns"] != ncol:
        problems.append(f"column count differs (VLM {ncol}, OCR {second['n_columns']})")
    else:
        for k in range(ncol):
            others = [overlap[k][j] for j in range(ncol) if j != k]
            if not overlap[k][k] or (others and overlap[k][k] <= max(others)):
                problems.append(f"values of VLM column {k} do not match OCR column {k} best (overlap {overlap[k]})")
    if not levels_ok:
        problems.append(f"indentation not confirmed: {levels_why}")
    return {"ok": not problems, "problems": problems, "vlm_unit": vu, "ocr_unit": second["unit"],
            "vlm_dates": vdates, "ocr_dates": second["dates"], "value_overlap": overlap,
            "indent_source": levels_why if levels_ok else "vlm (unconfirmed)"}


# ----------------------------------------------------------------------------- backend
class VLMBackend:
    """Visual-route backend. `runner(image_bytes, prompt, settings) -> (raw_text, info)` is optional;
    without one, only cached responses are used (offline, reproducible scoring)."""
    name = "vlm"

    def __init__(self, cache_dir: str | Path = "vlm_cache", model: str = DEFAULT_MODEL,
                 settings: dict | None = None, runner=None, second_reader: str | None = "tesseract"):
        self.cache_dir, self.model = Path(cache_dir), model
        self.settings = dict(DEFAULT_SETTINGS | (settings or {}))
        self.runner = runner
        self.second_reader = second_reader          # None: no cross-check -> A8 sends every page to review

    def response_for(self, image: bytes) -> tuple[str, dict]:
        sha = hashlib.sha256(image).hexdigest()
        path = cache_file(self.cache_dir, self.model, self.settings, sha)
        if path.exists():
            rec = json.loads(path.read_text(encoding="utf-8"))
            return rec["raw_text"], {"cache": "hit", "cache_file": str(path), "image_sha256": sha,
                                     "run_info": rec.get("run_info", {})}
        if self.runner is None:
            raise VLMCacheMiss(f"no cached {self.model} response for image {sha[:16]} "
                               f"(settings {settings_key(self.model, self.settings)}) and no runner")
        t0 = time.time()
        raw, info = self.runner(image, PROMPT, self.settings)
        rec = {"model": self.model, "settings": self.settings, "prompt_version": PROMPT_VERSION,
               "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(), "image_sha256": sha,
               "raw_text": raw, "run_info": info | {"seconds": round(time.time() - t0, 1)}}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return raw, {"cache": "written", "cache_file": str(path), "image_sha256": sha, "run_info": rec["run_info"]}

    def read(self, page: pymupdf.Page) -> PageContent:
        image, how = page_image(page)
        raw, info = self.response_for(image)
        table = parse_response(raw)                       # VLMSchemaError -> page failed -> review
        second = (second_reading(page, info["image_sha256"]) if self.second_reader == "tesseract"
                  else {"status": "unavailable", "reason": "no second reader configured"})
        levels, why = derive_levels(table, second)
        vlm_indent = [r["indent"] for r in table["rows"]]
        if levels is not None:                            # geometry wins over the model's indent field
            for r, lv in zip(table["rows"], levels):
                r["indent"] = lv
        cc = cross_check(table, second, why, levels is not None)
        diag = {"model": self.model, "settings_key": settings_key(self.model, self.settings),
                "image_source": how, "vlm_rows": len(table["rows"]), "cross_check": cc,
                "indent_changed_rows": sum(a != b for a, b in zip(vlm_indent, [r["indent"] for r in table["rows"]])),
                "second_reading": {k: second.get(k) for k in ("status", "reason", "unit", "unit_source", "dates",
                                                              "deskew_deg")}
                | {"rows": len(second.get("rows", []))}} | info
        return table_to_content(table, page.number, page.rect.width, page.rect.height, diag)


# ----------------------------------------------------------------------------- live runner (GPU)
class TransformersRunner:
    """Runs an open VLM with Hugging Face transformers (Colab or a local GPU). Greedy decoding.

    quant: "4bit" (bitsandbytes nf4 weights, fp16 compute, ~7 GB for an 8B model by estimate: a free
    Colab T4; the default) or "none" (bf16 on Ampere or newer, ~18 GB: L4 / A100). quant is part of
    the cache key, so the two never mix. The compute dtype is recorded with every response.
    """

    def __init__(self, model: str = DEFAULT_MODEL, quant: str = "4bit"):
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        self.torch, self.model_id = torch, model
        gpu_gb = torch.cuda.get_device_properties(0).total_memory / 2**30 if torch.cuda.is_available() else 0
        if quant not in ("4bit", "none"):
            raise ValueError("quant must be '4bit' or 'none'")
        self.quant = quant
        # Compute dtype: fp16 on pre-Ampere GPUs (T4 = compute capability 7.5 has no native bf16;
        # torch may report emulated bf16 as "supported", so the capability is checked instead).
        cap = torch.cuda.get_device_capability(0) if torch.cuda.is_available() else (0, 0)
        dtype = torch.bfloat16 if (self.quant == "none" and cap[0] >= 8) else torch.float16
        kw = {"device_map": "auto", "torch_dtype": dtype}
        if self.quant == "4bit":
            from transformers import BitsAndBytesConfig
            # the vision encoder stays unquantized in fp16 (small, and it is what reads the digits)
            kw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                           bnb_4bit_compute_dtype=torch.float16,
                                                           llm_int8_skip_modules=["visual", "lm_head"])
        self.processor = AutoProcessor.from_pretrained(model)
        self.model = AutoModelForImageTextToText.from_pretrained(model, **kw).eval()
        import transformers
        self.info = {"model": model, "quant": self.quant, "gpu": torch.cuda.get_device_name(0)
                     if torch.cuda.is_available() else "cpu", "gpu_gb": round(gpu_gb, 1),
                     "transformers": transformers.__version__, "torch": torch.__version__,
                     "compute_dtype": str(dtype).replace("torch.", ""), "gpu_capability": list(cap),
                     "model_revision": self._revision(model)}

    def _revision(self, model: str) -> str | None:
        """Commit sha of the weights actually loaded: the local snapshot folder name, else the hub's
        current sha (config._commit_hash was null on Colab run 1)."""
        try:
            from huggingface_hub import snapshot_download
            return Path(snapshot_download(model, local_files_only=True)).name
        except Exception:                                    # noqa: BLE001
            pass
        try:
            from huggingface_hub import model_info
            return model_info(model).sha
        except Exception:                                    # noqa: BLE001
            return getattr(self.model.config, "_commit_hash", None)

    def __call__(self, image: bytes, prompt: str, settings: dict) -> tuple[str, dict]:
        if settings["quant"] != self.quant:
            raise ValueError(f"settings say quant={settings['quant']} but the model was loaded with {self.quant}")
        img = Image.open(io.BytesIO(image)).convert("RGB")
        scale = min(1.0, settings["max_side"] / max(img.size))
        if scale < 1.0:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        msgs = [{"role": "user", "content": [{"type": "image", "image": img}, {"type": "text", "text": prompt}]}]
        inputs = self.processor.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True,
                                                    return_dict=True, return_tensors="pt").to(self.model.device)
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=settings["max_new_tokens"],
                                      do_sample=settings["do_sample"])
        new = out[0, inputs["input_ids"].shape[1]:]
        text = self.processor.decode(new, skip_special_tokens=True)
        return text, self.info | {"fed_image_size": list(img.size), "new_tokens": int(new.shape[0]),
                                  "hit_token_limit": int(new.shape[0]) >= settings["max_new_tokens"]}
