"""Skew estimation and correction for rendered page images (visual path, before OCR).

Method: projection profile. Text lines and table rules are horizontal; when the image is level,
the row-wise ink profile has the sharpest peaks and valleys. The estimate is the rotation that
maximises sum((profile[i+1] - profile[i])^2), found by a coarse search over +-max_angle and a fine
search around the best coarse angle, on a downscaled binary image.

Small angles (< min_angle) are left alone so clean scans are not resampled for nothing.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

WORK_WIDTH = 1200          # px; estimation runs on a downscaled copy
INK_THRESHOLD = 160        # grey level below which a pixel counts as ink


def _binary(img: Image.Image) -> Image.Image:
    g = img.convert("L")
    if g.width > WORK_WIDTH:
        g = g.resize((WORK_WIDTH, round(g.height * WORK_WIDTH / g.width)), Image.BILINEAR)
    ink = (np.asarray(g) < INK_THRESHOLD).astype(np.uint8) * 255
    return Image.fromarray(ink)


def _score(binary: Image.Image, angle: float) -> float:
    r = binary.rotate(angle, resample=Image.NEAREST, expand=False, fillcolor=0)
    prof = np.asarray(r, dtype=np.float64).sum(axis=1)
    return float(np.sum(np.diff(prof) ** 2))


def estimate_skew(img: Image.Image, max_angle: float = 5.0, coarse: float = 0.25, fine: float = 0.02) -> float:
    """Angle (degrees, PIL convention: counter-clockwise) to rotate `img` by to level it."""
    b = _binary(img)
    cands = np.arange(-max_angle, max_angle + 1e-9, coarse)
    best = max(cands, key=lambda a: _score(b, a))
    fines = np.arange(best - coarse, best + coarse + 1e-9, fine)
    return round(float(max(fines, key=lambda a: _score(b, a))), 2)


def deskew(img: Image.Image, min_angle: float = 0.1, **kw) -> tuple[Image.Image, float]:
    """Returns (levelled image, applied rotation in degrees). Rotation 0.0 = left untouched."""
    a = estimate_skew(img, **kw)
    if abs(a) < min_angle:
        return img, 0.0
    g = img.convert("L")
    return g.rotate(a, resample=Image.BICUBIC, expand=False, fillcolor=255), a
