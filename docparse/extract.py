"""Extraction backends. Each returns a PageContent: positioned text spans plus (when the source
has them) horizontal vector segments for rules and underlines.

  TextLayerBackend  born-digital PDFs: spans with per-glyph boxes + vector drawings
  TesseractBackend  visual path: renders the page, OCR word boxes (no vector data)
  VLMBackend        visual path: marked stub (contract documented below)
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

import pymupdf
from PIL import Image

from .deskew import deskew as _deskew

MAX_SEGMENT_THICKNESS = 3.0   # pt; anything thicker is a shaded box, not a rule/underline candidate


@dataclass
class Span:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    font: str = ""
    chars: list[tuple[str, tuple[float, float, float, float]]] = field(default_factory=list)
    source: str = "text_layer"
    conf: float = 1.0


@dataclass(frozen=True)
class Segment:
    x0: float
    y0: float
    x1: float
    y1: float
    thickness: float
    kind: str                     # "rect" | "line"

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class PageContent:
    page_index: int
    width: float
    height: float
    spans: list[Span]
    segments: list[Segment] | None     # None = source has no vector data (scans, OCR)
    source: str
    diagnostics: dict = field(default_factory=dict)


def _is_dark(color) -> bool:
    if color is None:
        return False
    c = tuple(color)
    if len(c) == 1:
        return c[0] < 0.5
    if len(c) == 3:
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] < 0.5
    if len(c) == 4:                       # CMYK
        return c[3] > 0.5 or sum(c[:3]) / 3 > 0.7
    return False


def horizontal_segments(page: pymupdf.Page) -> list[Segment]:
    segs: set[Segment] = set()
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "re":
                r = it[1]
                if _is_dark(d.get("fill")) and r.width > r.height and r.height <= MAX_SEGMENT_THICKNESS:
                    segs.add(Segment(round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2),
                                     round(r.height, 2), "rect"))
            elif it[0] == "l":
                p1, p2 = it[1], it[2]
                w = d.get("width") or 1.0
                if _is_dark(d.get("color")) and abs(p1.y - p2.y) < 0.5 and w <= MAX_SEGMENT_THICKNESS:
                    x0, x1 = sorted((p1.x, p2.x))
                    y = (p1.y + p2.y) / 2
                    segs.add(Segment(round(x0, 2), round(y - w / 2, 2), round(x1, 2), round(y + w / 2, 2),
                                     round(w, 2), "line"))
    return sorted(segs, key=lambda s: (s.y0, s.x0))


class TextLayerBackend:
    name = "text_layer"

    def read(self, page: pymupdf.Page) -> PageContent:
        spans = []
        for b in page.get_text("rawdict")["blocks"]:
            for l in b.get("lines", []):
                if l.get("dir", (1, 0))[1] != 0:      # rotated text lines: out of scope here
                    continue
                for s in l["spans"]:
                    chars = [(c["c"], tuple(c["bbox"])) for c in s["chars"]]
                    text = "".join(c for c, _ in chars)
                    if text.strip():
                        spans.append(Span(text, tuple(s["bbox"]), s["size"], s["font"], chars))
        return PageContent(page.number, page.rect.width, page.rect.height, spans,
                           horizontal_segments(page), self.name)


class OCRError(RuntimeError):
    """The OCR backend could not produce text for a page."""


class OCRSetupError(OCRError):
    """Tesseract or its language model is missing. See scripts/setup_tesseract.sh."""


class OCRNoTextError(OCRError):
    """OCR ran but returned zero words: never treat that as an empty table."""


_PROJECT_TESSDATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tessdata")


class TesseractBackend:
    """OCR word boxes from the Tesseract CLI. Needs the `jpn` model (tessdata_best).

    Model lookup: explicit `tessdata` argument, else $TESSDATA_PREFIX, else ./tessdata in the
    project (created by scripts/setup_tesseract.sh), else the system default.
    TSV is requested with -c tessedit_create_tsv=1, so no `configs/tsv` file is needed.
    Settings are fixed up front (psm 6 = one uniform block, render at 300 dpi) and are not tuned
    per document. The rendered page is deskewed (docparse.deskew) before OCR; box coordinates are
    therefore in the levelled frame, which is what row/column grouping needs.
    """
    name = "ocr_tesseract"

    def __init__(self, lang: str = "jpn", psm: int = 6, dpi: int = 300, tessdata: str | None = None,
                 deskew: bool = True):
        self.lang, self.psm, self.dpi, self.deskew = lang, psm, dpi, deskew
        self.tessdata = tessdata or os.environ.get("TESSDATA_PREFIX") or (
            _PROJECT_TESSDATA if os.path.isdir(_PROJECT_TESSDATA) else None)

    def _env(self) -> dict:
        env = dict(os.environ)
        if self.tessdata:
            env["TESSDATA_PREFIX"] = self.tessdata
        return env

    def check_setup(self) -> None:
        if shutil.which("tesseract") is None:
            raise OCRSetupError("tesseract binary not found; install tesseract-ocr (see scripts/setup_tesseract.sh)")
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, env=self._env())
        langs = {l.strip() for l in (out.stdout + out.stderr).splitlines()[1:]}
        missing = [l for l in self.lang.split("+") if l not in langs]
        if missing:
            raise OCRSetupError(f"tesseract language model(s) {missing} not found in "
                                f"{self.tessdata or 'system tessdata'}; run scripts/setup_tesseract.sh "
                                f"and export TESSDATA_PREFIX")

    def read(self, page: pymupdf.Page) -> PageContent:
        self.check_setup()
        scale = self.dpi / 72.0
        pix = page.get_pixmap(dpi=self.dpi, colorspace=pymupdf.csGRAY)
        image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        angle = 0.0
        if self.deskew:                       # level the page before OCR and line grouping
            image, angle = _deskew(image)
        with tempfile.TemporaryDirectory() as td:
            img = os.path.join(td, "page.png")
            image.save(img)
            proc = subprocess.run(["tesseract", img, "stdout", "-l", self.lang, "--psm", str(self.psm),
                                   "-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"],
                                  capture_output=True, text=True, env=self._env())
        if proc.returncode != 0:
            raise OCRError(f"tesseract exited {proc.returncode}: {proc.stderr.strip()[:300]}")
        out = proc.stdout
        if not out.startswith("level\t"):
            raise OCRError(f"tesseract did not return TSV (stderr: {proc.stderr.strip()[:300]})")
        spans = []
        for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
            if row.get("level") != "5" or not (row.get("text") or "").strip():
                continue
            x, y, w, h = (int(row[k]) / scale for k in ("left", "top", "width", "height"))
            spans.append(Span(row["text"], (x, y, x + w, y + h), h, source=self.name,
                              conf=max(float(row["conf"]), 0.0) / 100.0))
        if not spans:
            raise OCRNoTextError(f"OCR returned zero words for page {page.number} "
                                 f"(lang={self.lang}, psm={self.psm}, dpi={self.dpi})")
        return PageContent(page.number, page.rect.width, page.rect.height, spans, None, self.name,
                           {"deskew_deg": angle, "ocr_words": len(spans)})


class VLMBackend:
    """STUB. Contract for a vision-language backend on the visual path:

    read(page) -> PageContent whose spans are cell-level strings with page-coordinate boxes
    (label cells and value cells as printed, △ and dashes preserved, no normalization).
    The model is prompted with the page or a crop plus a strict JSON schema
    ({cells: [{text, bbox:[x0,y0,x1,y1], kind: label|value|header}]}); responses failing the
    schema are rejected, never repaired. Everything downstream (merge, layout, hierarchy,
    normalize, validate) is shared with the other backends.
    """
    name = "vlm"

    def read(self, page: pymupdf.Page) -> PageContent:
        raise NotImplementedError("VLM backend is a stub; see class docstring for the contract")
