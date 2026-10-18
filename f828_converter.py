#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Required Notice: Copyright (c) 2026 wuvshroom
# Required Notice: Official repository: https://github.com/wuvshroom/Sony-F-828-RGBE-RAW-Color-Converter
# Free for noncommercial use. See LICENSE. Selling this software, a modified version of it, or paid
# support for it is not permitted. Modified versions must link to the official repository above in
# their documentation.
"""
Sony DSC-F828 RGBE RAW converter  -  v20

  pip install -r requirements.txt     (PySide6 numpy scipy rawpy tifffile Pillow)
  (optional)  pip install imagecodecs      -> JPEG XL, LZW / Zstandard TIFF, more JPEG 2000 / AVIF
              pip install pillow-heif      -> HEIF / HEIC (source only: GPL, so not in the .exe)
              pip install OpenEXR          -> OpenEXR
  The two Lightroom camera profiles (Sony DSC-F828 IDC Real / Standard.dcp) go
  next to this file or in a "profiles" folder beside it, for the DNG "Sony look".

The sensor maths (black levels, RGBE white balance, ForwardMatrix, bilinear
RGBE demosaic, highlight roll-off, IR false colour) is unchanged from v15.
Everything new is layered around it:

  * white balance expressed as Kelvin + tint (as-shot / auto / presets / picker)
  * crop, straighten, 90-degree rotation, flip, zoom + drag-to-pan viewer
  * live RGB histogram and clipping overlay
  * file library with thumbnails, drag & drop, check-to-batch
  * per-image edits, "sync edits to checked images"
  * TIFF / JPEG / PNG / WebP / AVIF export with per-format quality controls
  * selectable output colour space with a matching embedded ICC profile
  * EXIF + GPS copied from the .SRF into every exported file
  * preview resolution / compression-simulation panel

New in v17:

  * DNG export (linear or mosaic) carrying your colour matrices, so Lightroom
    treats the file as a raw
  * optional edge-aware RGBE demosaic (per image)
  * optional highlight reconstruction from the channels that did not clip
  * thumbnails, the sharpened preview and exports run on background threads

New in v18:

  * "Sony colour (Image Data Converter)" rendering, switchable per image next
    to the default rendering. Reproduces Image Data Converter SR's 'Real'
    (or 'Standard') output: Sony's own 4x3 RGBE matrix followed by the tone
    curve the camera writes into the .SRF. Measured against IDC SR exports:
    about 1-4 / 255 mean difference in sRGB.
  * "As shot" white balance now comes from the camera's embedded preview.
    The WB values stored in the .SRF (which LibRaw reports) are the same in
    every file, so they were not the real as-shot setting.
  * Fixed black level is 492. The masked-border measurement ignores the junk
    values some files carry at the far end of the masked strip.

New in v19:

  * "Sony colour" is now exact. Image Data Converter SR turned out to run a
    software copy of the camera's own colour processor, set up from a block of
    camera registers stored (encrypted) in every .SRF. v19 reads those registers
    and runs the same steps: Sony's 4x3 RGBE matrix, white balance applied after
    the matrix, the camera tone curve, then the camera's luma / colour-difference
    stage with its per-shot 'Real' hue and saturation coefficients. Against IDC
    SR's own 16-bit exports of 11 test shots (natural scenes and a colour chart
    under five WB settings) it is within about 0.3 / 255 RMS, 1 / 255 at the
    99th percentile, where v18 was off by up to 30 / 255 on saturated colours.
  * "As shot" white balance is read from the camera registers (exact, instant).
    The preview-based estimate is kept only as a fallback.
  * New black level source "Camera (stored in the file)", now the default: the
    per-file black level the camera recorded, which is what IDC SR uses.

New in v20:

  * Image Data Converter's own demosaic, recreated and now the
    default: smooth colour planes with a sharp brightness from the green and
    emerald photosites, and IDC's colour kill on the hardest edges. In the
    default rendering it gives about 40 % less colour noise in the shadows than
    the edge-aware demosaic and no colour fringes on fine lines.
  * Sony colour in IDC's own arithmetic: full-size exports and their export
    preview run IDC's colour processor bit for bit (its integer steps, 8-bit
    luma and chroma, 4:2:2 chroma, luma noise filter and 16-bit output step).
    With IDC's demosaic they match IDC SR's own TIFFs to 0.64 / 255 RMS over
    every pixel of the 11 test shots and 0.10 / 255 on flat colour (v19: 5.3
    and 0.24, because only smooth areas matched). The 0.9945 output factor v19
    measured is IDC's 255.5 / 257, and IDC moves four of the processor's
    thresholds on every render, which v20 now does too (deeper shadow colour).
    The preview keeps the faster floating-point version (0.2 / 255 on flat
    colour).
  * IDC's own white-balance presets with Sony colour: daylight, cloudy, shade,
    tungsten, fluorescent and flash use the gains IDC applies (the camera
    records the same numbers for its own presets).
  * The edge-aware demosaic works again: v19 fell back to bilinear on every
    F828 file (the frame is 2460 x 3287, an odd width). Now 2.5x faster too.
  * Export preview: the viewer can show the picture exactly as the export file
    will hold it (format, quality or target size, bit depth, colour space,
    resize, output sharpening), with its file size and what the compression
    changed.
  * New export dialog with presets, JPEG XL, HEIF, JPEG 2000, OpenEXR and BMP
    beside TIFF / DNG / PNG / JPEG / WebP / AVIF, resizing (long or short edge,
    width, height, percent), output sharpening for screen / glossy / matte,
    file-size targets and metadata choices.
  * Lightroom / Camera Raw camera profiles fitted to IDC's 'Real' and
    'Standard' (in the "profiles" folder). A DNG can embed one with the shot's
    exposure, so Lightroom renders it as IDC does (within about 0.4 / 255 on
    smooth areas, as the DNG SDK's renderer shows it).
  * Lens corrections: lateral chromatic aberration (automatic or by hand) and
    purple / green fringe removal; colour noise reduction; hot / dead pixel
    removal.
  * The slow tools now run on every core.

Run:   python f828_converter.py            (or pass .SRF files / folders as arguments)
Build: pyinstaller f828_converter.spec      (one .exe, with the profiles and the codecs installed)

The file is split in two halves: PART 1 is pure processing (no Qt), PART 2 is
the interface.
"""

import collections
import concurrent.futures
import datetime
import gc
import io
import json
import math
import os
import struct
import sys
import tempfile
import threading
import traceback
import zlib
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np
import rawpy
import tifffile as tiff
from PIL import Image
from PIL import features as pil_features
from scipy.ndimage import (affine_transform, convolve, convolve1d, correlate1d, gaussian_filter,
                           map_coordinates, maximum_filter, median_filter, minimum_filter,
                           uniform_filter)
from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal, qVersion
from PySide6.QtGui import (QBrush, QColor, QCursor, QDesktopServices, QIcon, QImage, QKeySequence,
                         QLinearGradient, QPainter, QPainterPath, QPalette, QPen, QPixmap,
                         QPolygonF, QRadialGradient, QShortcut, QTextOption)
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication,
                             QButtonGroup, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                             QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QProgressDialog, QPushButton,
                             QScrollArea, QSlider, QSpinBox, QSplitter, QStackedWidget,
                             QToolButton, QVBoxLayout, QWidget)

APP_NAME = "F828 RGBE Converter"
APP_VERSION = "20"
SOFTWARE_TAG = f"{APP_NAME} v{APP_VERSION}"
RAW_EXTENSIONS = (".srf",)

# =====================================================================
# PART 1  -  PROCESSING (no Qt below until PART 2)
# =====================================================================

# ==========================================
# --- BLACK / WHITE LEVEL SETTINGS ---
# ==========================================

# Typical F828 black level (masked border at ISO 64, normal exposure times). Used by
# "Fixed" mode. Long exposures read a little higher (about 496 at 4 s), which the
# per-file "border" mode picks up; Image Data Converter follows the per-file value.
FIXED_BLACK_RGBE = np.array([492.0, 492.0, 492.0, 492.0], dtype=np.float32)
FALLBACK_WHITE = 16368.0     # 0x3FF0, the F828's 14-bit clipping point

# "Masked border" mode: median of the light-shielded strip right of the image,
# skipping the columns nearest the image (they can catch scene light) and the
# last 17 sensor columns (LibRaw skips those for the F828 as well).
OB_SKIP_NEAR = 8
OB_SKIP_FAR = 17

BLACK_MODES = [
    ("Camera (stored in the file, as Image Data Converter)", "camera"),
    ("Masked border, robust (measured per file)", "border"),
    ("Fixed value (492)", "fixed"),
    ("LibRaw per-file value (old behaviour)", "libraw"),
]

# ==========================================
# --- COLOUR CONSTANTS ---
# ==========================================

WB_DAYLIGHT_RGBE = np.array([2.094020, 0.917032, 1.222377, 1.203671], dtype=np.float32)

# ForwardMatrix2: white-balanced camera RGBE -> XYZ D50
RGBE_TO_XYZ_D50 = np.array([
    [ 0.7280,  0.2722,  0.0204, -0.0563],
    [ 0.3960,  0.6731, -0.2624,  0.1933],
    [ 0.0327, -0.2171,  0.9509,  0.0586]
], dtype=np.float32)

# XYZ D50 -> linear sRGB D65, Bradford-adapted
XYZD50_TO_SRGB = np.array([
    [ 3.1338561, -1.6168667, -0.4906146],
    [-0.9787684,  1.9161415,  0.0334540],
    [ 0.0719453, -0.2289914,  1.4052427]
], dtype=np.float32)

RGBE_TO_SRGB = XYZD50_TO_SRGB @ RGBE_TO_XYZ_D50      # 3x4

# ColorMatrix2 (XYZ D65 -> un-balanced camera RGBE), the Adobe / dcraw table
# for the DSC-F828.  WB_DAYLIGHT_RGBE above is exactly 1 / (this @ D65), so the
# Kelvin model below and the v15 daylight multipliers agree.  It is used ONLY
# to translate Kelvin + tint <-> channel multipliers, never for colour rendering.
XYZ_TO_RGBE = np.array([
    [ 0.7924, -0.1910, -0.0777],
    [-0.8226,  1.5459,  0.2998],
    [-0.1517,  0.2199,  0.6818],
    [-0.7242,  1.1401,  0.3481]
], dtype=np.float64)

BILINEAR_KERNEL = np.array([[1, 2, 1],
                            [2, 4, 2],
                            [1, 2, 1]], dtype=np.float32)

XYZ_D50 = np.array([0.96422, 1.0, 0.82521])
XYZ_D65 = np.array([0.95047, 1.0, 1.08883])

# ==========================================
# --- WHITE BALANCE: KELVIN + TINT ---
# ==========================================
# Same convention as Lightroom / the DNG SDK: the pair describes the LIGHT.
# Temperature is the nearest point on the black-body locus; tint is the distance
# from the locus in CIE 1960 uv, x3000, positive = greener light (so the image
# is pushed towards magenta to compensate).

TEMP_MIN, TEMP_MAX = 2000.0, 20000.0
TINT_MIN, TINT_MAX = -150.0, 150.0
TINT_SCALE = 3000.0


def planck_uv(t):
    """CIE 1960 uv of a black body at t Kelvin (Krystek 1985 rational fit)."""
    u = ((0.860117757 + 1.54118254e-4 * t + 1.28641212e-7 * t * t)
         / (1.0 + 8.42420235e-4 * t + 7.08145163e-7 * t * t))
    v = ((0.317398726 + 4.22806245e-5 * t + 4.20481691e-8 * t * t)
         / (1.0 - 2.89741816e-5 * t + 1.61456053e-7 * t * t))
    return u, v


def _locus_normal(t):
    """Unit vector perpendicular to the locus at t, pointing to the green (+v) side."""
    d = t * 1e-3
    u1, v1 = planck_uv(t - d)
    u2, v2 = planck_uv(t + d)
    tu, tv = u2 - u1, v2 - v1
    n = math.hypot(tu, tv)
    nu, nv = tv / n, -tu / n
    if nv < 0:
        nu, nv = -nu, -nv
    return nu, nv


def temp_tint_to_xy(temp, tint):
    u, v = planck_uv(temp)
    nu, nv = _locus_normal(temp)
    u += nu * tint / TINT_SCALE
    v += nv * tint / TINT_SCALE
    d = 2.0 * u - 8.0 * v + 4.0
    return 3.0 * u / d, 2.0 * v / d


def xy_to_temp_tint(x, y):
    d = -2.0 * x + 12.0 * y + 3.0
    u, v = 4.0 * x / d, 6.0 * y / d

    def dist2(mired):
        pu, pv = planck_uv(1e6 / mired)
        return (pu - u) ** 2 + (pv - v) ** 2

    # golden-section search in mired (the locus is close to uniform there)
    lo, hi = 1e6 / 60000.0, 1e6 / 1000.0
    g = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa, fb = dist2(a), dist2(b)
    for _ in range(70):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = dist2(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = dist2(b)
    temp = 1e6 / (0.5 * (lo + hi))
    pu, pv = planck_uv(temp)
    nu, nv = _locus_normal(temp)
    tint = ((u - pu) * nu + (v - pv) * nv) * TINT_SCALE
    return temp, tint


def wb_from_temp_tint(temp, tint):
    """RGBE channel multipliers for a light of the given Kelvin + tint.
    Smallest multiplier is 1.0, so clipped highlights stay neutral (as in v15)."""
    x, y = temp_tint_to_xy(float(temp), float(tint))
    xyz = np.array([x / y, 1.0, (1.0 - x - y) / y])
    neutral = XYZ_TO_RGBE @ xyz
    # Extreme settings (e.g. 2000 K with a strong green tint) describe a light the
    # sensor would see no blue from at all; cap the gain ratio at 50x instead.
    neutral = np.maximum(neutral, 0.02 * neutral.max())
    m = 1.0 / neutral
    return (m / m.min()).astype(np.float32)


def temp_tint_from_wb(mult, rgb_only=False):
    """Inverse of wb_from_temp_tint. `mult` are R,G,B(,E) multipliers. Returns
    (temp, tint) or None if the multipliers do not describe a plausible light."""
    m = np.asarray(mult, dtype=np.float64)
    if m.size < 3 or not np.all(np.isfinite(m)) or np.any(m[:3] <= 0):
        return None
    try:
        if rgb_only or m.size < 4 or m[3] <= 0:
            xyz = np.linalg.solve(XYZ_TO_RGBE[:3], 1.0 / m[:3])
        else:
            xyz = np.linalg.lstsq(XYZ_TO_RGBE, 1.0 / m[:4], rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    s = float(xyz.sum())
    if s <= 0 or xyz[1] <= 0:
        return None
    temp, tint = xy_to_temp_tint(xyz[0] / s, xyz[1] / s)
    if not (math.isfinite(temp) and math.isfinite(tint)):
        return None
    return temp, tint


def clamp_temp_tint(tt):
    return (float(min(max(tt[0], TEMP_MIN), TEMP_MAX)),
            float(min(max(tt[1], TINT_MIN), TINT_MAX)))


# What v15 always used: the light for which the colour matrix is neutral (D65).
D65_TEMP_TINT = clamp_temp_tint(temp_tint_from_wb(WB_DAYLIGHT_RGBE))

# (label, key, (kelvin, tint) or None when it is computed per file)
WB_PRESETS = [
    ("As shot", "asshot", None),
    ("Auto", "auto", None),
    ("Daylight", "daylight", (5500.0, 10.0)),
    ("Cloudy", "cloudy", (6500.0, 10.0)),
    ("Shade", "shade", (7500.0, 10.0)),
    ("Tungsten", "tungsten", (2850.0, 0.0)),
    ("Fluorescent", "fluorescent", (3800.0, 21.0)),
    ("Flash", "flash", (5500.0, 0.0)),
    ("D65 neutral (v15 look)", "d65", D65_TEMP_TINT),
    ("Custom", "custom", None),
]
WB_PRESET_VALUES = {key: tt for _, key, tt in WB_PRESETS}

# Image Data Converter SR's own white-balance presets for the F828, as the gains its
# colour processor applies after the matrix: (R, B) x 256 with G = 256 (the camera's
# daylight, fluorescent and incandescent settings give the same numbers). With Sony
# colour these presets match IDC's exactly.
IDC_WB_PRESETS = {"daylight": (565, 539), "cloudy": (705, 439), "shade": (775, 389),
                  "tungsten": (291, 1100), "fluorescent": (419, 835), "flash": (628, 504)}


def idc_preset_wb(key):
    """IDC's after-matrix gains (R, G, B) for one of its presets."""
    r, b = IDC_WB_PRESETS[key]
    return np.array([r / 256.0, 1.0, b / 256.0], dtype=np.float32)


def uses_idc_preset(edit, sensor):
    """Sony colour with camera registers and one of IDC's presets: the preset's own gains."""
    return (edit.wb_preset in IDC_WB_PRESETS and getattr(edit, "render", "default") == "sony"
            and not edit.ir and sensor.get("camera") is not None)


def auto_white_balance(rgbe):
    """Robust grey-world estimate on black-subtracted, normalised, UN-balanced
    RGBE data. Ignores clipped and near-black pixels, then iterates towards the
    pixels that are closest to neutral. Returns (temp, tint) or None."""
    px = np.asarray(rgbe, dtype=np.float32).reshape(-1, 4)
    if px.shape[0] > 400_000:
        px = px[:: px.shape[0] // 400_000 + 1]
    ok = (px.max(axis=1) < 0.90) & (px.min(axis=1) > 0.004)
    px = px[ok]
    if px.shape[0] < 64:
        return None
    mean = px.mean(axis=0).astype(np.float64)
    for _ in range(4):
        bal = px / mean
        lum = bal.mean(axis=1, keepdims=True)
        dev = np.abs(bal - lum).max(axis=1) / np.maximum(lum[:, 0], 1e-6)
        near = dev < 0.20
        if near.sum() < max(64, px.shape[0] // 50):
            break
        mean = px[near].mean(axis=0).astype(np.float64)
    tt = temp_tint_from_wb(1.0 / np.maximum(mean, 1e-6))
    return None if tt is None else clamp_temp_tint(tt)


def picked_white_balance(rgbe_patch):
    """Kelvin + tint that makes the given patch of un-balanced RGBE neutral."""
    px = np.asarray(rgbe_patch, dtype=np.float64).reshape(-1, 4)
    mean = px.mean(axis=0)
    if px.size == 0 or np.any(mean <= 1e-5) or mean.max() >= 0.98:
        return None                      # too dark or clipped to be trusted
    tt = temp_tint_from_wb(1.0 / mean)
    return None if tt is None else clamp_temp_tint(tt)


# ==========================================
# --- OUTPUT COLOUR SPACES + ICC PROFILES ---
# ==========================================

BRADFORD = np.array([[ 0.8951,  0.2664, -0.1614],
                     [-0.7502,  1.7135,  0.0367],
                     [ 0.0389, -0.0685,  1.0296]])


def bradford_adapt(src_white, dst_white):
    gain = (BRADFORD @ dst_white) / (BRADFORD @ src_white)
    return np.linalg.inv(BRADFORD) @ np.diag(gain) @ BRADFORD


def rgb_to_xyz_matrix(primaries, white_xyz):
    p = np.array([[x / y for x, y in primaries],
                  [1.0, 1.0, 1.0],
                  [(1.0 - x - y) / y for x, y in primaries]])
    return p * (np.linalg.inv(p) @ white_xyz)


_PRIM_SRGB = ((0.640, 0.330), (0.300, 0.600), (0.150, 0.060))
_PRIM_P3 = ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060))
_PRIM_ADOBE = ((0.640, 0.330), (0.210, 0.710), (0.150, 0.060))
_PRIM_PROPHOTO = ((0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001))
_PRIM_REC2020 = ((0.708, 0.292), (0.170, 0.797), (0.131, 0.046))

# key -> definition.  trc: ("srgb",) | ("rec709",) | ("gamma", g) | ("linear",)
COLOR_SPACES = {
    "srgb": dict(label="sRGB", icc_name="sRGB IEC61966-2.1 (F828 Converter)",
                 primaries=_PRIM_SRGB, white=XYZ_D65, trc=("srgb",),
                 note="Safest choice. Web, email, most printers and screens."),
    "p3": dict(label="Display P3", icc_name="Display P3 (F828 Converter)",
               primaries=_PRIM_P3, white=XYZ_D65, trc=("srgb",),
               note="Wider reds and greens. Recent Apple / wide-gamut screens."),
    "adobergb": dict(label="Adobe RGB (1998)", icc_name="Adobe RGB (1998) compatible (F828 Converter)",
                     primaries=_PRIM_ADOBE, white=XYZ_D65, trc=("gamma", 563.0 / 256.0),
                     note="Wider greens and cyans. Common for print workflows."),
    "prophoto": dict(label="ProPhoto RGB", icc_name="ProPhoto RGB compatible (F828 Converter)",
                     primaries=_PRIM_PROPHOTO, white=XYZ_D50, trc=("gamma", 461.0 / 256.0),
                     note="Largest gamut; what Lightroom works in internally. Use 16-bit."),
    "rec2020": dict(label="Rec. 2020", icc_name="Rec. 2020 (F828 Converter)",
                    primaries=_PRIM_REC2020, white=XYZ_D65, trc=("rec709",),
                    note="Very wide gamut, video / HDR pipelines. Use 16-bit."),
    "linsrgb": dict(label="Linear sRGB (gamma 1.0)", icc_name="Linear sRGB (F828 Converter)",
                    primaries=_PRIM_SRGB, white=XYZ_D65, trc=("linear",),
                    note="Scene-linear, sRGB primaries. For compositing / measurement. Use 16-bit."),
    "linprophoto": dict(label="Linear ProPhoto (gamma 1.0)", icc_name="Linear ProPhoto (F828 Converter)",
                        primaries=_PRIM_PROPHOTO, white=XYZ_D50, trc=("linear",),
                        note="Scene-linear, widest gamut. Closest to an untouched raw. Use 16-bit."),
}
COLOR_SPACE_ORDER = ["srgb", "p3", "adobergb", "prophoto", "rec2020", "linsrgb", "linprophoto"]


def xyz_d50_to_rgb(space):
    """3x3 matrix: XYZ (D50) -> linear RGB of the given output space."""
    if space == "srgb":
        return XYZD50_TO_SRGB.astype(np.float64)       # v15's exact constants
    cs = COLOR_SPACES[space]
    native = rgb_to_xyz_matrix(cs["primaries"], cs["white"])
    return np.linalg.inv(native) @ bradford_adapt(XYZ_D50, cs["white"])


def camera_to_rgb(space):
    """3x4 matrix: white-balanced camera RGBE -> linear RGB of the output space."""
    return (xyz_d50_to_rgb(space) @ RGBE_TO_XYZ_D50.astype(np.float64)).astype(np.float32)


def encode_trc(lin, space):
    """Linear [0,1] -> encoded [0,1] with the output space's transfer curve."""
    trc = COLOR_SPACES[space]["trc"]
    if trc[0] == "linear":
        return lin
    if trc[0] == "srgb":
        return np.where(lin <= 0.0031308, 12.92 * lin,
                        1.055 * np.power(lin, 1.0 / 2.4) - 0.055).astype(np.float32)
    if trc[0] == "rec709":
        return np.where(lin < 0.018, 4.5 * lin,
                        1.099 * np.power(lin, 0.45) - 0.099).astype(np.float32)
    return np.power(lin, np.float32(1.0 / trc[1])).astype(np.float32)


def _decode_trc_samples(space, n):
    """Encoded -> linear, sampled on n points (for the ICC tone curve)."""
    v = np.linspace(0.0, 1.0, n)
    kind = COLOR_SPACES[space]["trc"][0]
    if kind == "srgb":
        return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    return np.where(v < 0.081, v / 4.5, ((v + 0.099) / 1.099) ** (1.0 / 0.45))


def _s15f16(x):
    return struct.pack(">i", int(round(float(x) * 65536.0)))


def _icc_xyz(xyz):
    return b"XYZ \0\0\0\0" + b"".join(_s15f16(c) for c in xyz)


def _icc_text_desc(text):
    a = text.encode("ascii", "replace") + b"\0"
    return (b"desc\0\0\0\0" + struct.pack(">I", len(a)) + a
            + b"\0" * 4 + b"\0" * 4          # unicode language + count
            + b"\0" * 2 + b"\0" + b"\0" * 67)  # scriptcode block


_ICC_CACHE = {}


def build_icc_profile(space):
    """A small ICC v2 matrix/TRC display profile describing the output space, so
    Lightroom / Photoshop / browsers interpret the exported numbers correctly."""
    if space in _ICC_CACHE:
        return _ICC_CACHE[space]
    cs = COLOR_SPACES[space]
    to_d50 = bradford_adapt(cs["white"], XYZ_D50)
    colorants = to_d50 @ rgb_to_xyz_matrix(cs["primaries"], cs["white"])   # columns = R,G,B in PCS

    trc = cs["trc"]
    if trc[0] == "linear":
        curve = b"curv\0\0\0\0" + struct.pack(">I", 0)
    elif trc[0] == "gamma":
        curve = b"curv\0\0\0\0" + struct.pack(">IH", 1, int(round(trc[1] * 256.0)))
    else:
        samples = np.round(_decode_trc_samples(space, 4096) * 65535.0).astype(">u2")
        curve = b"curv\0\0\0\0" + struct.pack(">I", samples.size) + samples.tobytes()

    chad = b"sf32\0\0\0\0" + b"".join(_s15f16(c) for c in to_d50.ravel())
    blocks = [                                   # (signature, data); identical data is shared
        (b"desc", _icc_text_desc(cs["icc_name"])),
        (b"cprt", b"text\0\0\0\0" + b"Public domain. Generated by " + SOFTWARE_TAG.encode() + b"\0"),
        (b"wtpt", _icc_xyz(cs["white"])),
        (b"chad", chad),
        (b"rXYZ", _icc_xyz(colorants[:, 0])),
        (b"gXYZ", _icc_xyz(colorants[:, 1])),
        (b"bXYZ", _icc_xyz(colorants[:, 2])),
        (b"rTRC", curve), (b"gTRC", curve), (b"bTRC", curve),
    ]
    table, body, placed = [], b"", {}
    offset = 128 + 4 + 12 * len(blocks)
    for sig, data in blocks:
        if data not in placed:
            pad = (-len(body)) % 4
            body += b"\0" * pad
            placed[data] = (offset + len(body), len(data))
            body += data
        table.append(sig + struct.pack(">II", *placed[data]))
    body += b"\0" * ((-len(body)) % 4)

    size = offset + len(body)
    now = datetime.datetime(2025, 1, 1)          # fixed date: identical bytes every run
    header = (struct.pack(">I", size) + b"\0\0\0\0" + struct.pack(">I", 0x02400000)
              + b"mntrRGB XYZ "
              + struct.pack(">6H", now.year, now.month, now.day, 0, 0, 0)
              + b"acsp" + b"MSFT" + struct.pack(">I", 0) + b"\0" * 4 + b"\0" * 4
              + b"\0" * 8 + struct.pack(">I", 0)
              + _s15f16(0.9642) + _s15f16(1.0) + _s15f16(0.8249)
              + b"\0" * 4)
    header += b"\0" * (128 - len(header))
    profile = header + struct.pack(">I", len(blocks)) + b"".join(table) + body
    _ICC_CACHE[space] = profile
    return profile


# ==========================================
# --- SONY "REAL" / "STANDARD" RENDERING (Image Data Converter SR) ---
# ==========================================
# Measured against Image Data Converter SR 16-bit TIFF exports. IDC does:
#   balanced RGBE (same white balance + normalisation as this converter)
#     -> SONY_RGBE_TO_CURVE (one matrix for both colour modes; rows sum to 1, so
#        balanced sensor white lands exactly on the top of the curve)
#     -> the tone curve the camera writes into the .SRF (SRF2 tag 0x0022 against
#        0x0020). 'Real' and 'Standard' differ ONLY in this curve.
#     -> the result is treated as sRGB-encoded. Other output spaces are made from
#        linear sRGB, which is also how IDC makes its Adobe RGB export.

RENDER_MODES = [("Default (v17 rendering)", "default"),
                ("Sony colour (Image Data Converter)", "sony")]
SONY_CURVE_MODES = [("Real", "real"), ("Standard", "standard")]

SONY_RGBE_TO_CURVE = np.array([
    [ 1.542599,  0.061028,  0.205498, -0.809125],
    [-0.152736,  0.944303, -0.273623,  0.482057],
    [-0.042805, -0.543895,  1.507271,  0.079429],
], dtype=np.float32)

_SONY_CURVE_X = [
    0, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120, 128, 136, 144, 152, 160, 168, 176, 184, 192,
    200, 208, 216, 224, 232, 240, 248, 256, 272, 288, 304, 320, 336, 352, 368, 384, 400, 416, 432, 448, 464, 480,
    496, 512, 544, 576, 608, 640, 672, 704, 736, 768, 800, 832, 864, 896, 928, 960, 992, 1024, 1088, 1152, 1216,
    1280, 1344, 1408, 1472, 1536, 1600, 1664, 1728, 1792, 1856, 1920, 1984, 2048, 2176, 2304, 2432, 2560, 2688,
    2816, 2944, 3072, 3200, 3328, 3456, 3584, 3712, 3840, 3968, 4095]
_SONY_CURVE_Y = {
    "real": [
        0, 13, 26, 39, 53, 66, 79, 92, 108, 122, 136, 149, 161, 172, 183, 193, 203, 212, 222, 230, 239, 247, 255,
        263, 271, 278, 285, 292, 299, 306, 312, 319, 325, 337, 349, 360, 371, 382, 392, 402, 412, 421, 430, 439,
        448, 456, 465, 473, 481, 497, 512, 526, 540, 554, 567, 580, 592, 604, 616, 627, 639, 650, 660, 671, 681,
        701, 720, 739, 757, 774, 791, 807, 823, 838, 850, 861, 871, 880, 888, 896, 903, 916, 927, 937, 946, 955,
        963, 970, 977, 983, 990, 995, 1001, 1006, 1011, 1016, 1020],
    "standard": [
        0, 9, 19, 28, 37, 46, 56, 65, 74, 84, 93, 102, 112, 121, 130, 139, 149, 158, 167, 177, 186, 195, 205, 215,
        225, 235, 245, 254, 263, 272, 280, 289, 297, 312, 327, 340, 354, 366, 378, 390, 401, 412, 423, 433, 442,
        452, 461, 470, 479, 495, 511, 526, 540, 554, 567, 580, 592, 604, 615, 626, 636, 647, 656, 666, 675, 693,
        710, 727, 742, 757, 771, 785, 798, 810, 823, 834, 846, 857, 867, 878, 888, 906, 921, 933, 944, 954, 962,
        970, 977, 984, 990, 996, 1002, 1007, 1012, 1016, 1021],
}
SONY_CURVE_X = np.array(_SONY_CURVE_X, dtype=np.float32) / 4095.0
SONY_CURVE = {k: np.array(v, dtype=np.float32) / 1023.0 for k, v in _SONY_CURVE_Y.items()}

# linear sRGB (D65) -> XYZ D50, Bradford-adapted
SRGB_LINEAR_TO_XYZ_D50 = np.array([
    [0.4360747, 0.3850649, 0.1430804],
    [0.2225045, 0.7168786, 0.0606169],
    [0.0139322, 0.0971045, 0.7141733],
], dtype=np.float64)

# R, G, B, E multipliers (G = 1) of the light SONY_RGBE_TO_CURVE was calibrated
# under: about 4400 K, tint +19 in this converter's Kelvin model.
_SONY_REF_MULT = np.array([1.824277, 1.0, 1.817585, 1.415528])


def sony_curve(x, mode="real"):
    """Sony tone curve: curve input (0..1, 1 = balanced sensor white) -> sRGB-encoded 0..1."""
    return np.interp(x, SONY_CURVE_X, SONY_CURVE[mode]).astype(np.float32)


def srgb_decode(v):
    v = np.asarray(v, dtype=np.float32)
    return np.where(v <= 0.04045, v / np.float32(12.92),
                    ((np.maximum(v, 0.0) + np.float32(0.055)) / np.float32(1.055)) ** np.float32(2.4)
                    ).astype(np.float32)


def render_sony(balanced, mode="real", gain=1.0):
    """(..., 4) balanced, normalised RGBE -> LINEAR sRGB (D65), as IDC renders it.
    `gain` is exposure as a linear factor, applied before the curve."""
    shp = balanced.shape[:-1]
    x = balanced.reshape(-1, 4) @ SONY_RGBE_TO_CURVE.T
    if gain != 1.0:
        x *= np.float32(gain)
    return srgb_decode(sony_curve(x, mode)).reshape(*shp, 3)


# ==========================================
# --- EXACT SONY COLOUR: THE CAMERA'S OWN COLOUR PROCESSOR (v19) ---
# ==========================================
# Image Data Converter SR has no colour science of its own for the F828. It runs a
# bit-exact software copy of the camera's colour processor, set up from a block of
# camera registers that every .SRF carries (encrypted, SRF2 tag 0x0003). Those hold
# the per-file black level, a digital gain, Sony's 4x3 RGBE matrix, the real as-shot
# white balance (applied AFTER the matrix) and the per-shot coefficients of the
# 'Real' colour-difference stage, which is not a matrix but a different linear map
# in each quadrant of the colour plane. IDC keeps those coefficients as shot when the
# white balance is changed; only the curve changes between Real and Standard.
# Everything below recreates that processor's behaviour, in floating point.

SRF_MASTER_KEY_PTR = 200896          # the master key sits at this offset + 4 * byte[this offset]
SRF_SRF1 = 164600                    # SRF1 directory: tag 0 = key for SRF2
SRF_REGISTER_BYTES = 7458
DSP_INPUT_SCALE = 0.25               # 14-bit sensor numbers -> the processor's 12-bit range
# IDC's export turns 8-bit luma into 255.5 / 65535 per step and 8-bit chroma into 256 C8 - C8 / 2,
# so its output is 255.5 / 257 = 0.99416 of the plain conversion (v19 measured 0.9945).
# The C8 / 2 also lowers chroma by 64 / 65535, but the stage rounds
# its chroma bytes half up, which raises them by as much on average, so the floating-point
# version takes the scale alone (on IDC's TIFFs the 64 moves its flat colour further from IDC).
DSP_OUTPUT_SCALE = 255.5 / 65535.0     # per 8-bit step
# Input positions of the processor's 97-point segmented tone-curve table (12-bit in).
DSP_CURVE_X = np.array([0] + [8 * i for i in range(1, 33)] + [256 + 16 * i for i in range(1, 17)] +
                       [512 + 32 * i for i in range(1, 17)] + [1024 + 64 * i for i in range(1, 17)] +
                       [2048 + 128 * i for i in range(1, 17)], dtype=np.float32)


def _srf_keystream(key, n):
    pad = [0] * 128
    for p in range(4):
        key = (key * 48828125 + 1) & 0xffffffff
        pad[p] = key
    pad[3] = ((pad[3] << 1) | ((pad[0] ^ pad[2]) >> 31)) & 0xffffffff
    for p in range(4, 127):
        pad[p] = (((pad[p - 4] ^ pad[p - 2]) << 1) | ((pad[p - 3] ^ pad[p - 1]) >> 31)) & 0xffffffff
    out, p = [], 127
    for _ in range(n):
        p += 1
        pad[(p - 1) & 127] = pad[p & 127] ^ pad[(p + 64) & 127]
        out.append(pad[(p - 1) & 127])
    return out


def _srf_decrypt(buf, off, nwords, key):
    words = struct.unpack_from(">%dI" % nwords, buf, off)
    struct.pack_into(">%dI" % nwords, buf, off, *[w ^ k for w, k in zip(words, _srf_keystream(key, nwords))])


def _srf_ifd(buf, off):
    n = struct.unpack_from("<H", buf, off)[0]
    ents = {}
    for i in range(n):
        tag, typ, cnt, val = struct.unpack_from("<HHI4s", buf, off + 2 + 12 * i)
        ents[tag] = (typ, cnt, val)
    return ents


def read_srf_camera(path):
    """The camera registers IDC SR develops from, decoded. Raises if the file does
    not carry them (not an F828 .SRF, or damaged)."""
    with open(path, "rb") as fh:
        buf = bytearray(fh.read(SRF_MASTER_KEY_PTR + 1024 + 4))
    if len(buf) < SRF_MASTER_KEY_PTR + 4:
        raise ValueError("file too short for an F828 .SRF")
    master = struct.unpack_from(">I", buf, SRF_MASTER_KEY_PTR + buf[SRF_MASTER_KEY_PTR] * 4)[0]
    _srf_decrypt(buf, SRF_SRF1, (SRF_MASTER_KEY_PTR - SRF_SRF1) // 4, master)
    e1 = _srf_ifd(buf, SRF_SRF1)
    srf2 = struct.unpack_from("<I", buf, SRF_SRF1 + 2 + 12 * len(e1))[0]
    _srf_decrypt(buf, srf2, (SRF_MASTER_KEY_PTR - srf2) // 4, struct.unpack("<I", e1[0][2])[0])
    e2 = _srf_ifd(buf, srf2)
    base = struct.unpack("<I", e2[3][2])[0]
    d = bytes(buf[base:base + SRF_REGISTER_BYTES])
    if len(d) < SRF_REGISTER_BYTES:
        raise ValueError("camera registers missing")

    def r16(a):
        return (d[a] << 8) | d[a + 1]

    def s15(v):
        v &= 0x7fff
        return v - 0x8000 if v > 0x3fff else v

    def s8(v):
        return v - 0x100 if v > 0x7f else v

    def tag_words(t):
        typ, cnt, v = e2[t]
        off = struct.unpack("<I", v)[0]
        return [(buf[off + 2 * i] << 8) | buf[off + 2 * i + 1] for i in range(cnt)]

    cam = {
        "black": np.array([r16(a) for a in (0x424, 0x426, 0x428, 0x42a)], dtype=np.float32),     # R G B E
        "gain": (r16(0x42c) & 0x1ff) / 128.0,                                                  # digital gain
        "pregain": np.array([r16(a) & 0xfff for a in (0x434, 0x436, 0x438, 0x43a)], dtype=np.float32) / 256.0,
        "matrix": np.array([s15(r16(0x634 + 2 * i)) for i in range(12)], dtype=np.float32).reshape(3, 4) / 1024.0,
        "wb": np.array([r16(0x64c) & 0xfff, 256, r16(0x650) & 0xfff], dtype=np.float32) / 256.0,  # after the matrix
        "luma": np.array([r16(a) for a in (0x664, 0x666, 0x668)], dtype=np.float32) / 64.0,
        # colour-difference stage: cross terms by sign of the other difference, gains by sign of the result
        "cr_cross": (s8(d[0x688]), s8(d[0x68a])), "cb_cross": (s8(d[0x689]), s8(d[0x68b])),
        "cb_gain": (d[0x68c], d[0x68e]), "cr_gain": (d[0x68d], d[0x68f]),
        "cb_offset": s8(d[0x690]), "cr_offset": s8(d[0x691]),
        "contrast": d[0x679] / 128.0,
        "curve": np.array([0] + [w & 0xfff for w in tag_words(0x22)], dtype=np.float32),
        "mode": "real" if struct.unpack("<H", e2[0xfe][2][:2])[0] else "standard",
        "regs": d,                                         # the whole dump: IDC's demosaic reads it too
    }
    # IDC's luminance detail goes through its own curve (tag 0x24); in every file seen it equals the tone curve
    detail = np.array([0] + [w & 0xfff for w in tag_words(0x24)], dtype=np.float32) if 0x24 in e2 else None
    cam["detail_curve"] = detail if detail is not None and detail.size == DSP_CURVE_X.size else cam["curve"]
    if cam["curve"].size != DSP_CURVE_X.size or not 0.5 < cam["gain"] < 2.0 or cam["wb"].min() <= 0:
        raise ValueError("unexpected camera register values")
    try:                                                   # what the bit-exact processor needs
        cam["stage"] = idc_stage_registers(d, {t: tag_words(t) for t in (0x22, 0x24, 0x26) if t in e2})
        cam["post"] = idc_output_registers(d)
    except Exception:
        cam["stage"] = cam["post"] = None
    return cam


def dsp_white_balance(mult, cam):
    """White balance after the matrix (R, G, B gains, G = 1) that makes the light
    described by RGBE multipliers `mult` neutral, as the camera / IDC apply it."""
    neutral = cam["matrix"].astype(np.float64) @ (cam["pregain"] / np.asarray(mult, dtype=np.float64))
    neutral = np.maximum(neutral, 1e-6 * neutral.max())
    return (neutral[1] / neutral).astype(np.float32)


def dsp_temp_tint(cam):
    """The camera's as-shot white balance (registers) as Kelvin + tint in this
    converter's model, so the sliders show it. Exact round trip with
    dsp_white_balance(wb_from_temp_tint(...))."""
    target = np.log(cam["wb"][[0, 2]].astype(np.float64))

    def f(p):
        t = 1e6 / p[0]
        return np.log(dsp_white_balance(wb_from_temp_tint(t, p[1]), cam)[[0, 2]].astype(np.float64)) - target

    p = np.array([1e6 / 5000.0, 0.0])
    for _ in range(40):
        r = f(p)
        if np.abs(r).max() < 1e-6:
            break
        J = np.stack([(f(p + h) - r) / h[i] for i, h in enumerate((np.array([0.5, 0.0]), np.array([0.0, 0.5])))], 1)
        try:
            step = np.linalg.solve(J, -r)
        except np.linalg.LinAlgError:
            return None
        p = p + np.clip(step, -40.0, 40.0)
    if np.abs(f(p)).max() > 1e-3:
        return None
    return clamp_temp_tint((1e6 / p[0], p[1]))


def _dsp_curve(v, table):
    a = np.minimum(np.abs(v), np.float32(4095.0))
    return np.copysign(np.interp(a, DSP_CURVE_X, table).astype(np.float32), v)


def idc_thresholds(cam, mode=None, exposure=0.0):
    """The four colour-stage thresholds IDC renders with, in the stage's units: the high
    and low chroma-suppression knees, the detail-kill level and the raw-clip kill level.
    On every render (not only after an exposure change) IDC takes each register value
    through the inverse tone curve, times the exposure gain, truncates it and takes it
    through the curve again. So the values move a little
    even at 0 EV: the low knee 16 -> 14 with the Real curve and 15 with Standard, the kill
    level 160 -> 159. (11 shots: flat colour 0.115 -> 0.095 / 255 from IDC, DSC00673's dark
    areas 0.40 -> 0.23.)"""
    st = cam.get("stage")
    if st is None:
        return 3840, 256, 160, 1023
    table = cam["curve"] if mode in (None, cam["mode"]) or mode not in _SONY_CURVE_Y else _SONY_CURVE_Y[mode]
    vals = [float(v) / 4.0 for v in list(table)[:97]]               # the curve, 8-bit
    knots = [float(v) / 4.0 for v in DSP_CURVE_X]                    # its inputs, 10-bit
    r = 2.0 ** float(exposure)                                       # gain now / gain as shot

    def inverse(a):
        if a <= 0.0:
            return 0.0
        i = 0
        while i < 97 and not a <= vals[i]:
            i += 1
        i = min(max(i, 1), 96)
        lo, hi = vals[i - 1], vals[i]
        return (knots[i] - knots[i - 1]) * ((a - lo) / (hi - lo)) + knots[i - 1]

    def forward(n):
        x = float(n)
        j = 1
        while j < 96 and x > knots[j]:
            j += 1
        return int(((vals[j] - vals[j - 1]) / (knots[j] - knots[j - 1])) * (x - knots[j - 1]) + vals[j - 1])

    out = []
    for n, t in enumerate((min(st["sup_hi"] >> 4, 0xff), st["sup_lo"] >> 4, st["det_thr"], st["clip_thr"] >> 2)):
        v = inverse(float(t)) * r
        if n == 2 and r < 1.0:                   # the kill level falls 5/6 as fast as the gain
            v *= 1.0 - (1.0 - r) * 5.0 / 6.0
        out.append(forward(int(min(v, 1024.0))))
    return (out[0] & 0x1ff) << 4, out[1] << 4, out[2], (out[3] << 2) | 3


def dsp_suppression(cam, mode=None, exposure=0.0):
    """The colour stage's chroma suppression in deep shadows and highlights: (high knee, low
    knee, high slope, low slope), the knees in units of 4 x the 10-bit luma."""
    st = cam.get("stage")
    if st is None:
        return 3840.0, 256.0, 0.5, 0.5
    if not st["sup_en"]:
        return 1e9, -1e9, 0.0, 0.0
    hi, lo = idc_thresholds(cam, mode, exposure)[:2]
    sh = st["sup_hi_sh"]
    return float(hi), float(lo), (2.0 ** (sh - 7) if 1 <= sh <= 7 else 0.0), st["sup_lo_g"] / 8.0


def render_sony_dsp(raw12, cam, wb_post, mode="real", detail=None, kill=None, exposure=0.0):
    """(..., 4) un-balanced RGBE in the processor's 12-bit units -> LINEAR sRGB.
    The camera / IDC chain: matrix, white balance after the matrix (both clipped
    at the 12-bit top), tone curve, luma (19 R + 38 G + 7 B) / 64 with the luma
    contrast register, colour differences with the camera's chroma suppression
    in deep shadows and highlights, then the quadrant 'Real' chroma coefficients.
    `detail` and `kill` come from IDC's demosaic (demosaic_idc_frame): its
    luminance detail Yhigh - (G + E) / 2 in 12-bit units (scaled like raw12)
    and its chroma-kill factor, applied where IDC's colour stage applies them.
    `exposure` (EV, already in raw12) moves the suppression knees as IDC does.
    IDC's integer rounding, 4:2:2 chroma and output filters are in
    render_sony_exact(), which full-size exports use."""
    shp = raw12.shape[:-1]
    x = raw12.reshape(-1, 4) @ cam["matrix"].T
    np.clip(x, -2048.0, 4092.0, out=x)
    x *= np.asarray(wb_post, dtype=np.float32)
    np.clip(x, -2048.0, 4092.0, out=x)
    table = cam["curve"] if mode == cam["mode"] else np.asarray(_SONY_CURVE_Y[mode], dtype=np.float32)
    c = _dsp_curve(x, table)                      # R' G' B' on the curve's 10-bit scale
    del x
    r, g, b = c[:, 0], c[:, 1], c[:, 2]
    lw = cam["luma"]
    y = np.clip(lw[0] * r + lw[1] * g + lw[2] * b, -2048.0, 2047.0)
    if detail is not None:                        # IDC: + LUT(Yhigh) - LUT((G + E) / 2), after the luma clip
        dtable = cam.get("detail_curve", table) if mode == cam["mode"] else table
        flat = raw12.reshape(-1, 4)
        ylow = np.float32(0.5) * (flat[:, 1] + flat[:, 3])
        y += _dsp_curve(ylow + detail.reshape(-1), dtable) - _dsp_curve(ylow, dtable)
        np.clip(y, -2048.0, 2047.0, out=y)
        del flat, ylow
    y10 = np.clip(y * np.float32(cam["contrast"]), 0.0, 1020.0)
    hi_k, lo_k, hi_s, lo_s = dsp_suppression(cam, mode, exposure)
    y4 = 4.0 * y                                  # chroma suppression in deep shadows and highlights
    sup = np.minimum(np.clip(255.0 - np.maximum(y4 - hi_k, 0.0) * hi_s, 0.0, 255.0),
                     np.clip(255.0 - np.maximum(lo_k - y4, 0.0) * lo_s, 0.0, 255.0)) / np.float32(256.0)
    if kill is not None:                          # IDC: colour differences x 1, 1/2, 1/4 or 0 at hard edges
        sup *= kill.reshape(-1)
    dr = (r - g) * sup
    db = (b - g) * sup
    t = dr + np.where(db >= 0, cam["cr_cross"][0], cam["cr_cross"][1]).astype(np.float32) / 128.0 * db
    cr = np.clip(np.where(t >= 0, cam["cr_gain"][0], cam["cr_gain"][1]).astype(np.float32) / 128.0 * t
                 + 4 * cam["cr_offset"], -508.0, 508.0)
    t = db + np.where(dr >= 0, cam["cb_cross"][0], cam["cb_cross"][1]).astype(np.float32) / 128.0 * dr
    cb = np.clip(np.where(t >= 0, cam["cb_gain"][0], cam["cb_gain"][1]).astype(np.float32) / 128.0 * t
                 + 4 * cam["cb_offset"], -508.0, 508.0)
    del c, r, g, b, dr, db, t, sup
    # Y / Cb / Cr in 8-bit units -> RGB with IDC's own coefficients (0x59c, 0x160, 0x2db, 0x717 / 1024)
    yy = y10 * np.float32(0.25)
    cb *= np.float32(0.25)
    cr *= np.float32(0.25)
    k = np.float32(DSP_OUTPUT_SCALE)
    out = np.empty((yy.size, 3), dtype=np.float32)
    out[:, 0] = (yy + np.float32(1436 / 1024) * cr) * k
    out[:, 1] = (yy - np.float32(352 / 1024) * cb - np.float32(731 / 1024) * cr) * k
    out[:, 2] = (yy + np.float32(1815 / 1024) * cb) * k
    np.clip(out, 0.0, 1.0, out=out)
    return srgb_decode(out).reshape(*shp, 3)


# ---- IDC's colour processor, bit for bit ------------------------------------------
# render_sony_dsp() above is the processor in floating point (about 0.2 / 255 from
# IDC on flat colour). This is the same processor in IDC's own integer arithmetic,
# with the steps the float version leaves out, so a full-size render comes out as
# IDC's TIFF does (0.64 / 255 over all pixels, 0.10 / 255 on flat colour, from
# 0.83 and 0.22 over 11 shots). It covers the colour stage, IDC's output step and its
# YCbCr -> RGB conversion:
#  - every multiply, shift, rounding and clip of the colour stage, and its 8-bit
#    luma and chroma bytes;
#  - chroma suppression from a [1 3 3 1] / 2 filtered luma, the chroma kill, a
#    [1 1] average of R-G and B-G, and 4:2:2 chroma: both pixels of a pair get the
#    pair's Cb and Cr, and pairs start at odd sensor columns;
#  - IDC's 3x3 sigma filter on the luma, as its exported TIFFs show it
#    (thresholds 0: only neighbours equal to the centre are averaged, otherwise the
#    4 direct ones, centre weight 12; no aperture sharpening);
#  - 8 -> 16 bits as C16 = 256 C8 - C8 / 2 and Y16 = 255.5 Y8: the 0.9945 output
#    factor v19 measured is 255.5 / 257 = 0.99416, and the chroma sits 64 / 65535 low.
# The stage streams along each sensor row; every state it keeps (rings, delays,
# FIR filters) is a fixed delay, so here each one is a shift along the row, and
# the result is identical to running the stage pixel by pixel. A render needs
# whole sensor rows (it starts each row at column 0) and 2 rows above and below
# for the sigma filter.

IDC_KILL_LEVEL = np.array((0, 1, 2, 2, 3, 3, 3, 3), dtype=np.int32)
IDC_KILL_MASKS = (0x80, 0x1c0, 0x3e0, 0x7f0, 0xff8, 0x1ffc, 0x3ffe, 0x7fff)
IDC_PREROLL = 8                           # columns streamed before column 0 (edge repeated)
IDC_LUMA_DELAY = 13                       # stream steps from a pixel's input to its luma byte
IDC_LUT_Y_IDENTITY = [16 * (i + 1) for i in range(80)]      # SRF2 tag 0x26: the luma output LUT


def idc_stage_registers(d, tags):
    """The colour stage's settings from the 7458-byte register dump `d` and the SRF2
    tables {0x22: tone curve, 0x24: detail curve, 0x26: luma LUT}. All of them are the same in every F828 file seen except the white balance, the digital gain
    and the eight chroma coefficients."""
    b = lambda a: d[a]
    w = lambda a: (d[a] << 8) | d[a + 1]
    s15 = lambda v: v - 0x8000 if (v & 0x7fff) > 0x3fff else (v & 0x7fff)
    s8 = lambda v: v - 0x100 if v > 0x7f else v
    p = {}
    p["m"] = [[s15(w(0x634 + 2 * (4 * i + j)) & 0x7fff) for j in range(4)] for i in range(3)]
    p["wb"] = (w(0x64c) & 0xfff, 0x100, w(0x650) & 0xfff)
    p["cap"] = (w(0x652) << 6) | 0xc3c
    p["lsel"] = b(0x626) & 7                                              # detail: low input
    p["hsel"] = b(0x627) & 7                                              # detail: high input
    p["det_thr"] = b(0x6a4)                                               # kill: edge threshold
    p["clip_thr"] = (b(0x6a5) << 2) | 3                                   # kill: clip threshold
    p["ext_en"] = b(0x6a9) & 1                                            # kill from the demosaic
    p["clip_en"] = (b(0x6a9) >> 1) & 1
    p["det_en"] = (b(0x6a9) >> 2) & 1
    p["mask0"] = IDC_KILL_MASKS[b(0x6a7) & 7]                             # kill spread
    p["mask1"] = IDC_KILL_MASKS[(b(0x6a7) >> 4) & 7]
    p["mask2"] = IDC_KILL_MASKS[b(0x6a6) & 7]
    p["lw"] = (w(0x664), w(0x666), w(0x668))                              # luma weights /64
    p["ymode"] = b(0x66b) & 7                                             # 1: + luma detail
    p["pivot"] = (b(0x678) & 0xf) << 4                                    # luma out
    yo = (b(0x676) & 0x3f) << 1
    p["yoff"] = yo - 0x80 if yo & 0x40 else yo
    p["ycon"] = b(0x679)                                                  # contrast /128
    p["knee"] = b(0x677) << 2
    p["knee_s"] = b(0x678) >> 4
    p["posterize"] = (b(0x67b) >> 1) & 1
    p["neg_y"] = b(0x67b) & 1
    p["ymax"] = (b(0x675) >> 4) * 16 + 0x30f
    p["ymin"] = (b(0x675) & 0xf) << 4
    p["ydel"] = b(0x674) & 3
    p["sup_en"] = w(0x694)                                                # chroma suppression
    p["sup_lo"] = b(0x699) << 4
    p["sup_lo_g"] = (b(0x698) >> 4) & 7
    p["sup_hi"] = (w(0x696) & 0x1ff) << 4
    p["sup_hi_sh"] = (b(0x696) >> 4) & 7
    cm = b(0x693) & 3                                                     # chroma filters
    p["cmode"] = cm
    p["f2tap"], p["f121"], p["f323"] = {2: (1, 0, 0), 3: (0, 1, 1), 4: (0, 0, 1), 5: (0, 1, 0),
                                        6: (0, 0, 0)}.get(cm, (1, 1, 1))
    p["ck_hi"] = (b(0x685) & 0x7f) << 4                                   # chroma knees
    p["ck_hi_m"] = b(0x684) & 3
    p["ck_lo"] = (b(0x687) & 0x7f) << 4
    p["ck_lo_m"] = b(0x686) & 3
    p["k688"], p["k689"], p["k68a"], p["k68b"] = (s8(b(a)) for a in (0x688, 0x689, 0x68a, 0x68b))
    p["g68c"], p["g68d"], p["g68e"], p["g68f"] = (b(a) for a in (0x68c, 0x68d, 0x68e, 0x68f))
    p["off_cb"], p["off_cr"] = s8(b(0x690)), s8(b(0x691))
    p["neg_c"] = (b(0x693) >> 2) & 1
    curve = [0] + [v & 0xfff for v in tags[0x22]][:96]
    detail = [0] + [v & 0xfff for v in tags.get(0x24, [])][:96]
    lut = list(tags.get(0x26, []))[:80]
    p["curve"] = curve
    p["curve_d"] = detail if len(detail) == len(curve) else curve
    p["lut_y"] = lut if len(lut) == 80 else list(IDC_LUT_Y_IDENTITY)
    return p


def idc_output_registers(d):
    """IDC's output-stage settings ('w(n)' is 16-bit register word n). The same in every F828 file seen, except the aperture and region
    settings, which differ between Real and Standard files and do not reach IDC's exports."""
    w = lambda n: (int(d[2 * n]) << 8) | int(d[2 * n + 1])
    s8 = lambda v: v - 0x100 if v > 0x7f else v
    p = {}
    # chroma
    p["c_neg"] = (w(1108) >> 3) & 1
    p["c_fix"] = w(1108) & 1
    p["c_fix_cb"], p["c_fix_cr"] = w(1109) >> 8, w(1109) & 0xff
    p["c_thr_cb"] = s8(w(1085) >> 8)                 # colour-region quadrants
    p["c_thr_cr"] = s8(w(1085) & 0xff)
    p["c_cen_cb"] = tuple(s8(w(n) >> 8) for n in (1087, 1089, 1091, 1093))
    p["c_cen_cr"] = tuple(s8(w(n) & 0xff) for n in (1087, 1089, 1091, 1093))
    p["c_scale"] = tuple(w(n) & 0xff for n in (1086, 1088, 1090, 1092))
    p["c_pass"] = (w(1094) >> 8) & 1                 # 1: chroma passed unchanged
    p["c_qmask"] = w(1094) >> 12                     # quadrants that set the level
    # luma and the sigma filter
    p["y_post"] = (w(1108) >> 2) & 1
    p["y_neg"] = (w(1108) >> 1) & 1
    p["s_lev_hi"] = (w(1058) >> 8) & 1
    p["s_en"] = (w(1058) >> 3) & 1
    p["s_pair_off"] = (w(1058) >> 2) & 1
    p["s_alt"] = (w(1058) >> 1) & 1
    p["s_w0"] = (w(1059) >> 8) & 0x1f                # centre weight when no neighbour passes
    p["s_w"] = w(1059) & 0x1f                        # centre weight
    p["s_fixed"] = w(1062) & 1
    p["s_adapt"] = (w(1063) >> 12) & 1
    p["s_adapt_sh"] = (w(1063) >> 8) & 3
    p["s_pattern"] = w(1063) & 0xff
    return p


def _idc_stage_params(cam, wb_post, mode, exposure=0.0):
    """The stage settings for one render: the file's, with this render's white balance
    (after the matrix, G = 1), tone curve (Real / Standard) and thresholds (idc_thresholds)."""
    p = dict(cam["stage"])
    wbp = np.asarray(wb_post, dtype=np.float64)
    p["wb"] = (int(round(wbp[0] * 256)), 0x100, int(round(wbp[2] * 256)))
    if mode != cam["mode"] and mode in _SONY_CURVE_Y:
        p["curve"] = p["curve_d"] = [int(v) for v in _SONY_CURVE_Y[mode]]
    p["sup_hi"], p["sup_lo"], p["det_thr"], p["clip_thr"] = idc_thresholds(cam, mode, exposure)
    return p


def _lag(a, k):
    """a delayed by k steps along each row, zeros before the row starts."""
    if k <= 0:
        return a
    out = np.zeros_like(a)
    out[:, k:] = a[:, :-k]
    return out


def _idc_curve(x, table):
    """The stage's segmented curve lookup, sign mirrored."""
    neg = x < 0
    a = np.where(neg, (-x) & 0xfff, x)
    s = a >> 8
    idx = np.where(s == 0, (a >> 3) & 0x1f,
          np.where(s == 1, ((a & 0xf0) | 0x200) >> 4,
          np.where(s <= 3, ((a & 0x1e0) | 0x600) >> 5,
          np.where(s <= 7, ((a & 0x3c0) | 0x1000) >> 6, ((a & 0x780) | 0x2800) >> 7))))
    fr = np.where(s == 0, (a & 7) << 4,
         np.where(s == 1, (a & 0xf) << 3,
         np.where(s <= 3, (a & 0x1f) << 2,
         np.where(s <= 7, (a & 0x3f) << 1, a & 0x7f))))
    lo = table[idx]
    v = ((((table[idx + 1] - lo) * fr) >> 6) + 1 + lo * 2) >> 1
    v = np.minimum(v, 0xfff)
    return np.where(neg, -v, v)


def idc_colour_stage(R, G, B, E, p, phase=1, H=None, K=None):
    """IDC's colour stage on whole rows of demosaiced 12-bit R, G, B, E (integer arrays)
    -> (Y8, C8, P): luma and chroma bytes at each stream step (step 0 = column -8),
    and P[step] = 1 where the chroma byte is a Cb, 0 where it is a Cr.
    H: the demosaic's sharp luminance Yhigh (12-bit units), K: its chroma-kill level 0..3."""
    i4 = np.int32
    rows, W = R.shape
    N = W + IDC_PREROLL + 16
    xs = np.clip(np.arange(N) - IDC_PREROLL, 0, W - 1)
    r, g, b, e = (np.asarray(a)[:, xs].astype(i4) for a in (R, G, B, E))
    P = ((np.arange(N) + phase) & 1).astype(np.uint8)
    ph0 = (P == 0)[None, :]
    curve = np.asarray(list(p["curve"]) + [p["curve"][-1]] * 32, dtype=i4)
    curve_d = np.asarray(list(p["curve_d"]) + [p["curve_d"][-1]] * 32, dtype=i4)
    lut = np.asarray([0] + list(p["lut_y"])[:80], dtype=i4)
    m = p["m"]
    cap = int(p["cap"])

    def matrix_wb(row, k):                       # 1-2: matrix /1024, WB after it, both clipped
        v = (((row[0] * r) >> 7) + ((row[1] * g) >> 7) + ((row[2] * b) >> 7) + ((row[3] * e) >> 7) + 4) >> 3
        np.clip(v, -0x800, 0xffc, out=v)
        v = (int(k) * v + 0x80) >> 8
        np.clip(v, -0x800, 0xffc, out=v)
        return np.minimum(v, cap, out=v)

    Rc = _idc_curve(matrix_wb(m[0], p["wb"][0]), curve)          # 3: tone curve
    Gc = _idc_curve(matrix_wb(m[1], p["wb"][1]), curve)
    Bc = _idc_curve(matrix_wb(m[2], p["wb"][2]), curve)

    # chroma kill: the clip detector's flags spread by the masks, and the demosaic's level
    kill = np.zeros((rows, N), dtype=i4)
    if p["clip_en"]:
        flag = np.zeros((rows, N), dtype=bool)
        for v in (r, g, b, e):
            flag |= np.maximum(v >> 2, 0) > p["clip_thr"]
        if flag.any():
            kidx = np.zeros((rows, N), dtype=i4)
            for bit, mask in ((1, p["mask0"]), (2, p["mask1"]), (4, p["mask2"])):
                hit = np.zeros((rows, N), dtype=bool)
                for k in range(32):
                    if (mask >> k) & 1:
                        hit |= _lag(flag, k)
                kidx += bit * hit
            kill = IDC_KILL_LEVEL[kidx]
            del kidx
        del flag
    if p["ext_en"] and K is not None:
        kill = np.maximum(kill, _lag(np.asarray(K)[:, xs].astype(i4), 7))

    # 4: luma, plus the luminance detail LUT(high) - LUT(low)
    hs, ls = p["hsel"], p["lsel"]
    if hs == 1:
        hv = ((g + e + 1) >> 1) if H is None else np.asarray(H)[:, xs].astype(i4)
    elif hs in (5, 6, 7):
        hv = {5: r, 6: g, 7: b}[hs]
    else:
        hv = np.zeros_like(r)
    if ls in (4, 6):
        lv = (e + 1 + g) >> 1
    elif ls in (5, 7):
        lv = {5: r, 7: b}[ls]
    elif ls == 1:
        lv = ((((e + b + g + r) >> 1) & 0x3fff) + 1) >> 1
    else:
        lv = np.zeros_like(r)
    hc, lc = _idc_curve(hv, curve_d), _idc_curve(lv, curve_d)
    del hv, lv, r, g, b, e
    lw0, lw1, lw2 = (int(v) for v in p["lw"])
    y = (((((lw2 * Bc) >> 3) + ((lw1 * Gc) >> 3) + ((lw0 * Rc) >> 3)) >> 2) + 1) >> 1
    y = np.where(y >= 0x7ff, 0x7ff, np.maximum(y, -0x800))
    ym = p["ymode"]
    if ym == 1:
        y = y - lc + hc
        y = np.where(y >= 0x7ff, 0x7ff, np.where(y < -0x7ff, -0x800, y))
    elif ym == 2:
        y = hc
    elif ym == 3:
        y = lc
    elif ym != 0:
        y = np.zeros_like(y)
    del hc, lc
    # (the stage's own aperture sharpening works on the demosaic's edge-detail input, which
    # this demosaic does not provide, and it is off in every F828 file: it adds nothing)
    yd = _lag(y, 1)

    # 5: luma out: output LUT, contrast, knee, clip, delay, 10 -> 8 bits
    yv = np.where(yd >= 0x7ff, 0x7ff, np.maximum(yd, -0x800))
    del yd
    ya = np.minimum(np.abs(yv), 0x4f0)
    k = ya >> 4
    lo = lut[k]
    yo = ((((lut[k + 1] - lo) * (ya & 0xf)) >> 3) + 1 + lo * 2) >> 1
    del ya, k, lo
    yo = np.where(yo > 0x7fe, 0x7ff, yo)
    yo = np.where(yv < 0, -yo, yo)
    del yv
    u = yo - p["pivot"] * 4
    u = np.where(u < -0x800, u & 0xfff, u)
    u = (((p["ycon"] * u) >> 6) + 1 + (p["yoff"] + p["pivot"]) * 8) >> 1
    u = np.where(u >= 0x7ff, 0x7ff, np.where(u < 1, 0, u))
    t = u - p["knee"]
    t = np.where(t < 0, t * 16, (16 - p["knee_s"]) * t)
    u = (((t >> 3) + 1) >> 1) + p["knee"]
    u = np.where(u >= 0x3fd, 0x3fc, np.where(u < 0, 0, u))
    if p["posterize"] == 1:
        u = u & ~0x7f
    if p["neg_y"] == 1:
        u = u ^ 0x3ff
    u = np.where(u >= p["ymax"], p["ymax"], np.where(u <= p["ymin"], p["ymin"], u))
    Y8 = _lag(np.minimum(((u >> 1) + 1) >> 1, 0xff), 10 + int(p["ydel"])).astype(np.uint8)
    del u, t, yo

    # 6: chroma suppression in deep shadows and highlights, from the [1 3 3 1] / 2 luma
    yprev = _lag(y, 1)
    a = (yprev if p["cmode"] == 3 else y) + yprev
    del y, yprev
    ysup = (_lag(a, 3) + _lag(a, 2) * 2 + 1 + _lag(a, 1)) >> 1
    del a
    if not p["sup_en"]:
        sup = np.full((rows, N), 0xff, dtype=i4)
    else:
        hi = np.maximum(ysup - p["sup_hi"], 0)
        sh = p["sup_hi_sh"]
        hi = (hi >> (7 - sh)) if 1 <= sh <= 6 else (hi if sh == 7 else np.zeros_like(hi))
        sup = np.minimum(np.clip(0xff - hi, 0, 0xff),
                         np.clip(0xff - ((np.maximum(p["sup_lo"] - ysup, 0) * p["sup_lo_g"]) >> 3), 0, 0xff))
        del hi
    del ysup
    supd = _lag(sup, 7)
    del sup

    # 7: colour differences (knees), kill, [1 1] average, suppression
    ck_hi, ck_lo, hm, lm = p["ck_hi"], p["ck_lo"], p["ck_hi_m"], p["ck_lo_m"]

    def knee_hi(v):
        t = v - ck_hi
        t2 = {1: lambda: t + (t >> 1), 2: lambda: t, 3: lambda: t >> 1}.get(hm, lambda: t * 2)()
        return np.where(t < 1, t * 2, t2)

    def low_alt(t):
        if lm == 0:
            return (t * 2 + 1 + ck_lo * 2) >> 1
        if lm == 1:
            return (t + (t >> 1) + 1 + ck_lo * 2) >> 1
        if lm == 3:
            return ((t >> 1) + 1 + ck_lo * 2) >> 1
        return (t + 1 + ck_lo * 2) >> 1

    def half_knee(v):
        t = v - ck_lo
        return np.where(t < 0, low_alt(t), (knee_hi(v) + 1 + ck_hi * 2) >> 1)

    l18 = half_knee(Bc)
    g17 = half_knee(Gc)
    t = Rc - ck_lo
    ralt = {0: lambda: t * 2, 1: lambda: t + (t >> 1), 3: lambda: t >> 1}.get(lm, lambda: t)()
    r16 = np.where(t < 0, ralt + 1 + ck_lo * 2, knee_hi(Rc) + 1 + ck_hi * 2)
    del Rc, Gc, Bc, t, ralt

    def chroma_filter(val):
        v = _lag(val, 7)
        v = np.where(kill == 1, v >> 1, np.where(kill == 2, v >> 2, v))
        v = np.where(kill == 3, 0, np.where(v >= 0x800, 0x7ff, np.maximum(v, -0x800)))
        prev = _lag(v, 1)
        v2 = (v if p["f2tap"] else prev) + prev
        del v, prev
        tq = _lag(v2, 1) * 4 if not p["f121"] else _lag(v2, 2) + _lag(v2, 1) * 2 + v2
        del v2
        q = ((tq + _lag(tq, 2)) * 3 + _lag(tq, 1) * 2) >> 3 if p["f323"] else _lag(tq, 1)
        return (((q * supd) >> 10) + 1) >> 1

    drf = chroma_filter((r16 >> 1) - g17)
    dbf = chroma_filter(l18 - g17)
    del r16, g17, l18, kill, supd

    # 8: 'Real' chroma, Cb on P = 1 steps, Cr on P = 0 steps, by the signs of the differences
    iv2 = np.where(ph0, _lag(dbf, 2), _lag(drf, 1))
    del drf, dbf
    neg = iv2 < 0
    coef = np.where(ph0, np.where(neg, p["k68a"], p["k688"]), np.where(neg, p["k68b"], p["k689"]))
    xd = _lag(np.clip((coef * iv2) >> 6, -0x1000, 0xfff), 2)
    tt = (xd + 1 + np.where(ph0, _lag(iv2, 3), _lag(iv2, 1)) * 2) >> 1
    del iv2, neg, coef, xd
    tn = tt < 0
    gg2 = np.where(ph0, np.where(tn, p["g68f"], p["g68d"]), np.where(tn, p["g68e"], p["g68c"]))
    c = ((((gg2 * tt) >> 6) + 1) >> 1) + np.where(ph0, p["off_cr"], p["off_cb"]) * 4
    c = np.where(c >= 0x1fc, 0x1fc, np.where(c < -0x1fb, -0x1fc, c))
    c8 = ((((c >> 1) + 1) ^ -0xff) >> 1) & 0xff
    if p["neg_c"] == 1:
        c8 = c8 ^ 0xff
    return Y8, c8.astype(np.uint8), P


def _idc_pixels(Y8, C8, P, W):
    """Stream bytes -> per-pixel Y8, Cb8, Cr8 (rows, W) and the parity of the columns that
    start a 4:2:2 pair. IDC's output loop gives both pixels of a pair the Cb at the pair's
    first and the Cr at its second stream step."""
    n0 = IDC_PREROLL + IDC_LUMA_DELAY
    nn = np.arange(W) + n0
    first = P[nn] == 1
    ncb = np.where(first, nn, nn - 1)
    return Y8[:, n0:n0 + W], C8[:, ncb], C8[:, ncb + 1], int(np.argmax(first)) % 2


_IDC_RECIP = np.array([0, 0x1fff, 0x1000, 0xaab, 0x800, 0x666, 0x555, 0x492, 0x400, 0x38e, 0x333, 0x2e9,
                       0x2ab, 0x276, 0x249, 0x222, 0x200, 0x1e2, 0x1c7, 0x1af, 0x19a, 0x186, 0x174, 0x164,
                       0x155, 0x148, 0x13b, 0x12f, 0x125, 0x11a, 0x111, 0x108] + [0] * 32, dtype=np.int32)
_IDC_FOUR = (0, 1, 0, 1, 1, 0, 1, 0)     # the sigma filter's fallback: the 4 direct neighbours


def _abs1(t):
    """IDC's |t| in its chroma step: -t - 1 for negative t."""
    return np.where(t < 0, -t - 1, t)


def _shift_rows(a, k):
    return a[np.clip(np.arange(a.shape[0]) + k, 0, a.shape[0] - 1)]


def _shift_cols(a, k):
    return a[:, np.clip(np.arange(a.shape[1]) + k, 0, a.shape[1] - 1)]


def idc_output16(y8, cb8, cr8, p, first):
    """IDC's 16-bit RGB from the per-pixel stage bytes (whole rows; the frame's edge rows
    and columns repeat): the chroma's 8 -> 16 bits and colour-region levels, the 3x3
    sigma filter on the luma as IDC's exports show it, then YCbCr -> RGB. p: idc_output_registers(); first: pair parity."""
    i4 = np.int32
    cb = cb8.astype(i4)
    cr = cr8.astype(i4)
    if p["c_fix"] == 1:
        cb = np.full_like(cb, p["c_fix_cb"])
        cr = np.full_like(cr, p["c_fix_cr"])
    if p["c_neg"] == 1:
        cb, cr = cb ^ 0xff, cr ^ 0xff
    # colour-region level (0..3) of each pair; it lowers the sigma filter's centre weight
    u0, v0 = cb - 0x80, cr - 0x80
    q = np.where(v0 < p["c_thr_cr"], (u0 < p["c_thr_cb"]).astype(i4) + 2, (u0 < p["c_thr_cb"]).astype(i4))
    u = u0 - np.asarray(p["c_cen_cb"], i4)[q]
    v = v0 - np.asarray(p["c_cen_cr"], i4)[q]
    au, av = _abs1(u), _abs1(v)
    s = (((_abs1(au - 2 * av) + _abs1(2 * au - av) + ((_abs1(au - av) * 3) >> 1)) >> 2) + 1) >> 1
    dd = (np.asarray(p["c_scale"], i4)[q] * (av + s + au)) >> 5
    lev = np.where(dd >= 64, 0, np.where(dd >= 32, 1, np.where(dd >= 16, 2, 3))).astype(i4)
    lev = np.where(((int(p["c_qmask"]) >> q) & 1) == 1, lev, 0)
    del u0, v0, q, u, v, au, av, s, dd
    cb16 = cb * 256 - (cb >> 1)
    cr16 = cr * 256 - (cr >> 1)
    del cb, cr
    y = y8.astype(i4)
    if p["y_post"] == 1:
        y = y & 0xe0
    if p["y_neg"] == 1:
        y = y ^ 0xff
    # per pair: the levels summed over 5 rows x 3 pairs
    W = y.shape[1]
    qi = (np.arange(W) + first) // 2
    lp = lev[:, np.maximum(2 * np.arange(qi[-1] + 1) - first, 0)]
    acc = _shift_rows(lp, -2) + _shift_rows(lp, -1) + lp + _shift_rows(lp, 1) + _shift_rows(lp, 2)
    S = (_shift_cols(acc, -1) + acc + _shift_cols(acc, 1))[:, qi]
    del lev, lp, acc
    # 3x3 sigma filter, thresholds 0 (IDC's exports; the registers' 12 are not what they show)
    up, dn = _shift_rows(y, -1), _shift_rows(y, 1)
    nb = [_shift_cols(up, -1), up, _shift_cols(up, 1), _shift_cols(y, -1), _shift_cols(y, 1),
          _shift_cols(dn, -1), dn, _shift_cols(dn, 1)]
    del up, dn
    f = [y == n for n in nb]
    count = f[0].astype(i4)
    for fi in f[1:]:
        count += fi
    if not p["s_en"]:
        f = [np.zeros_like(f[0])] * 8
    elif p["s_fixed"] == 1:
        f = [np.full(y.shape, bool((p["s_pattern"] >> i) & 1)) for i in range(8)]
    elif p["s_pair_off"] != 0 or p["s_alt"] != 0:
        u3 = p["s_alt"]
        b10 = p["s_pair_off"] == 0
        enter = (u3 & ((count != 0) & b10)) == 0
        inner = (u3 & (count == 0)) == 0
        pairs = [f[0] & f[7], f[1] & f[6], f[2] & f[5], f[3] & f[4]]
        sym = [pairs[0], pairs[1], pairs[2], pairs[3], pairs[3], pairs[2], pairs[1], pairs[0]]
        ei, en = enter & inner, enter & ~inner
        f = [np.where(ei, sym[i], np.where(en, bool(_IDC_FOUR[i]), f[i])) for i in range(8)]
    adapt = np.maximum(p["s_w"] - ((S & 0xff) >> ((4 - p["s_adapt_sh"]) & 0x1f)), 1) if p["s_adapt"] else None
    if p["s_en"] == 1 and p["s_alt"] == 1:
        wgt = np.where(count == 0, p["s_w0"], adapt if adapt is not None else p["s_w"]).astype(i4)
    elif adapt is None:
        wgt = np.full(y.shape, p["s_w"], i4)
    else:
        wgt = adapt.astype(i4)
    del S, count, adapt
    tot = y * wgt
    n = wgt
    for fi, nv in zip(f, nb):
        tot += fi * nv
        n = n + fi
    del f, nb, wgt
    y16 = (tot * _IDC_RECIP[np.minimum(n, 63)]) >> 5
    del tot, n
    np.clip(y16, 0, 0xffff, out=y16)
    y16 -= y16 >> 9
    # YCbCr -> RGB16: ((C - 0x8000) k + 1024 Y) >> 10, < 1 -> 0, > 0xfffe -> 0xffff
    y16 <<= 10
    cb16 -= 0x8000
    cr16 -= 0x8000
    out = np.empty(y8.shape + (3,), np.uint16)
    for k, v in enumerate(((cr16 * 0x59c + y16) >> 10, (cb16 * -0x160 + cr16 * -0x2db + y16) >> 10,
                           (cb16 * 0x717 + y16) >> 10)):
        out[..., k] = np.where(v < 1, 0, np.minimum(v, 0xffff))
    return out


def render_sony_exact(raw12, cam, wb_post, mode="real", detail=None, kill=None, context=(0, 0),
                      exposure=0.0):
    """render_sony_dsp in IDC's own arithmetic, with its 4:2:2 chroma and output filters:
    (rows, cols, 4) un-balanced RGBE in the processor's 12-bit units, WHOLE sensor rows
    (column 0 = the frame's column 0) -> LINEAR sRGB float32, exactly IDC's 16-bit output.
    `context` = (rows above, rows below) included only for the output filters (2 each
    is enough); they are not returned. detail / kill / exposure: as render_sony_dsp."""
    if cam.get("stage") is None:
        raise ValueError("no colour-stage registers")
    p = _idc_stage_params(cam, wb_post, mode, exposure)
    x = np.clip(raw12, -8192.0, 65535.0)
    xi = np.rint(x).astype(np.int32)
    H = K = None
    if detail is not None:                       # the stage's sharp-luminance input, Yhigh
        H = np.rint(np.clip(detail + np.float32(0.5) * (x[..., 1] + x[..., 3]), -8192.0, 65535.0)).astype(np.int32)
    if kill is not None:
        K = np.select([kill >= 0.75, kill >= 0.375, kill >= 0.125], [0, 1, 2], 3).astype(np.int32)
    del x
    Y8, C8, P = idc_colour_stage(xi[..., 0], xi[..., 1], xi[..., 2], xi[..., 3], p, 1, H, K)
    del xi, H, K
    y8, cb8, cr8, first = _idc_pixels(Y8, C8, P, raw12.shape[1])
    del Y8, C8
    rgb16 = idc_output16(y8, cb8, cr8, cam["post"], first)
    t, b = context
    if t or b:
        rgb16 = rgb16[t:rgb16.shape[0] - b]
    return srgb_decode(rgb16.astype(np.float32) * np.float32(1.0 / 65535.0))


def srgb_to_rgb(space):
    """3x3 matrix: linear sRGB (D65) -> linear RGB of the given output space."""
    if space in ("srgb", "linsrgb"):
        return np.eye(3, dtype=np.float32)
    return (xyz_d50_to_rgb(space) @ SRGB_LINEAR_TO_XYZ_D50).astype(np.float32)


def output_matrix(edit, space):
    """The matrix develop() needs for this edit: camera RGBE -> output RGB (3x4)
    for the default rendering, linear sRGB -> output RGB (3x3) for Sony's."""
    if getattr(edit, "render", "default") == "sony" and not edit.ir:
        return srgb_to_rgb(space)
    return camera_to_rgb(space)


def srf_preview_jpeg(path):
    """Bytes of the camera's own 640x480 JPEG preview (IFD1 of the .SRF), or None."""
    try:
        with open(path, "rb") as fh:
            f = fh.read(1 << 20)
        bo = "<" if f[:2] == b"II" else ">"
        ifd0 = struct.unpack(bo + "I", f[4:8])[0]
        n0 = struct.unpack(bo + "H", f[ifd0:ifd0 + 2])[0]
        ifd1 = struct.unpack(bo + "I", f[ifd0 + 2 + 12 * n0:ifd0 + 6 + 12 * n0])[0]
        tags = {}
        for i in range(struct.unpack(bo + "H", f[ifd1:ifd1 + 2])[0]):
            t, _, _, v = struct.unpack(bo + "HHII", f[ifd1 + 2 + 12 * i:ifd1 + 14 + 12 * i])
            tags[t] = v
        start, length = tags[0x201], tags[0x202]
        if start + length > len(f):
            with open(path, "rb") as fh:
                fh.seek(start)
                return fh.read(length)
        return f[start:start + length]
    except Exception:
        return None


def asshot_from_preview(path, superpixel_norm):
    """The camera's real as-shot white balance as (kelvin, tint), or None.

    The camera rendered its embedded preview with the as-shot white balance, so
    we look for the raw R and B gains that make our rendering line up with the
    preview, letting the camera's own preview tone curve be any monotonic curve.
    (Fitting Kelvin + tint directly is badly conditioned because the camera's
    preview treats the emerald channel a little differently.)
    superpixel_norm: half-size black-subtracted, normalised, UN-balanced RGBE.
    """
    from scipy.ndimage import zoom
    from scipy.optimize import least_squares
    jpg = srf_preview_jpeg(path)
    if not jpg:
        return None
    try:
        th = np.asarray(Image.open(io.BytesIO(jpg)).convert("RGB"), dtype=np.float32) / 255.0
    except Exception:
        return None
    if th.shape[:2] != (480, 640):
        return None
    sp = superpixel_norm
    h, w = sp.shape[:2]
    if (h, w) == (1230, 1644):                     # where the camera's 3264x2448 frame sits
        sp = sp[2:1226, 5:1637]
    else:                                          # unexpected margins: centred 4:3 crop
        ch, cw = min(h, w * 3 // 4), min(w, h * 4 // 3)
        y0, x0 = (h - ch) // 2, (w - cw) // 2
        sp = sp[y0:y0 + ch, x0:x0 + cw]
    sh, sw = sp.shape[:2]
    small = np.stack([zoom(sp[..., c].astype(np.float64), (480.0 / sh, 640.0 / sw), order=1)
                      for c in range(4)], -1)[:480, :640]
    if small.shape[:2] != (480, 640):
        return None
    x = small.reshape(120, 4, 160, 4, 4).mean(axis=(1, 3)).reshape(-1, 4)
    yb = th.reshape(120, 4, 160, 4, 3)
    y, ys = yb.mean(axis=(1, 3)).reshape(-1, 3), yb.std(axis=(1, 3)).reshape(-1, 3)
    ok = (ys.max(1) < 0.03) & (y.max(1) < 0.97) & (y.min(1) > 0.03) & (x.max(1) < 0.85)
    X, Y = x[ok], y[ok].astype(np.float64)
    if len(X) < 200:
        return None
    if len(X) > 2500:                              # plenty for 2 gains; keeps this well under a second
        pick = np.linspace(0, len(X) - 1, 2500).astype(int)
        X, Y = X[pick], Y[pick]
    M = SONY_RGBE_TO_CURVE.T.astype(np.float64)
    knots = (np.arange(13) / 12.0) ** 2
    ref = _SONY_REF_MULT
    s = max(float(np.percentile((X * ref) @ M, 99.5)), 1e-6)

    def resid(p):
        g = ref * np.array([p[0], 1.0, p[1], 1.0])
        v = np.concatenate([[p[2]], p[2] + np.cumsum(np.exp(p[3:]))])
        return (np.interp((X * g) @ M / s, knots, v) - Y).ravel()

    p0 = np.concatenate([[1.0, 1.0, 0.0], np.log(np.diff(np.sqrt(knots)) + 1e-3)])
    try:
        r = least_squares(resid, p0, max_nfev=400, ftol=1e-7, xtol=1e-7)
    except Exception:
        return None
    if not (r.x[0] > 0 and r.x[1] > 0):
        return None
    tt = temp_tint_from_wb(ref * np.array([r.x[0], 1.0, r.x[1], 1.0]), rgb_only=True)
    return None if tt is None else clamp_temp_tint(tt)


# ==========================================
# --- SENSOR DATA HELPERS ---
# ==========================================

# Raw files are decoded on several threads (thumbnails, export, opening). LibRaw
# is only guaranteed re-entrant in some builds, and decoding is quick, so every
# use of rawpy takes this lock.
RAW_LOCK = threading.Lock()


def read_sensor(path):
    """Unsubtracted CFA in DN (float32), colour map (0=R 1=G 2=B 3=E),
    candidate black levels, the white level, and the camera's as-shot WB."""
    with RAW_LOCK, rawpy.imread(path) as raw:
        if raw.color_desc != b'RGBE':
            raise ValueError(f"Unexpected CFA colours {raw.color_desc!r}, expected b'RGBE'")
        full = raw.raw_image.astype(np.float32)      # whole sensor incl. masked borders (a copy)
        cmap_full = raw.raw_colors.astype(np.uint8)
        s = raw.sizes
        libraw_black = np.array(raw.black_level_per_channel, dtype=np.float32)
        white = float(raw.white_level) or FALLBACK_WHITE
        t, l, h, w = s.top_margin, s.left_margin, s.height, s.width
        raw_width = s.raw_width
        try:
            flip = int(getattr(s, "flip", 0) or 0)
        except Exception:
            flip = 0
        try:
            cam_wb = [float(x) for x in raw.camera_whitebalance]
        except Exception:
            cam_wb = None

    # Robust per-file black: median of the masked strip, away from the image edge
    border_black = None
    x0, x1 = l + w + OB_SKIP_NEAR, raw_width - OB_SKIP_FAR
    if x1 - x0 >= 4:
        strip = full[t:t + h, x0:x1]
        smap = cmap_full[t:t + h, x0:x1]
        # Some files carry zeros / stray values at the far end of the masked strip;
        # only plausible black readings take part in the median.
        sane = (strip > 100.0) & (strip < 2000.0)
        vals = [strip[(smap == c) & sane] for c in range(4)]
        if all(v.size >= 100 for v in vals):
            border_black = np.array([np.median(v) for v in vals], dtype=np.float32)

    cfa = full[t:t + h, l:l + w].copy()
    cmap = cmap_full[t:t + h, l:l + w].copy()
    del full, cmap_full

    if sorted(cmap[:2, :2].ravel().tolist()) != [0, 1, 2, 3]:
        raise ValueError("Expected every 2x2 tile to hold one R, G, B and E sample")

    # What LibRaw reports as the camera WB comes from the .SRF's stored WB tags,
    # which hold the same numbers (404 / 256 / 751) in every F828 file, whatever
    # the light. It is kept only as a fallback; the real as-shot white balance is
    # recovered from the embedded preview by sensor_asshot() when first needed.
    tag_wb = None
    if cam_wb:
        tt = temp_tint_from_wb(cam_wb[:3], rgb_only=True)
        if tt and 1500.0 <= tt[0] <= 40000.0 and abs(tt[1]) <= 200.0:
            tag_wb = clamp_temp_tint(tt)

    try:
        camera = read_srf_camera(path)
    except Exception:
        camera = None

    return {"path": path, "cfa": cfa, "cmap": cmap, "white": white, "camera": camera,
            "black": {"camera": None if camera is None else camera["black"].copy(),
                      "border": border_black,
                      "fixed": FIXED_BLACK_RGBE.copy(),
                      "libraw": libraw_black},
            "camera_wb": cam_wb, "tag_wb": tag_wb,
            "asshot": None, "asshot_source": None,       # filled in by sensor_asshot()
            # LibRaw flip code -> number of clockwise quarter turns
            "quarter_turns": {3: 2, 5: 3, 6: 1}.get(flip, 0)}


def sensor_asshot(sensor):
    """The camera's as-shot white balance as (kelvin, tint), worked out once per
    file: from the camera registers when the file has them (exact), else from the
    embedded preview (about a second), else the stored WB tags, else None.
    sensor["asshot_source"] says which one was used."""
    if sensor.get("asshot_source") is None:
        tt, source = None, "none"
        if sensor.get("camera") is not None:
            try:
                tt = dsp_temp_tint(sensor["camera"])
                source = "camera" if tt else source
            except Exception:
                tt = None
        if tt is None and sensor.get("path"):
            try:
                black = sensor["black"]["border"]
                if black is None:
                    black = sensor["black"]["fixed"]
                sp = normalise_rgbe(cfa_to_superpixel(sensor["cfa"], sensor["cmap"]), black, sensor["white"])
                tt = asshot_from_preview(sensor["path"], sp)
                source = "preview" if tt else source
            except Exception:
                tt = None
        if tt is None and sensor.get("tag_wb"):
            tt, source = sensor["tag_wb"], "tags"
        sensor["asshot"], sensor["asshot_source"] = tt, source
    return sensor["asshot"]


def cfa_to_superpixel(cfa, cmap):
    """One R, G, B and E sample per 2x2 tile -> half-size 4-channel image, no interpolation."""
    h, w = cfa.shape
    h, w = h - h % 2, w - w % 2
    out = np.empty((h // 2, w // 2, 4), dtype=np.float32)
    for dy in (0, 1):
        for dx in (0, 1):
            out[..., int(cmap[dy, dx])] = cfa[dy:h:2, dx:w:2]
    return out


def block_mean(img, k):
    h, w, c = img.shape
    h, w = h - h % k, w - w % k
    return img[:h, :w].reshape(h // k, k, w // k, k, c).mean(axis=(1, 3))


def demosaic_bilinear(cfa, cmap):
    """Normalised-convolution bilinear demosaic. Linear, so zero-mean noise stays zero-mean."""
    h, w = cfa.shape
    out = np.empty((h, w, 4), dtype=np.float32)

    def one(c):
        m = (cmap == c).astype(np.float32)
        num = convolve(cfa * m, BILINEAR_KERNEL, mode='mirror')
        den = convolve(m, BILINEAR_KERNEL, mode='mirror')
        out[..., c] = num / np.maximum(den, 1e-6)

    list(_develop_pool().map(one, range(4)))
    return out


# ---- edge-aware demosaic -----------------------------------------------------
# "Guided residual interpolation".  A sharp luminance image is taken from the
# mosaic itself, then every colour is fitted to that luminance over small
# neighbourhoods (colour = a * luminance + b).  Edges in the luminance therefore
# carry over to all four colours instead of being blurred, and neighbourhoods
# where the straight-line fit is poor are given little say.  Whatever the fit
# still misses at the real samples is spread bilinearly, so measured pixels are
# reproduced exactly.  Three passes, each using the previous result as a better
# luminance.

LUMA_TAPS = np.array([-1.0, 2.0, 6.0, 2.0, -1.0], dtype=np.float32) / 8.0   # zero response at the mosaic frequency
DEMOSAIC_MODES = [("IDC  (Sony's own)", "idc"), ("Edge-aware", "guided"), ("Bilinear  (v15)", "bilinear")]
DEMOSAIC_NOTES = {
    "idc": "Image Data Converter's own method: clean colour, low noise and no colour fringes on fine "
           "lines. With Sony colour, exports match IDC's own files.",
    "guided": "Follows edges in every colour: a little more fine detail than IDC, but more colour noise "
              "and some fringing on fine lines.",
    "bilinear": "v15's method: the softest, strictly linear.",
}


def _box(x, r):
    return uniform_filter(x, size=2 * r + 1, mode="mirror")


def _fill_lattice(sub, oy, ox, shape):
    """Bilinear fill of a plane known on the pixels (oy::2, ox::2) to the full grid."""
    h, w = shape
    p = np.pad(sub, 1, mode="edge")
    rows = np.empty((sub.shape[0], w), dtype=np.float32)
    rows[:, ox::2] = sub
    if ox == 0:
        rows[:, 1::2] = 0.5 * (p[1:-1, 1:-1] + p[1:-1, 2:])
    else:
        rows[:, 0::2] = 0.5 * (p[1:-1, :-2] + p[1:-1, 1:-1])
    out = np.empty(shape, dtype=np.float32)
    out[oy::2] = rows
    rp = np.pad(rows, ((1, 1), (0, 0)), mode="edge")
    if oy == 0:
        out[1::2] = 0.5 * (rp[1:-1] + rp[2:])
    else:
        out[0::2] = 0.5 * (rp[:-2] + rp[1:-1])
    return out


def demosaic_guided(cfa, cmap, black, white, passes=3):
    """Edge-aware demosaic of the 2x2 RGBE mosaic. Input and output are in DN.
    `black` / `white` only set the working scale; nothing is clipped. A mosaic
    with an odd side (the F828's is 2460 x 3287) is mirrored out by one row or
    column, which keeps the colour pattern, and cut back afterwards. (v19 fell
    back to bilinear here, so its "edge-aware" was bilinear on every F828 file.)"""
    h0, w0 = cfa.shape
    if h0 % 2 or w0 % 2:
        pad = ((0, h0 % 2), (0, w0 % 2))
        cfa, cmap = np.pad(cfa, pad, mode="reflect"), np.pad(cmap, pad, mode="reflect")
    h, w = cfa.shape
    black, scale = float(black), max(float(white) - float(black), 1.0)
    x = (cfa - np.float32(black)) / np.float32(scale)          # ~0..1, keeps float32 products accurate
    eps = np.float32(1e-5)
    lum = convolve1d(convolve1d(x, LUMA_TAPS, axis=0, mode="mirror"), LUMA_TAPS, axis=1, mode="mirror")
    where = {int(cmap[dy, dx]): (dy, dx) for dy in (0, 1) for dx in (0, 1)}
    out = np.empty((h, w, 4), dtype=np.float32)

    def one(c):
        oy, ox = where[c]
        ls, cs = lum[oy::2, ox::2], x[oy::2, ox::2]              # this colour's own half-size lattice
        ml, mc = _box(ls, 1), _box(cs, 1)
        cov = _box(ls * cs, 1) - ml * mc
        a = cov / (_box(ls * ls, 1) - ml * ml + eps)
        misfit = np.maximum(_box(cs * cs, 1) - mc * mc - a * cov, 0.0)
        trust = 1.0 / (misfit + eps)
        a = _box(a * trust, 1) / _box(trust, 1)
        # colour = a * lum + b, plus the misfit at the samples spread bilinearly; the b terms cancel:
        # fill(b) + fill(cs - (a ls + b)) = fill(cs - a ls)
        out[..., c] = _fill_lattice(a, oy, ox, (h, w)) * lum + _fill_lattice(cs - a * ls, oy, ox, (h, w))

    for _ in range(passes):
        list(_develop_pool().map(one, range(4)))
        lum = out.mean(axis=2)
    out *= np.float32(scale)
    out += np.float32(black)
    return np.ascontiguousarray(out[:h0, :w0]) if (h, w) != (h0, w0) else out


# ---- Image Data Converter's own demosaic --------------------------------------
# Recreated to match IDC SR 1.1's output. All of its settings come from the camera registers in the .SRF, and they are the same
# in every F828 file examined.
#  - Colour: R, G, B and E are each a separable low-pass of their own lattice,
#    taps [5 15 26 38 44 44 38 26 15 5] / 128 (each lattice parity sums to 128),
#    evaluated at the centre of every 2x2 cell. On its own this is soft.
#  - Luminance: a sharp "Yhigh" from the G / E checkerboard, (-1 5 5 -1) / 8 along
#    each cell's G / E diagonal. IDC adds LUT(Yhigh) - LUT((G + E) / 2) to the
#    luma right after its luma clip (LUT = the tone curve), so the picture has the
#    soft colour planes' colour and the G / E checkerboard's sharpness.
#  - Chroma kill: where the G / E samples step very steeply from left to right
#    (|A| / 8 > 160, register 0x6a4) IDC scales the colour differences by 0 on
#    that pixel, 1/4 one pixel either side and 1/2 two pixels either side.
#    IDC's false-colour detector (registers 0x764..0x76b) can never trigger with
#    the F828's settings, and no aperture sharpening is added (0x66a = 1).
# The frame carries two planes after R G B E: the luminance detail
# Yhigh - (G + E) / 2 in the processor's 12-bit units, and the chroma-kill
# factor. develop() uses them: exactly as IDC does for the Sony rendering, and
# for everything else as one factor on all four colours (the sharp luminance at
# the same colour), with the same chroma kill.

IDC_TAPS_A = np.array([5, 26, 44, 38, 15], dtype=np.float32) / np.float32(128.0)
IDC_TAPS_B = IDC_TAPS_A[::-1].copy()
IDC_V12_MIN, IDC_V12_MAX = -128.0, 4092.0 - 128.0     # the processor's clip, without its +128 pedestal
IDC_KILL_SCALE = np.array([1.0, 0.5, 0.25, 0.0], dtype=np.float32)
IDC_DETAIL_EPS = np.float32(4.0)        # 12-bit units (~0.1 % of white): keeps the detail factor finite at black
IDC_DETAIL_MAX = np.float32(8.0)
IDC_PLANES = 6                          # R G B E + luminance detail + chroma kill


def idc_demosaic_available():
    """This build has IDC's demosaic. A file without camera registers (not an
    F828 .SRF, or damaged) falls back to the edge-aware one."""
    return True


def idc_settings(cam):
    """The demosaic settings IDC reads from the camera registers."""
    d = cam["regs"]
    return {"luma_detail": (d[0x66b] & 7) == 1,               # colour stage adds the luminance detail
            "kill": bool((d[0x6a9] >> 2) & 1),                  # aperture edge flag drives the chroma kill
            "kill_threshold": idc_thresholds(cam)[2] if cam.get("stage") else int(d[0x6a4])}


def _idc_interp(sub, o, n, axis):
    """Values at positions i + 1/2 (i = 0 .. n-1) along `axis`, from a lattice
    of samples at o, o + 2, o + 4, ... (the rows or columns of `sub`)."""
    fa = correlate1d(sub, IDC_TAPS_A, axis=axis, mode="mirror")
    fb = correlate1d(sub, IDC_TAPS_B, axis=axis, mode="mirror")
    i = np.arange(n)
    q = (i - o) % 2
    m = (i - o - q) // 2
    last = sub.shape[axis] - 1
    ia, ib = np.nonzero(q == 0)[0], np.nonzero(q == 1)[0]
    ja, jb = np.clip(m[ia], 0, last), np.clip(m[ib] + 1, 0, last)
    shape = list(sub.shape)
    shape[axis] = n
    out = np.empty(shape, dtype=np.float32)
    if axis == 0:
        out[ia], out[ib] = fa[ja], fb[jb]
    else:
        out[:, ia], out[:, ib] = fa[:, ja], fb[:, jb]
    return out


def _idc_yhigh(v, ge):
    """IDC's sharp luminance: (-1 5 5 -1) / 8 along the G / E diagonal of the 2x2
    cell whose top-left sample is (i, j), in 12-bit units."""
    h, w = v.shape
    vp = np.pad(v, 3, mode="reflect")
    at = lambda dy, dx: vp[3 + dy:3 + dy + h, 3 + dx:3 + dx + w]
    back = 5.0 * (at(0, 0) + at(1, 1)) - at(-1, -1) - at(2, 2)      # "\" diagonal through the cell
    fwd = 5.0 * (at(0, 1) + at(1, 0)) - at(-1, 2) - at(2, -1)       # "/" diagonal
    y = np.where(ge, back, fwd)
    y *= np.float32(0.125)
    np.clip(y, IDC_V12_MIN, IDC_V12_MAX, out=y)
    return y


def _idc_kill(v, ge, threshold):
    """IDC's chroma-kill factor (1, 1/2, 1/4, 0) from its aperture signal: the
    G / E samples of each column interpolated to the cell centre, (3 near + far) / 4,
    differenced two columns apart (the same colour)."""
    h, w = v.shape
    vp = np.pad(v, ((2, 2), (0, 0)), mode="reflect")
    near = np.where(ge, vp[2:2 + h], vp[3:3 + h])
    far = np.where(ge, vp[4:4 + h], vp[1:1 + h])
    f = np.float32(0.75) * near + np.float32(0.25) * far
    del near, far, vp
    fp = np.pad(f, ((0, 0), (0, 2)), mode="reflect")
    a = (fp[:, :-2] - fp[:, 2:]) * np.float32(0.5)
    flag = (np.abs(np.floor(a / np.float32(8.0))) > threshold).astype(np.uint8)
    if not flag.any():
        return np.ones((h, w), dtype=np.float32)
    fz = np.pad(flag, ((0, 0), (2, 2)))
    n1 = np.maximum(fz[:, 1:1 + w], fz[:, 3:3 + w])
    n2 = np.maximum(fz[:, 0:w], fz[:, 4:4 + w])
    return IDC_KILL_SCALE[np.maximum(3 * flag, np.maximum(2 * n1, n2))]


def demosaic_idc_frame(cfa, sensor):
    """IDC's demosaic of a mosaic (DN, black not subtracted) -> (h, w, 6) frame:
    R G B E in DN (black not subtracted), IDC's luminance detail in 12-bit units,
    and its chroma-kill factor. Pixel (i, j) is IDC's value at the cell centre
    (i + 1/2, j + 1/2)."""
    cam = sensor["camera"]
    cmap = sensor["cmap"]
    st = idc_settings(cam)
    h, w = cfa.shape
    black = cam["black"]
    k = (np.float32(DSP_INPUT_SCALE * cam["gain"]) * cam["pregain"]).astype(np.float32)
    v = (cfa - black[cmap]) * k[cmap]                     # the processor's 12-bit numbers
    np.clip(v, IDC_V12_MIN, IDC_V12_MAX, out=v)
    where = {int(cmap[dy, dx]): (dy, dx) for dy in (0, 1) for dx in (0, 1)}
    out = np.empty((h, w, IDC_PLANES), dtype=np.float32)

    def colour(c):
        oy, ox = where[c]
        full = _idc_interp(_idc_interp(v[oy::2, ox::2], oy, h, 0), ox, w, 1)
        np.clip(full, IDC_V12_MIN, IDC_V12_MAX, out=full)
        out[..., c] = full / k[c] + black[c]
        return full if c in (1, 3) else None

    ge = (cmap == 1) | (cmap == 3)
    pool = _develop_pool()
    jobs = [pool.submit(colour, c) for c in range(4)]
    kill = pool.submit(_idc_kill, v, ge, st["kill_threshold"]) if st["kill"] else None
    planes = [j.result() for j in jobs]
    if st["luma_detail"]:
        detail = _idc_yhigh(v, ge)
        detail -= np.float32(0.5) * (planes[1] + planes[3])
        out[..., 4] = detail
        del detail
    else:
        out[..., 4] = 0.0
    del planes
    out[..., 5] = kill.result() if kill is not None else 1.0
    return out


def idc_detail_factor(part, scale12):
    """The default rendering's form of IDC's luminance detail: a factor for all
    four colours, (Yhigh + e) / ((G + E) / 2 + e), from a normalised frame strip
    with the IDC planes. scale12: normalised RGBE -> the processor's 12-bit units."""
    ylow = np.float32(0.5) * (part[..., 1] * scale12[1] + part[..., 3] * scale12[3])
    np.maximum(ylow, 0.0, out=ylow)
    r = np.maximum(ylow + part[..., 4], 0.0)
    r += IDC_DETAIL_EPS
    r /= ylow + IDC_DETAIL_EPS
    return np.minimum(r, IDC_DETAIL_MAX, out=r)


def idc_finish_rgbe(img, part, scale12):
    """Balanced RGBE strip `img` with IDC's luminance detail and chroma kill
    (towards the G / E brightness) applied; `part` is the matching frame strip."""
    img = img * idc_detail_factor(part, scale12)[..., None]
    kill = part[..., 5]
    if kill.min() < 1.0:
        lum = np.float32(0.5) * (img[..., 1] + img[..., 3])
        img = lum[..., None] + kill[..., None] * (img - lum[..., None])
    return img


def idc_frame_to_dn(frame, sensor, black):
    """An IDC frame -> plain 4-channel DN with the luminance detail applied (the
    linear DNG, which has nowhere to carry the extra planes)."""
    cam = sensor["camera"]
    black = np.asarray(black, dtype=np.float32)
    white = np.float32(sensor["white"])
    scale12 = (white - black) * np.float32(DSP_INPUT_SCALE * cam["gain"]) * cam["pregain"]
    out = np.empty(frame.shape[:2] + (4,), dtype=np.float32)
    for y in range(0, frame.shape[0], 256):
        part = normalise_rgbe(frame[y:y + 256], black, white)
        r = idc_detail_factor(part, scale12)
        out[y:y + 256] = part[..., :4] * r[..., None] * (white - black) + black
    return out


def black_for(sensor, mode):
    """Black level for the chosen source; a source this file does not have falls
    back to the masked border, then to the fixed value."""
    for key in (mode, "border", "fixed"):
        black = sensor["black"].get(key)
        if black is not None:
            return black
    return FIXED_BLACK_RGBE


# Preview / export resolutions.  key -> (label, linear scale relative to the sensor)
RESOLUTIONS = [
    ("quarter", "25%  (fastest)", 0.25),
    ("half", "50%  (averaged down from the full demosaic)", 0.5),
    ("full", "100%  (every pixel, as a full-size export)", 1.0),
]
RESOLUTION_SCALE = {key: scale for key, _, scale in RESOLUTIONS}


# ---- hot and dead photosites ---------------------------------------------------
# A hot photosite reads far above all eight nearest photosites of its own colour
# while the photosites of the other colours right next to it do not. Real points
# of light (stars, glints) are spread by the lens over photosites of every
# colour, so they are kept. Dead photosites are the same thing, darker.

def remove_hot_pixels(cfa, cmap, black):
    """Copy of the mosaic with hot and dead photosites replaced by the median of
    their same-colour neighbours. Returns (mosaic, number replaced)."""
    h, w = cfa.shape
    h2, w2 = h - h % 2, w - w % 2
    black = np.asarray(black, dtype=np.float32)
    ring = np.ones((3, 3), dtype=bool)
    ring[1, 1] = False
    excess = np.zeros((h, w), dtype=np.float32)
    median = np.zeros((h, w), dtype=np.float32)
    limit = np.zeros((h, w), dtype=np.float32)
    ratio_hi = np.zeros((h, w), dtype=np.float32)
    ratio_lo = np.full((h, w), np.inf, dtype=np.float32)
    for dy in (0, 1):
        for dx in (0, 1):
            c = int(cmap[dy, dx])
            x = cfa[dy:h2:2, dx:w2:2] - black[c]
            med = median_filter(x, footprint=ring, mode="mirror")
            e = x - med
            sigma = 1.4826 * float(np.median(np.abs(e[::3, ::3]))) + 1.0     # noise, robustly
            excess[dy:h2:2, dx:w2:2] = e
            median[dy:h2:2, dx:w2:2] = med + black[c]
            limit[dy:h2:2, dx:w2:2] = max(8.0 * sigma, 64.0)
            # against the brightest / darkest same-colour neighbour
            ratio_hi[dy:h2:2, dx:w2:2] = x / np.maximum(maximum_filter(x, footprint=ring, mode="mirror"), 16.0)
            ratio_lo[dy:h2:2, dx:w2:2] = np.maximum(x, 1.0) / np.maximum(
                minimum_filter(x, footprint=ring, mode="mirror"), 1.0)
    nb_hi = maximum_filter(excess, footprint=ring, mode="mirror")      # the other colours, next door
    nb_lo = minimum_filter(excess, footprint=ring, mode="mirror")
    hot = ((excess > limit) & (ratio_hi > 3.0)
           & (excess > 4.0 * np.maximum(nb_hi, 0.0) + 0.25 * limit))
    dead = ((excess < -limit) & (ratio_lo < 1.0 / 3.0)
            & (-excess > 4.0 * np.maximum(-nb_lo, 0.0) + 0.25 * limit))
    bad = hot | dead
    out = cfa.copy()
    out[bad] = median[bad]
    return out, int(bad.sum())


def sensor_mosaic(sensor, hot_pixels=False):
    """The mosaic the demosaic works from: as recorded, or with hot pixels removed
    (worked out once per file)."""
    if not hot_pixels:
        return sensor["cfa"]
    cached = sensor.get("cfa_clean")
    if cached is None:
        cached, n = remove_hot_pixels(sensor["cfa"], sensor["cmap"], black_for(sensor, "camera"))
        sensor["cfa_clean"], sensor["hot_pixels_found"] = cached, n
    return cached


# ---- lateral chromatic aberration ----------------------------------------------
# The lens images red and blue at slightly different sizes from green, which
# shows as colour fringes that grow towards the corners. Each plane is scaled
# about the frame centre by (1 + amount / 10000). Emerald sits between green and
# blue in wavelength, so it moves by part of the blue amount.

CA_EMERALD_SHARE = 0.375


def _cubic_taps(n, s):
    """Source indices and weights (Keys cubic, a = -0.5) that resample a line of
    n samples scaled by s about its centre: output i reads input c + s * (i - c).
    Positions past the ends take the edge sample."""
    c = (n - 1) / 2.0
    x = c + s * (np.arange(n, dtype=np.float64) - c)
    i0 = np.floor(x).astype(np.int64)
    k = np.arange(-1, 3)[:, None]                        # taps at i0-1 .. i0+2
    d = np.abs((x - i0)[None, :] - k)
    a = -0.5
    wgt = np.where(d <= 1.0, ((a + 2.0) * d - (a + 3.0)) * d * d + 1.0,
                   np.where(d < 2.0, ((a * d - 5.0 * a) * d + 8.0 * a) * d - 4.0 * a, 0.0))
    return np.clip(i0[None, :] + k, 0, n - 1), wgt.astype(np.float32)


def scale_plane(plane, s, strip=256):
    """One image plane magnified by s about its centre (cubic), on the develop threads."""
    h, w = plane.shape
    ix, wx = _cubic_taps(w, s)
    iy, wy = _cubic_taps(h, s)

    def one(y0):
        y1 = min(h, y0 + strip)
        rows = iy[:, y0:y1]
        r0, r1 = int(rows.min()), int(rows.max()) + 1
        part = plane[r0:r1]
        across = part[:, ix[0]] * wx[0]
        for t in (1, 2, 3):
            across += part[:, ix[t]] * wx[t]
        res = across[rows[0] - r0] * wy[0, y0:y1, None]
        for t in (1, 2, 3):
            res += across[rows[t] - r0] * wy[t, y0:y1, None]
        return res

    return np.concatenate(list(_develop_pool().map(one, range(0, h, strip))), axis=0)


def correct_lateral_ca(dn, ca_red, ca_blue):
    """RGBE frame (any units) with the red, blue and emerald planes rescaled. In place."""
    for ch, k in ((0, ca_red), (2, ca_blue), (3, CA_EMERALD_SHARE * ca_blue)):
        if abs(k) >= 1e-3:
            dn[..., ch] = scale_plane(dn[..., ch], 1.0 + k / 10000.0)
    return dn


def estimate_lateral_ca(sensor):
    """(red, blue) lateral-CA amounts (1/10000 of the radius) measured from the
    image itself: local shifts of red and blue against green across the frame,
    fitted with one magnification each. None when the picture has too little detail."""
    sp = cfa_to_superpixel(sensor["cfa"], sensor["cmap"])
    black = np.asarray(black_for(sensor, "camera"), dtype=np.float32)
    sp = np.maximum(sp - black, 0.0)
    h, w = sp.shape[:2]
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    hp = lambda z: gaussian_filter(z, 1.0) - gaussian_filter(z, 4.0)     # detail only, no shading
    g = hp(np.sqrt(sp[..., 1]))
    gy_, gx_ = np.gradient(g)
    tile = 64
    result = []
    for ch in (0, 2):
        c = hp(np.sqrt(sp[..., ch]))
        rows = []
        for y0 in range(tile, h - 2 * tile, tile):
            for x0 in range(tile, w - 2 * tile, tile):
                sl = (slice(y0, y0 + tile), slice(x0, x0 + tile))
                a, b = g[sl], c[sl]
                sa, sb = a.std(), b.std()
                if sa < 1e-3 or sb < 1e-3:
                    continue
                b = b * (sa / sb)
                ix, iy, it = gx_[sl], gy_[sl], b - a
                sxx, syy, sxy = (ix * ix).sum(), (iy * iy).sum(), (ix * iy).sum()
                det = sxx * syy - sxy * sxy
                lam = 0.5 * (sxx + syy - math.sqrt(max((sxx - syy) ** 2 + 4 * sxy * sxy, 0.0)))
                if det <= 1e-9 or lam < 0.05 * tile * tile * sa * sa:
                    continue
                bx, by = (ix * it).sum(), (iy * it).sum()
                dx = -(syy * bx - sxy * by) / det             # how far this colour sits from green
                dy = -(sxx * by - sxy * bx) / det
                if abs(dx) > 3 or abs(dy) > 3:
                    continue
                rows.append((x0 + tile / 2 - cx, y0 + tile / 2 - cy, dx, dy, lam))
        if len(rows) < 12:
            return None
        r = np.array(rows)
        wgt = np.sqrt(r[:, 4])
        keep = np.ones(len(r), dtype=bool)
        for _ in range(3):                                   # fit d = m * (position - centre) + shift
            A = np.zeros((2 * keep.sum(), 3))
            A[0::2, 0], A[0::2, 1] = r[keep, 0], 1.0
            A[1::2, 0], A[1::2, 2] = r[keep, 1], 1.0
            d = np.empty(2 * keep.sum())
            d[0::2], d[1::2] = r[keep, 2], r[keep, 3]
            ww = np.repeat(wgt[keep], 2)
            sol = np.linalg.lstsq(A * ww[:, None], d * ww, rcond=None)[0]
            res = np.hypot(r[:, 2] - (sol[0] * r[:, 0] + sol[1]), r[:, 3] - (sol[0] * r[:, 1] + sol[2]))
            keep = res < max(3.0 * np.median(res[keep]), 0.05)
        result.append(float(np.clip(sol[0] * 10000.0, -60.0, 60.0)))
    return tuple(result)


def demosaic_full(cfa, sensor, mode):
    """Full-resolution frame in DN from a mosaic with the chosen demosaic: RGBE,
    and for "idc" IDC's two extra planes after them (see demosaic_idc_frame).
    A file without camera registers gets the edge-aware demosaic for "idc"."""
    if mode == "idc" and sensor.get("camera") is not None:
        return demosaic_idc_frame(cfa, sensor)
    if mode in ("guided", "idc"):
        return demosaic_guided(cfa, sensor["cmap"], float(np.mean(sensor["black"]["fixed"])),
                               sensor["white"])
    return demosaic_bilinear(cfa, sensor["cmap"])


def sensor_rgbe_dn(sensor, level, demosaic="bilinear", hot_pixels=False, ca=(0.0, 0.0)):
    """4-channel RGBE image in raw DN (black NOT subtracted) at the given level
    (6 planes for IDC's demosaic at "full": see demosaic_idc_frame).
    Black subtraction is per-channel and every step here is per-channel linear,
    so subtracting afterwards gives the same numbers as v15's build_rgbe().
    "full" runs the chosen demosaic; "half" and "quarter" are 2x2 superpixels
    (no interpolation), averaged once more for "quarter". Lateral-CA correction
    applies to "full" only (the preview averages the full frame down instead)."""
    cfa = sensor_mosaic(sensor, hot_pixels)
    if level == "full":
        dn = demosaic_full(cfa, sensor, demosaic)
        if ca and (abs(ca[0]) >= 1e-3 or abs(ca[1]) >= 1e-3):
            dn = correct_lateral_ca(dn, ca[0], ca[1])
        return dn
    sp = cfa_to_superpixel(cfa, sensor["cmap"])
    if level == "half":
        return sp
    return block_mean(sp, 2).astype(np.float32)


def frame_from_full(full_dn, level):
    """A 50 % / 25 % frame averaged down from the full-resolution demosaic, so
    every preview size shows the demosaic (and CA correction) the export uses."""
    k = {"full": 1, "half": 2, "quarter": 4}[level]
    return full_dn if k == 1 else block_mean(full_dn, k).astype(np.float32)


def normalise_rgbe(dn, black, white):
    """Black-subtracted, normalised 4-channel image. No lower clip anywhere.
    Planes after the fourth (IDC's demosaic) are passed through unchanged."""
    black = np.asarray(black, dtype=np.float32)
    if dn.shape[-1] == 4:
        return (dn - black) / (np.float32(white) - black)
    out = np.empty(dn.shape, dtype=np.float32)
    out[..., :4] = (dn[..., :4] - black) / (np.float32(white) - black)
    out[..., 4:] = dn[..., 4:]
    return out


# ==========================================
# --- PER-IMAGE EDIT SETTINGS ---
# ==========================================

@dataclass
class Edit:
    black_mode: str = "camera"
    wb_preset: str = "asshot"
    temp: Optional[float] = None        # None = not resolved for this file yet
    tint: Optional[float] = None
    exposure: float = 0.0               # EV
    black_point: int = 0                # 0..500  (x 1/10000 of full scale)
    rolloff: bool = True
    highlights: bool = False            # rebuild clipped channels from the ones that did not clip
    ir: bool = False
    render: str = "default"             # "default" (v17 rendering) | "sony" (Image Data Converter colour)
    sony_curve: str = "real"            # "real" | "standard": which of the camera's tone curves
    demosaic: str = "idc"               # "idc" | "guided" | "bilinear"  (preview and full-size export)
    hot_pixels: bool = False            # replace hot / dead photosites before the demosaic
    ca_red: float = 0.0                 # lateral chromatic aberration: red plane scale, 1/10000
    ca_blue: float = 0.0                # ... blue plane scale (emerald follows by a share)
    chroma_nr: int = 0                  # colour noise reduction, 0..100
    defringe: int = 0                   # purple / green fringe removal, 0..100
    rot_k: Optional[int] = None         # clockwise quarter turns; None = use camera orientation
    flip: bool = False                  # mirror left-right (applied before the turns)
    straighten: float = 0.0             # degrees, positive = clockwise
    crop: tuple = (0.0, 0.0, 1.0, 1.0)  # x0, y0, x1, y1 as fractions of the oriented frame

    def copy(self):
        return replace(self)


SYNC_GROUPS = [
    ("Colour rendering (default / Sony)", ("render", "sony_curve")),
    ("White balance", ("wb_preset", "temp", "tint")),
    ("Exposure, black point, highlights", ("exposure", "black_point", "rolloff", "highlights")),
    ("IR false colour", ("ir",)),
    ("Black level source, demosaic, hot pixels", ("black_mode", "demosaic", "hot_pixels")),
    ("Lens: chromatic aberration, fringes", ("ca_red", "ca_blue", "defringe")),
    ("Colour noise reduction", ("chroma_nr",)),
    ("Crop, rotation, straighten", ("rot_k", "flip", "straighten", "crop")),
]


def sync_edit(source, target, groups):
    """Copy the named groups from source into a copy of target. Per-file white
    balance modes (as shot / auto) are left unresolved so each file gets its own."""
    out = target.copy()
    for label, fields in SYNC_GROUPS:
        if label in groups:
            for f in fields:
                setattr(out, f, getattr(source, f))
    if "White balance" in groups and source.wb_preset != "custom":
        out.temp = out.tint = None
    return out


def resolve_edit(edit, sensor, proxy_dn=None):
    """Fill in the per-file values (camera orientation, as-shot / auto WB) in place."""
    if edit.rot_k is None:
        edit.rot_k = int(sensor.get("quarter_turns", 0))
    if edit.temp is None or edit.tint is None:
        tt = None
        if edit.wb_preset == "auto":
            if proxy_dn is None:
                proxy_dn = sensor_rgbe_dn(sensor, "quarter")
            tt = auto_white_balance(normalise_rgbe(
                proxy_dn, black_for(sensor, edit.black_mode), sensor["white"]))
        elif edit.wb_preset == "asshot":
            tt = sensor_asshot(sensor)
        elif uses_idc_preset(edit, sensor):
            tt = dsp_temp_tint(dict(sensor["camera"], wb=idc_preset_wb(edit.wb_preset)))
            tt = tt or WB_PRESET_VALUES[edit.wb_preset]
        elif WB_PRESET_VALUES.get(edit.wb_preset):
            tt = WB_PRESET_VALUES[edit.wb_preset]
        edit.temp, edit.tint = tt if tt else D65_TEMP_TINT
    return edit


# ==========================================
# --- DEVELOP (the v15 render, strip by strip) ---
# ==========================================

# ---- highlight reconstruction --------------------------------------------------
# A sensor channel that reaches the white level stops recording, but the others
# usually have not: for neutral light green clips first and red last, more than a
# stop later.  Without reconstruction every white-balanced channel is cut at 1.0
# (v15 behaviour: clean, neutral, but everything above is lost).  With it, the
# clipped channels are rebuilt from the surviving ones using the colour of the
# nearest unclipped surroundings, and nothing is cut at 1.0, so the roll-off (or
# a negative exposure) has real detail to work with.

HL_KNEE, HL_CLIP = 0.90, 0.98      # fraction of the white level where a channel starts / is fully distrusted
HL_MAX_GAIN = 4.0                  # a rebuilt channel may reach at most this multiple of its clipped value


def reconstruct_highlights(rgbe, wb):
    """rgbe: black-subtracted, normalised, UN-balanced camera data (1.0 = white
    level). Returns the white-balanced image with clipped channels rebuilt and
    the top left unclipped. A channel is only ever raised, never lowered."""
    wb = np.asarray(wb, dtype=np.float32)
    bal = rgbe * wb
    peak = rgbe.max(axis=2)
    hit = peak > HL_KNEE
    if not hit.any():
        return bal
    h, w = peak.shape

    # 1. Colour (channel / channel-average) of trustworthy, reasonably bright
    #    pixels, spread into the clipped areas on a small grid. Far from any
    #    good pixel it fades to neutral.
    mean = bal.mean(axis=2)
    trust = np.clip((HL_CLIP - peak) / (HL_CLIP - HL_KNEE), 0.0, 1.0) * np.clip(mean / 0.25, 0.0, 1.0)
    colour = bal / np.maximum(mean, 1e-4)[..., None] * trust[..., None]
    k = max(1, min(h, w) // 256)
    hh, ww = (h // k) * k, (w // k) * k
    num = colour[:hh, :ww].reshape(hh // k, k, ww // k, k, 4).mean(axis=(1, 3))
    den = trust[:hh, :ww].reshape(hh // k, k, ww // k, k).mean(axis=(1, 3))
    del colour, trust, mean
    sigma = max(1.5, 0.02 * min(num.shape[:2]))
    num = gaussian_filter(num, (sigma, sigma, 0), mode="nearest")
    den = gaussian_filter(den, sigma, mode="nearest")
    prior = 0.02
    local = (num + prior) / (den[..., None] + prior)

    # 2. For every pixel with a clipped channel: fit the brightness to the
    #    channels that can still be trusted, rebuild the others from the colour.
    ys, xs = np.nonzero(hit)
    at = np.array([(ys + 0.5) / k - 0.5, (xs + 0.5) / k - 0.5])
    q = np.stack([map_coordinates(local[..., c], at, order=1, mode="nearest") for c in range(4)], axis=1)
    q = np.maximum(q, 1e-3).astype(np.float32)
    b = bal[ys, xs]
    gone = np.clip((rgbe[ys, xs] - HL_KNEE) / (HL_CLIP - HL_KNEE), 0.0, 1.0)     # 0 = trusted, 1 = clipped
    kept = 1.0 - gone
    fit = (kept * b * q).sum(axis=1) / np.maximum((kept * q * q).sum(axis=1), 1e-6)   # least-squares brightness
    # How far to believe the borrowed colour: it needs at least two surviving
    # channels to be checked against, and they have to agree with it. Otherwise
    # lean towards neutral, which is what bright highlights mostly are.
    miss = np.sqrt((kept * (b - fit[:, None] * q) ** 2).sum(axis=1)) / np.maximum((kept * b).sum(axis=1), 1e-6)
    belief = np.clip((kept.sum(axis=1) - 1.0) / 1.5, 0.0, 1.0) * np.exp(-(miss / 0.08) ** 2)
    q = 1.0 + belief[:, None] * (q - 1.0)
    fit = (kept * b * q).sum(axis=1) / np.maximum((kept * q * q).sum(axis=1), 1e-6)
    rebuilt = np.clip(fit[:, None] * q, b, HL_MAX_GAIN * b)          # never lower a channel, never invent > 2 stops
    # When nothing at all survives there is no brightness to fit: plain white at
    # the level of the last channel to clip.
    some = np.clip(kept.sum(axis=1) / 0.5, 0.0, 1.0)[:, None]
    rebuilt = some * rebuilt + (1.0 - some) * b.max(axis=1, keepdims=True)
    bal[ys, xs] = b + gone * (rebuilt - b)
    return bal


def dsp_input_scale(sensor, edit):
    """Per channel: normalised RGBE -> the camera processor's 12-bit units, with
    this edit's black level. None for a file without camera registers."""
    cam = sensor.get("camera")
    if cam is None:
        return None
    black = np.asarray(black_for(sensor, edit.black_mode), dtype=np.float32)
    scale = (np.float32(sensor["white"]) - black) * np.float32(DSP_INPUT_SCALE * cam["gain"]) * cam["pregain"]
    return scale.astype(np.float32)


def sony_dsp_setup(sensor, edit):
    """What develop() needs for the exact Sony rendering of this image, or None
    when the image is not rendered that way (or the file has no camera registers):
    (camera registers, white balance after the matrix, scale from normalised
    RGBE to the processor's 12-bit units)."""
    cam = sensor.get("camera")
    if cam is None or getattr(edit, "render", "default") != "sony" or edit.ir:
        return None
    if edit.wb_preset == "asshot":
        wb_post = cam["wb"]
    elif uses_idc_preset(edit, sensor):
        wb_post = idc_preset_wb(edit.wb_preset)
    else:
        wb_post = dsp_white_balance(wb_from_temp_tint(edit.temp, edit.tint), cam)
    return cam, wb_post, dsp_input_scale(sensor, edit)


_DEVELOP_POOL = None


def _develop_pool():
    """Worker threads for develop(): numpy releases the GIL in the heavy steps, so
    strips run in parallel on all cores."""
    global _DEVELOP_POOL
    if _DEVELOP_POOL is None:
        _DEVELOP_POOL = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(8, os.cpu_count() or 2)), thread_name_prefix="develop")
    return _DEVELOP_POOL


def develop(rgbe, edit, wb, cam_to_rgb, strip=256, dsp=None, oog=None, parallel=True, scale12=None,
            exact=False):
    """rgbe: (h, w, 4) linear camera data, black-subtracted, negatives allowed.
    Returns LINEAR float32 RGB in [0, 1] in the output space of `cam_to_rgb`.
    `dsp` (from sony_dsp_setup) switches the Sony rendering to the exact camera
    colour processor; without it the Sony rendering is v18's approximation.
    `cam_to_rgb` comes from output_matrix(edit, space): 3x4 camera -> RGB for the
    default rendering, 3x3 linear sRGB -> RGB for the Sony rendering.
    With highlight reconstruction off and the default rendering this is the
    identical sequence of operations, in the identical order, to v15's render().
    `oog`: optional (h, w) bool array, set where the colour lies outside the
    output space (default rendering only). Strips run on several threads.
    A frame from IDC's demosaic carries two planes after R G B E; `scale12`
    (dsp_input_scale) puts them in proportion for the default rendering.
    `exact`: the Sony rendering in IDC's own arithmetic (render_sony_exact), for a
    whole full-resolution sensor frame (not cropped, turned or binned)."""
    h, w, planes = rgbe.shape
    idc = planes >= IDC_PLANES
    if idc and scale12 is None:
        scale12 = dsp[2] if dsp is not None else np.full(4, 4000.0, dtype=np.float32)
    rebuilt = reconstruct_highlights(rgbe[..., :4], wb) if edit.highlights else None
    out = np.empty((h, w, 3), dtype=np.float32)
    wb = np.asarray(wb, dtype=np.float32)
    m_t = np.ascontiguousarray(cam_to_rgb.T, dtype=np.float32)
    gain = np.float32(2.0 ** edit.exposure)
    bp = np.float32(edit.black_point / 10000.0)
    sony = getattr(edit, "render", "default") == "sony" and not edit.ir

    def finish_sony(y, n, srgb):
        """Linear sRGB from the Sony rendering -> the output space, black point, clip."""
        if m_t.shape == (3, 3) and not np.allclose(m_t, np.eye(3)):
            lin = (srgb.reshape(-1, 3) @ m_t).reshape(n, w, 3)
        else:                                        #    sRGB output (or a 3x4 matrix passed by mistake)
            lin = srgb
        if bp:                                       #    neutral black point, on the finished picture
            lin = (lin - bp) / (np.float32(1.0) - bp)
        np.clip(lin, 0.0, 1.0, out=lin)              #    (no roll-off: Sony's curve has its own shoulder)
        out[y:y + n] = lin

    exact = exact and sony and dsp is not None and dsp[0].get("stage") is not None

    def sony_input(a, b):
        """Rows a..b of the frame as the camera processor's input: un-balanced RGBE in its
        12-bit units with the exposure, and IDC's luminance detail and chroma kill."""
        cam, wb_post, scale = dsp
        part = rgbe[a:b]
        raw12 = part[..., :4] * scale if rebuilt is None else rebuilt[a:b] / wb * scale
        if edit.exposure:
            raw12 *= gain
        detail = kill = None
        if idc:
            detail = part[..., 4] * gain if edit.exposure else part[..., 4]
            kill = part[..., 5]
        return raw12, detail, kill

    def one(y):
        part = rgbe[y:y + strip]
        if exact:                                    # 3-6 (Sony, bit for bit): whole rows, 2 rows of context
            n = part.shape[0]
            a, b = max(0, y - 2), min(h, y + n + 2)
            raw12, detail, kill = sony_input(a, b)
            srgb = render_sony_exact(raw12, dsp[0], dsp[1], edit.sony_curve, detail, kill,
                                     context=(y - a, b - y - n), exposure=edit.exposure)
            del raw12, detail, kill
            finish_sony(y, n, srgb)
            return
        if rebuilt is None:
            img = part[..., :4] * wb                 # 1. white balance (new array)
            np.minimum(img, 1.0, out=img)            # 2. clip the TOP only
        else:
            img = rebuilt[y:y + strip]               # 1+2. balanced, clipped channels rebuilt, top kept
        n = img.shape[0]
        if sony:                                     # 3-6 (Sony): matrix, exposure, Sony tone curve
            if dsp is not None:                      #    the camera's colour processor (floating point)
                raw12, detail, kill = sony_input(y, y + n)
                srgb = render_sony_dsp(raw12, dsp[0], dsp[1], edit.sony_curve, detail, kill, edit.exposure)
                del raw12, detail, kill
            else:
                if idc:
                    img = idc_finish_rgbe(img, part, scale12)
                srgb = render_sony(img, edit.sony_curve, gain)
            finish_sony(y, n, srgb)
            return
        if idc:                                      # 2b. IDC's demosaic: sharp luminance, chroma kill
            img = idc_finish_rgbe(img, part, scale12)
        if edit.ir:                                  # 3a. false colour (original mapping)
            lin = np.empty((n, w, 3), dtype=np.float32)
            lin[..., 0] = img[..., 2]
            lin[..., 1] = 0.5 * (img[..., 1] + img[..., 3])
            lin[..., 2] = img[..., 0]
        else:                                        # 3b. RGBE -> XYZ D50 -> linear RGB
            lin = (img.reshape(-1, 4) @ m_t).reshape(n, w, 3)
        if edit.exposure:                            # 4. exposure
            lin *= gain
        if bp:                                       # 5. neutral black point
            lin = (lin - bp) / (np.float32(1.0) - bp)
        if edit.rolloff:                             # 6. highlight roll-off
            t = np.float32(0.8)
            m = lin > t
            lin[m] = t + (1.0 - t) * (1.0 - np.exp(-(lin[m] - t) / (1.0 - t)))
        if oog is not None and not edit.ir:          #    colours the output space cannot hold
            lo, hi = lin.min(axis=2), lin.max(axis=2)
            oog[y:y + strip] = lo < -np.maximum(0.004, 0.03 * hi)
        np.clip(lin, 0.0, 1.0, out=lin)              # 7. the ONLY lower clip
        out[y:y + strip] = lin

    if parallel:
        list(_develop_pool().map(one, range(0, h, strip)))
    else:
        for y in range(0, h, strip):
            one(y)
    return out


# ---- whole-picture finishing: fringes and colour noise ---------------------------
# These need neighbourhoods, so they run on the finished picture rather than
# strip by strip. Radii are given at full resolution; `scale` shrinks them for
# a smaller preview so the effect looks the same at every size.

def _to_opponent(lin):
    g = np.power(np.clip(lin, 0.0, 1.0), np.float32(1.0 / 2.2))
    y = g @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    return y, g[..., 2] - y, g[..., 0] - y               # luma, blue-difference, red-difference


def _from_opponent(y, cb, cr):
    r = y + cr
    b = y + cb
    g = (y - 0.299 * r - 0.114 * b) / 0.587
    out = np.stack([r, g, b], axis=-1)
    # (the floor keeps x ** 2.2 from underflowing, which is ~30x slower in numpy; 1e-12 ** 2.2 is 0 in effect)
    np.clip(out, 1e-12, 1.0, out=out)
    return np.power(out, np.float32(2.2)).astype(np.float32)


def _guided(guide, sources, r, eps):
    """He et al.'s guided filter: smooths each of `sources` except where `guide`
    has edges. Output depends on input up to 2r rows away."""
    box = lambda z: uniform_filter(z, size=2 * r + 1, mode="mirror")
    mg = box(guide)
    var = box(guide * guide) - mg * mg + eps
    out = []
    for src in sources:
        ms = box(src)
        a = (box(guide * src) - mg * ms) / var
        b = ms - a * mg
        out.append(box(a) * guide + box(b))
    return out


def _strip_map(fn, img, halo, strip=256, parallel=True):
    """fn applied to horizontal strips of img, on the develop threads. Each strip
    carries `halo` extra rows above and below, so a filter that reaches at most
    `halo` rows gives the same result as on the whole picture."""
    h = img.shape[0]
    starts = list(range(0, h, strip))
    if not parallel or len(starts) == 1:
        return fn(img)

    def one(y0):
        y1 = min(h, y0 + strip)
        a, b = max(0, y0 - halo), min(h, y1 + halo)
        return fn(img[a:b])[y0 - a:y1 - a]

    return np.concatenate(list(_develop_pool().map(one, starts)), axis=0)


def reduce_chroma_noise(lin, amount, scale=1.0, parallel=True):
    """Colour noise reduction: the colour-difference planes are smoothed over a
    radius that grows with `amount` (0..100), following the brightness edges so
    colours do not bleed across them. Brightness detail is untouched."""
    a = float(np.clip(amount, 0, 100)) / 100.0
    if a <= 0:
        return lin
    r = max(1, int(round((1.0 + 7.0 * a) * scale)))
    eps = np.float32((0.004 + 0.03 * a) ** 2)
    mix = np.float32(min(1.0, 0.35 + a))

    def run(part):
        y, cb, cr = _to_opponent(part)
        cb2, cr2 = _guided(y, (cb, cr), r, eps)
        return _from_opponent(y, cb + mix * (cb2 - cb), cr + mix * (cr2 - cr))

    return _strip_map(run, lin, 2 * r, parallel=parallel)


def remove_fringes(lin, amount, scale=1.0, parallel=True):
    """Purple (and green) fringe removal: colours in those hue ranges are
    desaturated where they sit within a few pixels of a high-contrast edge,
    which is where the F828's lens and sensor produce them. Blue sky has no red
    in it and is left alone."""
    a = float(np.clip(amount, 0, 100)) / 100.0
    if a <= 0:
        return lin
    half = max(1, int(round(6 * scale)))
    ramp = lambda v, lo, hi: np.clip((v - lo) / (hi - lo), 0.0, 1.0)

    def run(part):
        y, cb, cr = _to_opponent(part)
        size = 2 * half + 1
        contrast = (maximum_filter(y, size=size, mode="nearest")
                    - minimum_filter(y, size=size, mode="nearest"))
        near_edge = ramp(contrast, 0.15, 0.40)
        hue = np.degrees(np.arctan2(cr, cb))             # 0 = blue-violet, 90 = red, -135 = green
        sat = np.hypot(cb, cr)
        purple = ramp(hue, -14.0, -2.0) * ramp(-hue, -80.0, -62.0)     # sky (about -20 to -40) is spared
        green = 0.6 * ramp(hue, -170.0, -150.0) * ramp(-hue, 100.0, 120.0)
        w = (a * near_edge * np.maximum(purple, green) * ramp(sat, 0.04, 0.10)).astype(np.float32)
        keep = np.float32(1.0) - w
        return _from_opponent(y, cb * keep, cr * keep)

    return _strip_map(run, lin, half, parallel=parallel)


def develop_finished(rgbe, edit, wb, cam_to_rgb, dsp=None, scale=1.0, oog=None, parallel=True,
                     scale12=None, exact=False):
    """develop() plus the whole-picture steps: fringe removal, then colour noise
    reduction. `scale` = this picture's size relative to the full-resolution
    frame. `oog`: optional list that receives the out-of-gamut mask. `parallel`
    False keeps everything on the calling thread."""
    mask = np.zeros(rgbe.shape[:2], dtype=bool) if oog is not None else None
    lin = develop(rgbe, edit, wb, cam_to_rgb, dsp=dsp, oog=mask, parallel=parallel, scale12=scale12,
                  exact=exact)
    if oog is not None:
        oog[:] = [mask]
    if getattr(edit, "defringe", 0):
        lin = remove_fringes(lin, edit.defringe, scale, parallel=parallel)
    if getattr(edit, "chroma_nr", 0):
        lin = reduce_chroma_noise(lin, edit.chroma_nr, scale, parallel=parallel)
    return lin


def encode_quantise(lin, space, bits, strip=256):
    """Linear RGB -> transfer curve -> uint8 / uint16."""
    h = lin.shape[0]
    top = 255.0 if bits == 8 else 65535.0
    out = np.empty(lin.shape, dtype=np.uint8 if bits == 8 else np.uint16)
    for y in range(0, h, strip):
        enc = encode_trc(lin[y:y + strip], space)
        out[y:y + strip] = (enc * top + 0.5).astype(out.dtype)
    return out


# ==========================================
# --- GEOMETRY: ORIENTATION, STRAIGHTEN, CROP ---
# ==========================================

def apply_orientation(arr, rot_k, flip):
    """Mirror left-right if `flip`, then turn clockwise `rot_k` quarter turns (views, no copy)."""
    if flip:
        arr = arr[:, ::-1]
    k = int(rot_k or 0) % 4
    return np.rot90(arr, -k) if k else arr


def oriented_size(w, h, rot_k):
    return (h, w) if int(rot_k or 0) % 2 else (w, h)


# How the stored orientation / crop change when the user presses a button.  All
# of these act on what is currently on screen.
def op_rotate(edit, clockwise=True):
    x0, y0, x1, y1 = edit.crop
    k = int(edit.rot_k or 0)
    if clockwise:
        edit.rot_k, edit.crop = (k + 1) % 4, (1.0 - y1, x0, 1.0 - y0, x1)
    else:
        edit.rot_k, edit.crop = (k - 1) % 4, (y0, 1.0 - x1, y1, 1.0 - x0)


def op_flip(edit, horizontal=True):
    x0, y0, x1, y1 = edit.crop
    k = int(edit.rot_k or 0)
    if horizontal:
        edit.rot_k, edit.crop = (-k) % 4, (1.0 - x1, y0, 1.0 - x0, y1)
    else:
        edit.rot_k, edit.crop = (2 - k) % 4, (x0, 1.0 - y1, x1, 1.0 - y0)
    edit.flip = not edit.flip
    edit.straighten = -edit.straighten


def _rot_matrix_rc(angle_deg):
    """Output (row, col) offset from the centre -> source offset, for content
    turned clockwise by angle_deg."""
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def _corners_inside(crop, angle_deg, w, h, scale=1.0):
    x0, y0, x1, y1 = crop
    cx, cy = 0.5 * (x0 + x1) * w, 0.5 * (y0 + y1) * h
    hw, hh = 0.5 * (x1 - x0) * w * scale, 0.5 * (y1 - y0) * h * scale
    m = _rot_matrix_rc(angle_deg)
    eps = 1e-6
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        d = m @ np.array([cy + sy * hh - h / 2.0, cx + sx * hw - w / 2.0])
        r, c = d[0] + h / 2.0, d[1] + w / 2.0
        if r < -eps or r > h + eps or c < -eps or c > w + eps:
            return False
    return True


def normalise_crop(crop):
    x0, y0, x1, y1 = (min(max(float(v), 0.0), 1.0) for v in crop)
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    if x1 - x0 < 0.02:
        x0, x1 = max(0.0, x0 - 0.01), min(1.0, x0 + 0.01)
    if y1 - y0 < 0.02:
        y0, y1 = max(0.0, y0 - 0.01), min(1.0, y0 + 0.01)
    return (x0, y0, x1, y1)


def effective_crop(crop, angle_deg, w, h):
    """The crop actually used: the requested one, shrunk about its centre just
    enough that a straightened image never shows empty corners."""
    crop = normalise_crop(crop)
    if abs(angle_deg) < 1e-3 or _corners_inside(crop, angle_deg, w, h):
        return crop
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        if _corners_inside(crop, angle_deg, w, h, mid):
            lo = mid
        else:
            hi = mid
    if lo < 0.05 and crop != (0.0, 0.0, 1.0, 1.0):
        return effective_crop((0.0, 0.0, 1.0, 1.0), angle_deg, w, h)
    x0, y0, x1, y1 = crop
    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    hw, hh = 0.5 * (x1 - x0) * lo, 0.5 * (y1 - y0) * lo
    return (cx - hw, cy - hh, cx + hw, cy + hh)


def crop_to_pixels(crop, w, h):
    x0, y0, x1, y1 = normalise_crop(crop)
    px0, py0 = int(round(x0 * w)), int(round(y0 * h))
    px1, py1 = int(round(x1 * w)), int(round(y1 * h))
    px0, py0 = min(px0, w - 2), min(py0, h - 2)
    return px0, py0, max(px1, px0 + 2), max(py1, py0 + 2)


def fit_aspect(crop, ratio, w, h):
    """Largest rectangle of the given width:height ratio (in pixels) that fits
    inside `crop`, sharing its centre."""
    x0, y0, x1, y1 = normalise_crop(crop)
    bw, bh = (x1 - x0) * w, (y1 - y0) * h
    if bw / bh > ratio:
        bw = bh * ratio
    else:
        bh = bw / ratio
    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    return (cx - 0.5 * bw / w, cy - 0.5 * bh / h, cx + 0.5 * bw / w, cy + 0.5 * bh / h)


def constrain_aspect(rect, handle, k):
    """Force a crop rectangle being dragged by `handle` ('l','r','t','b' or a
    corner such as 'tl') to the shape  height = k * width  (both as fractions of
    the frame), keeping the opposite edge / corner fixed and staying inside 0..1."""
    x0, y0, x1, y1 = rect
    if handle in ("l", "r"):
        cy = 0.5 * (y0 + y1)
        nw = x1 - x0
        nh = min(nw * k, 2.0 * min(cy, 1.0 - cy))
        nw = nh / k
        x0, x1 = (x1 - nw, x1) if handle == "l" else (x0, x0 + nw)
        return (x0, cy - nh / 2.0, x1, cy + nh / 2.0)
    if handle in ("t", "b"):
        cx = 0.5 * (x0 + x1)
        nh = y1 - y0
        nw = min(nh / k, 2.0 * min(cx, 1.0 - cx))
        nh = nw * k
        y0, y1 = (y1 - nh, y1) if handle == "t" else (y0, y0 + nh)
        return (cx - nw / 2.0, y0, cx + nw / 2.0, y1)
    room_w = x1 if "l" in handle else 1.0 - x0
    room_h = y1 if "t" in handle else 1.0 - y0
    nw = min(max(x1 - x0, (y1 - y0) / k), room_w, room_h / k)
    nh = nw * k
    x0, x1 = (x1 - nw, x1) if "l" in handle else (x0, x0 + nw)
    y0, y1 = (y1 - nh, y1) if "t" in handle else (y0, y0 + nh)
    return (x0, y0, x1, y1)


def frame_point_to_source(fx, fy, angle_deg, w, h):
    """A point given as fractions of the straightened frame -> (column, row) in
    the un-straightened image of size w x h (used by the white-balance picker)."""
    centre = np.array([(h - 1) / 2.0, (w - 1) / 2.0])
    r, c = _rot_matrix_rc(angle_deg) @ (np.array([fy * h - 0.5, fx * w - 0.5]) - centre) + centre
    return float(c), float(r)


def rotate_crop(img, angle_deg, rect, order=1, edge="nearest"):
    """Turn the content of `img` (h, w, c) clockwise by angle_deg about the frame
    centre and return only the pixel rectangle rect = (x0, y0, x1, y1).
    edge="nearest" repeats the border for the half-pixel overshoot a valid crop
    can have; edge="constant" leaves everything outside the frame black."""
    x0, y0, x1, y1 = rect
    if abs(angle_deg) < 1e-3:
        return img[y0:y1, x0:x1]
    if img.ndim == 2:                                  # a single plane (e.g. a mask)
        return rotate_crop(img[..., None], angle_deg, rect, order, edge)[..., 0]
    h, w = img.shape[:2]
    m = _rot_matrix_rc(angle_deg)
    centre = np.array([(h - 1) / 2.0, (w - 1) / 2.0])
    offset = m @ (np.array([y0, x0], dtype=np.float64) - centre) + centre
    out = np.empty((y1 - y0, x1 - x0, img.shape[2]), dtype=img.dtype)
    for c in range(img.shape[2]):
        affine_transform(img[..., c], m, offset=offset, output_shape=out.shape[:2],
                         output=out[..., c], order=order, mode=edge, cval=0)
    return out


_RESAMPLE_FILTERS = {"lanczos": Image.Resampling.LANCZOS, "bicubic": Image.Resampling.BICUBIC,
                     "area": Image.Resampling.BOX, "bilinear": Image.Resampling.BILINEAR}


def resize_linear(lin, new_w, new_h, method="lanczos"):
    """Anti-aliased resize of float RGB, done in linear light. method: lanczos
    (sharpest), bicubic, area (plain averaging: softest, never rings)."""
    flt = _RESAMPLE_FILTERS.get(method, Image.Resampling.LANCZOS)
    out = np.empty((new_h, new_w, lin.shape[2]), dtype=np.float32)
    for c in range(lin.shape[2]):
        ch = Image.fromarray(np.ascontiguousarray(lin[..., c], dtype=np.float32))     # 32-bit float image
        out[..., c] = np.asarray(ch.resize((new_w, new_h), flt))
    np.clip(out, 0.0, 1.0, out=out)
    return out


# ==========================================
# --- METADATA: READ EXIF FROM THE .SRF ---
# ==========================================
# An .SRF is a TIFF container, so the standard IFD0 / EXIF / GPS directories
# can be read directly.  Values are kept with their TIFF type so they can be
# written back losslessly.

_TIFF_FMT = {1: ("B", 1), 2: ("c", 1), 3: ("H", 2), 4: ("I", 4), 5: ("I", 8), 6: ("b", 1),
             7: ("B", 1), 8: ("h", 2), 9: ("i", 4), 10: ("i", 8), 11: ("f", 4), 12: ("d", 8),
             13: ("I", 4)}
_TAG_EXIF_IFD, _TAG_GPS_IFD = 34665, 34853
_MAX_TAG_BYTES = 65536


def _decode_value(typ, count, data, bo):
    if typ in (1, 2, 6, 7):
        return bytes(data[:count])
    fmt, _ = _TIFF_FMT[typ]
    if typ in (5, 10):
        flat = struct.unpack(bo + fmt * (2 * count), data[:8 * count])
        return tuple((flat[i], flat[i + 1]) for i in range(0, len(flat), 2))
    return struct.unpack(bo + fmt * count, data[:struct.calcsize(fmt) * count])


def _read_ifd(f, bo, offset, file_size, max_bytes=_MAX_TAG_BYTES):
    """-> {tag: (type, value)} for one TIFF directory. Oversized or broken entries are skipped."""
    out = {}
    if offset <= 0 or offset + 2 > file_size:
        return out
    f.seek(offset)
    (n,) = struct.unpack(bo + "H", f.read(2))
    raw = f.read(12 * n)
    for i in range(len(raw) // 12):
        tag, typ, count = struct.unpack(bo + "HHI", raw[12 * i:12 * i + 8])
        if typ not in _TIFF_FMT or count == 0:
            continue
        size = _TIFF_FMT[typ][1] * count
        if size > max_bytes:
            continue
        if size <= 4:
            data = raw[12 * i + 8:12 * i + 12]
        else:
            (pos,) = struct.unpack(bo + "I", raw[12 * i + 8:12 * i + 12])
            if pos + size > file_size:
                continue
            f.seek(pos)
            data = f.read(size)
        try:
            out[tag] = (typ, _decode_value(typ, count, data, bo))
        except struct.error:
            continue
    return out


def read_metadata(path):
    """-> {"ifd0": {...}, "exif": {...}, "gps": {...}}; empty dicts if nothing readable."""
    meta = {"ifd0": {}, "exif": {}, "gps": {}}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(8)
            bo = {b"II": "<", b"MM": ">"}.get(head[:2])
            if bo is None or struct.unpack(bo + "H", head[2:4])[0] != 42:
                return meta
            meta["ifd0"] = _read_ifd(f, bo, struct.unpack(bo + "I", head[4:8])[0], size)
            for key, tag in (("exif", _TAG_EXIF_IFD), ("gps", _TAG_GPS_IFD)):
                ptr = meta["ifd0"].get(tag)
                if ptr and ptr[0] in (4, 13) and ptr[1]:
                    meta[key] = _read_ifd(f, bo, int(ptr[1][0]), size)
    except (OSError, struct.error, IndexError):
        pass
    return meta


def _ascii(entry):
    if not entry or entry[0] != 2:
        return ""
    return entry[1].split(b"\0")[0].decode("latin-1", "replace").strip()


def _ratio(entry):
    try:
        if entry[0] in (5, 10):
            n, d = entry[1][0]
            return n / d if d else None
        return float(entry[1][0])
    except (TypeError, IndexError, ValueError):
        return None


def describe_metadata(meta):
    """Human-readable (label, value) pairs for the Info panel."""
    ifd0, exif = meta.get("ifd0", {}), meta.get("exif", {})
    rows = []
    camera = " ".join(x for x in (_ascii(ifd0.get(271)), _ascii(ifd0.get(272))) if x)
    if camera:
        rows.append(("Camera", camera))
    date = _ascii(exif.get(36867)) or _ascii(ifd0.get(306))
    if date:
        rows.append(("Taken", date))
    t = _ratio(exif.get(33434)) if exif.get(33434) else None
    if t:
        rows.append(("Shutter", f"1/{round(1.0 / t)} s" if t < 0.5 else f"{t:.1f} s"))
    fnum = _ratio(exif.get(33437)) if exif.get(33437) else None
    if fnum:
        rows.append(("Aperture", f"f/{fnum:.1f}"))
    iso = exif.get(34855)
    if iso and iso[0] in (3, 4) and iso[1]:
        rows.append(("ISO", str(iso[1][0])))
    fl = _ratio(exif.get(37386)) if exif.get(37386) else None
    if fl:
        rows.append(("Focal length", f"{fl:.1f} mm"))
    return rows


# ==========================================
# --- METADATA: WRITE EXIF INTO EXPORTS ---
# ==========================================

# IFD0 tags worth carrying over.  Everything structural (sizes, strips,
# compression ...) describes the raw container, not the exported picture.
_IFD0_KEEP = (270, 271, 272, 282, 283, 296, 306, 315, 33432)
# EXIF tags that would be wrong in the export: MakerNote (its internal offsets
# point into the .SRF), the interoperability pointer, compressed-bits-per-pixel.
_EXIF_DROP = (37500, 40965, 37122)


def output_metadata(meta, width, height, space, copy=True, gps=True, dpi=0):
    """Metadata for an exported file: the camera's EXIF/GPS plus corrected
    dimensions, orientation, colour-space flag and software name.
    gps=False leaves the location out; dpi > 0 sets the print resolution."""
    out = {"ifd0": {}, "exif": {}, "gps": {}}
    if copy and meta:
        out["ifd0"] = {t: v for t, v in meta.get("ifd0", {}).items() if t in _IFD0_KEEP}
        out["exif"] = {t: v for t, v in meta.get("exif", {}).items() if t not in _EXIF_DROP}
        if gps:
            out["gps"] = dict(meta.get("gps", {}))
    out["ifd0"][274] = (3, (1,))                                   # pixels are already upright
    out["ifd0"][305] = (2, SOFTWARE_TAG.encode("ascii") + b"\0")
    if dpi:
        out["ifd0"][282] = out["ifd0"][283] = (5, ((int(dpi), 1),))
        out["ifd0"][296] = (3, (2,))
    out["ifd0"].setdefault(282, (5, ((300, 1),)))
    out["ifd0"].setdefault(283, (5, ((300, 1),)))
    out["ifd0"].setdefault(296, (3, (2,)))
    out["exif"].setdefault(36864, (7, b"0230"))                    # ExifVersion
    out["exif"][40961] = (3, (1 if space == "srgb" else 65535,))   # ColorSpace
    out["exif"][40962] = (4, (int(width),))                        # PixelXDimension
    out["exif"][40963] = (4, (int(height),))                       # PixelYDimension
    return out


def _encode_value(typ, value, bo):
    if typ in (1, 2, 6, 7):
        data = bytes(value)
        return len(data), data
    fmt, _ = _TIFF_FMT[typ]
    if typ in (5, 10):
        flat = [int(x) for pair in value for x in pair]
        return len(value), struct.pack(bo + fmt * len(flat), *flat)
    cast = float if typ in (11, 12) else int
    return len(value), struct.pack(bo + fmt * len(value), *[cast(x) for x in value])


def serialise_ifd(entries, base, bo="<"):
    """entries {tag: (type, value)} -> bytes of one TIFF directory that will sit
    at absolute file offset `base` (out-of-line values follow it directly)."""
    tags = sorted(entries)
    data_start = base + 2 + 12 * len(tags) + 4
    head, tail = struct.pack(bo + "H", len(tags)), b""
    for tag in tags:
        typ, value = entries[tag]
        count, data = _encode_value(typ, value, bo)
        if len(data) <= 4:
            field = data + b"\0" * (4 - len(data))
        else:
            tail += b"\0" * (len(tail) % 2)
            field = struct.pack(bo + "I", data_start + len(tail))
            tail += data
        head += struct.pack(bo + "HHI", tag, typ, count) + field
    return head + struct.pack(bo + "I", 0) + tail


def build_exif_blob(out_meta):
    """A stand-alone little-endian EXIF block (TIFF header + IFD0 + EXIF + GPS),
    as embedded in JPEG APP1, PNG eXIf, WebP and AVIF."""
    ifd0 = dict(out_meta["ifd0"])
    subs = [(tag, out_meta[key]) for key, tag in (("exif", _TAG_EXIF_IFD), ("gps", _TAG_GPS_IFD))
            if out_meta.get(key)]
    for tag, _ in subs:
        ifd0[tag] = (4, (0,))
    pos = 8 + len(serialise_ifd(ifd0, 8))
    pos += pos % 2
    chunks = []
    for tag, entries in subs:
        ifd0[tag] = (4, (pos,))
        block = serialise_ifd(entries, pos)
        block += b"\0" * (len(block) % 2)
        chunks.append(block)
        pos += len(block)
    first = serialise_ifd(ifd0, 8)
    first += b"\0" * (len(first) % 2)
    return b"II*\0" + struct.pack("<I", 8) + first + b"".join(chunks)


def _tiff_add_sub_ifds(f, out_meta):
    """tifffile cannot write an EXIF or GPS directory, so they are appended here:
    the sub-directories go at the end of the file, and a copy of the first
    directory that points at them replaces the original."""
    subs = [(tag, out_meta[key]) for key, tag in (("exif", _TAG_EXIF_IFD), ("gps", _TAG_GPS_IFD))
            if out_meta.get(key)]
    if not subs:
        return
    f.seek(0)
    head = f.read(8)
    if head[:4] != b"II*\0":
        return                                           # BigTIFF / big-endian: leave untouched
    (ifd_pos,) = struct.unpack("<I", head[4:8])
    f.seek(ifd_pos)
    (n,) = struct.unpack("<H", f.read(2))
    raw = f.read(12 * n)
    next_ifd = f.read(4)
    entries = {struct.unpack("<H", raw[12 * i:12 * i + 2])[0]: raw[12 * i:12 * i + 12]
               for i in range(n)}
    f.seek(0, os.SEEK_END)
    pos = f.tell()
    if pos % 2:
        f.write(b"\0")
        pos += 1
    for tag, sub in subs:
        block = serialise_ifd(sub, pos)
        block += b"\0" * (len(block) % 2)
        f.write(block)
        entries[tag] = struct.pack("<HHII", tag, 4, 1, pos)
        pos += len(block)
    f.write(struct.pack("<H", len(entries)) + b"".join(entries[t] for t in sorted(entries)) + next_ifd)
    f.seek(4)
    f.write(struct.pack("<I", pos))


# ==========================================
# --- EXPORT FORMATS ---
# ==========================================
# JPEG, PNG, TIFF, WebP and BMP always work (Pillow + tifffile). The other
# formats appear in the export dialog only when the library that writes them
# is installed:
#     pip install imagecodecs    JPEG XL, 16-bit JPEG 2000, 10/12-bit AVIF, LZW and ZSTD TIFF
#     pip install pillow-heif    HEIF / HEIC
#     pip install OpenEXR        OpenEXR
# AVIF also works through Pillow's own AVIF support (Pillow 11.3 or newer).

_OPTIONAL_MODULES = {}


def _optional(name):
    """An optional module, imported once; None when it is not installed."""
    if name not in _OPTIONAL_MODULES:
        try:
            mod = __import__(name)
            if name == "pillow_heif":
                mod.register_heif_opener()
            if name == "OpenEXR" and not hasattr(mod, "File"):
                mod = None                      # the old (pre-3.0) bindings have a different API
            _OPTIONAL_MODULES[name] = mod
        except Exception:
            _OPTIONAL_MODULES[name] = None
    return _OPTIONAL_MODULES[name]


def _ic_has(codec):
    ic = _optional("imagecodecs")
    try:
        return bool(ic is not None and getattr(ic, codec).available)
    except Exception:
        return False


def _pil_has(feature):
    try:
        return bool(pil_features.check(feature))
    except Exception:
        return False


def lzw_available():
    return _ic_has("LZW")


def zstd_available():
    return _ic_has("ZSTD")


def avif_available():
    return _pil_has("avif") or _ic_has("AVIF")


# key -> (label, file extension, bit depths it can hold (first = default))
EXPORT_FORMATS = {
    "tiff": ("TIFF", ".tiff", (16, 8, 32)),
    "dng": ("DNG  (raw, for Lightroom)", ".dng", (16,)),
    "png": ("PNG", ".png", (16, 8)),
    "jpeg": ("JPEG", ".jpg", (8,)),
    "jxl": ("JPEG XL", ".jxl", (16, 8)),
    "avif": ("AVIF", ".avif", (8, 10, 12)),
    "heif": ("HEIF / HEIC", ".heic", (8, 10)),
    "webp": ("WebP", ".webp", (8,)),
    "jp2": ("JPEG 2000", ".jp2", (16, 8)),
    "exr": ("OpenEXR  (linear, floating point)", ".exr", (16, 32)),
    "bmp": ("BMP", ".bmp", (8,)),
}

# What each format needs installed (shown in the dialog when it is missing).
FORMAT_NEEDS = {"jxl": "imagecodecs", "heif": "pillow-heif", "exr": "OpenEXR"}

# Settings that change the pixels (quality) for the lossy formats.
QUALITY_FIELD = {"jpeg": "jpeg_quality", "webp": "webp_quality", "avif": "avif_quality",
                 "jxl": "jxl_quality", "heif": "heif_quality", "jp2": "jp2_quality"}
QUALITY_RANGE = {"jp2": (20, 70)}               # JPEG 2000 quality is a target PSNR in dB

BIT_LABELS = {8: "8-bit", 10: "10-bit", 12: "12-bit", 16: "16-bit", 32: "32-bit float"}


def format_available(fmt):
    if fmt == "avif":
        return avif_available()
    if fmt == "jxl":
        return _ic_has("JPEGXL")
    if fmt == "heif":
        return _optional("pillow_heif") is not None
    if fmt == "jp2":
        return _pil_has("jpg_2000") or _ic_has("JPEG2K")
    if fmt == "exr":
        return _optional("OpenEXR") is not None
    return fmt in EXPORT_FORMATS


def available_formats():
    return [k for k in EXPORT_FORMATS if format_available(k)]


def format_bits(fmt):
    """Bit depths this installation can write in `fmt` (first = default)."""
    bits = EXPORT_FORMATS[fmt][2]
    if fmt == "avif" and not _ic_has("AVIF"):
        bits = (8,)                          # Pillow's AVIF writer is 8-bit only
    if fmt == "jp2" and not _ic_has("JPEG2K"):
        bits = (8,)
    return bits


# Formats that cannot embed an ICC profile describe their colours with a short
# code instead (CICP), which exists for these spaces only. None = any space.
_CICP_SPACES = ("srgb", "p3", "rec2020", "linsrgb")


def format_spaces(fmt, bits=8):
    """Output colour spaces `fmt` can label correctly, or None for all of them."""
    if fmt in ("jp2", "bmp"):
        return ("srgb",)                     # no colour profile in these files: readers assume sRGB
    if fmt == "jxl" or (fmt == "avif" and (bits > 8 or not _pil_has("avif"))):
        return _CICP_SPACES
    return None


def format_lossy(opts):
    """True when the chosen format and settings throw image detail away."""
    fmt = opts.fmt
    if fmt == "webp":
        return not opts.webp_lossless
    if fmt == "jxl":
        return not opts.jxl_lossless
    if fmt == "jp2":
        return not opts.jp2_lossless
    if fmt == "exr":
        return opts.exr_compression == "dwaa"
    return fmt in ("jpeg", "avif", "heif")


SIZE_MODES = [("long", "Long edge"), ("short", "Short edge"), ("width", "Width"),
              ("height", "Height"), ("percent", "Percent")]
RESAMPLE_MODES = [("lanczos", "Lanczos  (sharpest)"), ("bicubic", "Bicubic  (smooth)"),
                  ("area", "Area average  (softest, no ringing)")]
SHARPEN_MODES = [("none", "None"), ("screen", "Screen"), ("matte", "Matte paper"),
                 ("glossy", "Glossy paper")]
SHARPEN_AMOUNTS = [("low", "Low"), ("standard", "Standard"), ("high", "High")]
EXR_COMPRESSIONS = [("zip", "ZIP  (lossless)"), ("piz", "PIZ  (lossless, good for grain)"),
                    ("dwaa", "DWAA  (lossy, small)")]


@dataclass
class ExportOptions:
    fmt: str = "tiff"
    bits: int = 16
    space: str = "srgb"
    embed_icc: bool = True
    copy_metadata: bool = True
    strip_gps: bool = False             # leave the location out even when copying metadata
    dpi: int = 0                        # 0 = keep the raw file's value (300 when it has none)
    resolution: str = "full"            # "full" = the image's demosaic, "half" = 2x2 superpixel binning
    size_mode: str = "long"             # what `long_edge` measures: long | short | width | height | percent
    long_edge: int = 0                  # 0 = keep size, otherwise shrink to this (pixels, or percent)
    resample: str = "lanczos"           # lanczos | bicubic | area
    sharpen: str = "none"               # output sharpening: none | screen | matte | glossy
    sharpen_amount: str = "standard"    # low | standard | high
    tiff_compression: str = "none"      # none | deflate | lzw | zstd
    jpeg_quality: int = 92
    jpeg_subsampling: str = "4:4:4"     # 4:4:4 | 4:2:2 | 4:2:0
    jpeg_progressive: bool = False
    png_level: int = 6                  # zlib level 1..9
    webp_quality: int = 90
    webp_lossless: bool = False
    webp_effort: int = 4                # 0..6
    avif_quality: int = 80
    avif_speed: int = 6                 # 0 (slowest, smallest) .. 10
    avif_subsampling: str = "4:4:4"     # 4:4:4 | 4:2:0
    jxl_quality: int = 90
    jxl_effort: int = 7                 # 1..9
    jxl_lossless: bool = False
    heif_quality: int = 85
    heif_subsampling: str = "4:2:0"     # 4:4:4 | 4:2:0
    jp2_quality: int = 45               # target PSNR in dB
    jp2_lossless: bool = False
    exr_compression: str = "zip"        # zip | piz | dwaa
    target_kb: int = 0                  # lossy formats: highest quality that fits in this many KB (0 = off)
    dng_kind: str = "linear"            # "linear" = demosaiced here (4 planes) | "mosaic" = untouched sensor data
    dng_own_profile: bool = True        # name the camera so that Lightroom must use the embedded matrices
    dng_linear_curve: bool = False      # embed a straight tone curve (render like this app, no added contrast)
    dng_look: str = "none"              # embed a Sony look profile: none | real | standard
    suffix: str = ""
    out_dir: str = ""                   # "" = next to the source file
    overwrite: bool = False

    def effective_bits(self):
        allowed = format_bits(self.fmt)
        return self.bits if self.bits in allowed else allowed[0]

    def effective_space(self):
        allowed = format_spaces(self.fmt, self.effective_bits())
        return self.space if not allowed or self.space in allowed else allowed[0]

    def copy(self):
        return replace(self)


# Sony looks a DNG can carry as its embedded camera profile (see export_dng).
DNG_LOOKS = [("none", "None  (the raw converter's own profiles)"),
             ("real", "Sony 'Real'  (Image Data Converter's colour and tone)"),
             ("standard", "Sony 'Standard'  (Image Data Converter's)")]


# The looks are two DNG camera profiles (.dcp) fitted to IDC SR's "Real" and
# "Standard" colour and tone (made by f828_dcp.py; see its README). They are
# looked for next to this program, in a "profiles" folder beside it, and in
# Adobe's own camera-profile folder.
DCP_FILES = {"real": "Sony DSC-F828 IDC Real.dcp", "standard": "Sony DSC-F828 IDC Standard.dcp"}
# The exposure the profiles are fitted at (f828_dcp.exposure_offset): G output of
# IDC's matrix for the neutral of the profiles' reference white (5000 K, tint 15
# in their model), and the 12-bit scale at black 494.
DCP_REF_G = 0.994350287629
DCP_REF_K = (16368.0 - 494.0) * 0.25
_DCP_FOUND = {}
_DCP_TAGS = {}


def _dcp_folders():
    out = []
    bases = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(os.path.abspath(sys.argv[0] or "."))]
    if getattr(sys, "frozen", False):                  # a PyInstaller build: beside the .exe, and bundled in it
        bases.append(os.path.dirname(os.path.abspath(sys.executable)))
        if getattr(sys, "_MEIPASS", None):
            bases.append(sys._MEIPASS)
    for base in bases:
        for d in (base, os.path.join(base, "profiles"), os.path.join(base, "lightroom-profile")):
            if d not in out:
                out.append(d)
    if os.environ.get("APPDATA"):
        out.append(os.path.join(os.environ["APPDATA"], "Adobe", "CameraRaw", "CameraProfiles"))
    out.append(os.path.expanduser("~/Library/Application Support/Adobe/CameraRaw/CameraProfiles"))
    return out


def dcp_path(look):
    """Where the camera profile of a Sony look is, or None (looked up once)."""
    if look not in _DCP_FOUND:
        name = DCP_FILES.get(look)
        _DCP_FOUND[look] = next((p for p in (os.path.join(d, name) for d in _dcp_folders())
                                 if os.path.isfile(p)), None) if name else None
    return _DCP_FOUND[look]


def dng_looks_available():
    return all(dcp_path(k) is not None for k in DCP_FILES)


def read_dcp_tags(path):
    """A .dcp camera profile's tags as {tag: (type, value)}, the form serialise_ifd() writes."""
    key = (path, os.path.getmtime(path))
    if key not in _DCP_TAGS:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(8)
            if head[:4] != b"IIRC":
                raise ValueError(f"{os.path.basename(path)} is not a DNG camera profile")
            tags = _read_ifd(f, "<", struct.unpack("<I", head[4:8])[0], size, max_bytes=size)
        _DCP_TAGS.clear()
        _DCP_TAGS[key] = tags
    return _DCP_TAGS[key]


def dng_look_tags(look, sensor, black, white, neutral):
    """Tags that embed a Sony look in a DNG: everything in its camera profile
    except the camera name, the white balance as AsShotNeutral, and the shot's
    exposure relative to the profile as BaselineExposure (IDC's digital gain and
    the G output of its matrix for this white, in the 0.01 EV steps the DNG SDK
    keeps). Applied last, they replace the converter's own matrices."""
    tags = dict(read_dcp_tags(dcp_path(look)))
    tags.pop(50708, None)                                    # UniqueCameraModel: the DNG names its camera
    n = np.asarray(neutral, dtype=np.float64)
    n = n / n.max()
    tags[50728] = _rationals(n, 1000000)                     # AsShotNeutral
    cam = sensor.get("camera")
    if cam is not None:
        g = float((cam["matrix"].astype(np.float64) @ (n * cam["pregain"].astype(np.float64)))[1])
        k = (float(white) - float(np.mean(black))) * DSP_INPUT_SCALE
        ev = math.log2(float(cam["gain"]) * g * k / (DCP_REF_G * DCP_REF_K))
        tags[50730] = _rationals([round(ev, 2)], 100, signed=True)      # BaselineExposure
    return tags


EXPORT_PRESETS = [
    ("Archive / further editing: 16-bit TIFF, ProPhoto, ZIP",
     dict(fmt="tiff", bits=16, space="prophoto", tiff_compression="deflate", long_edge=0,
          sharpen="none", resolution="full")),
    ("Print: 16-bit TIFF, Adobe RGB, 300 dpi, matte sharpening",
     dict(fmt="tiff", bits=16, space="adobergb", tiff_compression="deflate", long_edge=0, dpi=300,
          sharpen="matte", sharpen_amount="standard", resolution="full")),
    ("Web: JPEG, sRGB, 2048 px, screen sharpening",
     dict(fmt="jpeg", bits=8, space="srgb", jpeg_quality=88, jpeg_subsampling="4:4:4",
          size_mode="long", long_edge=2048, resample="lanczos", sharpen="screen",
          sharpen_amount="standard", strip_gps=True, resolution="full")),
    ("Share small: JPEG, sRGB, 1600 px, under 500 KB",
     dict(fmt="jpeg", bits=8, space="srgb", jpeg_subsampling="4:2:0", size_mode="long",
          long_edge=1600, sharpen="screen", target_kb=500, strip_gps=True, resolution="full")),
    ("Modern web: AVIF, sRGB, 2560 px",
     dict(fmt="avif", bits=8, space="srgb", avif_quality=70, size_mode="long", long_edge=2560,
          sharpen="screen", resolution="full")),
    ("Lightroom: linear DNG with the Sony look",
     dict(fmt="dng", dng_kind="linear", dng_own_profile=True, dng_look="real")),
]


def export_summary(opts):
    """One line describing the export settings (for the export preview panel)."""
    fmt = opts.fmt
    name = EXPORT_FORMATS[fmt][0].split("  (")[0]
    if fmt == "dng":
        return f"DNG  \u00B7  {'linear' if opts.dng_kind == 'linear' else 'mosaic'}  \u00B7  stays raw"
    parts = [f"{name} {BIT_LABELS[opts.effective_bits()]}", COLOR_SPACES[opts.effective_space()]["label"]]
    field = QUALITY_FIELD.get(fmt)
    if fmt == "exr":
        parts.append(dict(EXR_COMPRESSIONS)[opts.exr_compression].split("  (")[0])
    elif field and format_lossy(opts):
        parts.append(f"quality {getattr(opts, field)}{' dB' if fmt == 'jp2' else ''}")
        if opts.target_kb:
            parts.append(f"at most {opts.target_kb} KB")
    else:
        parts.append("lossless")
    if opts.resolution == "half":
        parts.append("half size (binned)")
    if opts.long_edge:
        if opts.size_mode == "percent":
            parts.append(f"{opts.long_edge}% size")
        else:
            parts.append(f"{dict(SIZE_MODES)[opts.size_mode].lower()} {opts.long_edge} px")
    if opts.sharpen != "none":
        parts.append(f"{dict(SHARPEN_MODES)[opts.sharpen].lower()} sharpening")
    return "  \u00B7  ".join(parts)


def _png_chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def encode_png16(arr, level=6, icc=None, exif=None, icc_name="ICC profile", dpi=None):
    """Pillow cannot write 16-bit RGB PNG, so this is a minimal writer for it
    (colour type 2, 'Sub' row filter, optional iCCP, pHYs and eXIf chunks)."""
    h, w, _ = arr.shape
    rows = np.ascontiguousarray(arr.astype(">u2")).view(np.uint8).reshape(h, w * 6)
    filt = np.empty((h, w * 6 + 1), dtype=np.uint8)
    filt[:, 0] = 1
    filt[:, 1:7] = rows[:, :6]
    np.subtract(rows[:, 6:], rows[:, :-6], out=filt[:, 7:])
    out = [b"\x89PNG\r\n\x1a\n", _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))]
    if icc:
        out.append(_png_chunk(b"iCCP", icc_name.encode("latin-1")[:79] + b"\0\0" + zlib.compress(icc, 9)))
    if dpi:
        ppm = int(round(dpi / 0.0254))
        out.append(_png_chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1)))
    if exif:
        out.append(_png_chunk(b"eXIf", exif))
    comp = zlib.compressobj(int(level))
    step = max(1, (4 << 20) // filt.shape[1])
    for y in range(0, h, step):
        data = comp.compress(filt[y:y + step].tobytes())
        if data:
            out.append(_png_chunk(b"IDAT", data))
    out.append(_png_chunk(b"IDAT", comp.flush()))
    out.append(_png_chunk(b"IEND", b""))
    return b"".join(out)


def _jxl_distance(quality):
    """cjxl's mapping from a 0-100 quality to the Butteraugli distance it encodes at."""
    q = float(quality)
    return 0.1 + (100.0 - q) * 0.09 if q >= 30 else 6.4 + (2.5 ** ((30.0 - q) / 5.0)) / 6.25


def _cicp(space):
    """(primaries, transfer) codes of an output space for formats without ICC profiles."""
    prim = {"srgb": 1, "linsrgb": 1, "p3": 12, "rec2020": 9}[space]
    trc = {"srgb": 13, "p3": 13, "linsrgb": 8, "rec2020": 1}[space]
    return prim, trc


def _resolution(out_meta, opts):
    """(dots per inch, unit) for the file."""
    if opts.dpi:
        return float(opts.dpi), "INCH"
    ifd0 = out_meta["ifd0"] if out_meta else {}
    res = (_ratio(ifd0[282]) if ifd0.get(282) else None) or 300.0
    unit = {2: "INCH", 3: "CENTIMETER"}.get(ifd0[296][1][0] if ifd0.get(296) else 2, "INCH")
    return res, unit


def _encode_tiff(arr, opts, out_meta, icc):
    ifd0 = out_meta["ifd0"] if out_meta else {}
    extratags = [(tag, "s", 0, _ascii(ifd0[tag]), True)
                 for tag in (271, 272, 306, 315, 33432) if _ascii(ifd0.get(tag))]
    extratags.append((274, "H", 1, 1, True))                    # Orientation: upright
    res, unit = _resolution(out_meta, opts)
    kw = dict(photometric="rgb", metadata=None, byteorder="<", bigtiff=False,
              software=SOFTWARE_TAG, resolution=(res, res), resolutionunit=unit,
              extratags=extratags)
    if _ascii(ifd0.get(270)):
        kw["description"] = _ascii(ifd0.get(270))
    comp = opts.tiff_compression
    if comp == "lzw" and not lzw_available() or comp == "zstd" and not zstd_available():
        comp = "deflate"
    if comp != "none":
        kw.update(compression={"deflate": "zlib", "lzw": "lzw", "zstd": "zstd"}[comp], predictor=True)
    if icc:
        kw["iccprofile"] = icc
    buf = io.BytesIO()
    try:
        tiff.imwrite(buf, arr, **kw)
    except TypeError:
        # older tifffile releases have no `iccprofile` argument: write the
        # profile as a plain tag (34675 = InterColorProfile) instead
        kw.pop("iccprofile", None)
        if icc:
            kw["extratags"] = extratags + [(34675, "B", len(icc), icc, True)]
        buf = io.BytesIO()
        tiff.imwrite(buf, arr, **kw)
    if out_meta:
        _tiff_add_sub_ifds(buf, out_meta)
    return buf.getvalue()


def _encode_exr(arr, opts):
    """OpenEXR: linear light, half or full float, labelled with the space's primaries."""
    exr = _optional("OpenEXR")
    cs = COLOR_SPACES[opts.space]
    white = cs["white"]
    wx, wy = white[0] / sum(white), white[1] / sum(white)
    (rx, ry), (gx, gy), (bx, by) = cs["primaries"]
    header = {"compression": {"zip": exr.ZIP_COMPRESSION, "piz": exr.PIZ_COMPRESSION,
                              "dwaa": exr.DWAA_COMPRESSION}[opts.exr_compression],
              "type": exr.scanlineimage,
              "chromaticities": (rx, ry, gx, gy, bx, by, wx, wy)}
    data = np.ascontiguousarray(arr, dtype=np.float16 if opts.effective_bits() == 16 else np.float32)
    fd, tmp = tempfile.mkstemp(suffix=".exr")
    os.close(fd)
    try:
        exr.File(header, {"RGB": data}).write(tmp)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def encode_image(arr, opts, out_meta, quality=None):
    """The complete file `opts` describes, as bytes.
    arr: uint8 / uint16 holding the encoded values of `opts.space` (10 and 12-bit
    AVIF / HEIF take uint16 too), float32 for 32-bit TIFF, linear float32 for
    OpenEXR. `quality` overrides the format's quality setting."""
    fmt, bits = opts.fmt, opts.effective_bits()
    space = opts.effective_space()
    icc = build_icc_profile(space) if opts.embed_icc else None
    exif = build_exif_blob(out_meta) if out_meta else None
    dpi, unit = _resolution(out_meta, opts)
    if unit != "INCH":
        dpi = dpi * 2.54
    q = int(quality if quality is not None else getattr(opts, QUALITY_FIELD.get(fmt, "jpeg_quality")))

    if fmt == "tiff":
        return _encode_tiff(arr, opts, out_meta, icc)
    if fmt == "exr":
        return _encode_exr(arr, opts)
    if fmt == "png" and arr.dtype == np.uint16:
        return encode_png16(arr, opts.png_level, icc, exif, COLOR_SPACES[space]["label"], dpi)

    ic = _optional("imagecodecs")
    if fmt == "jxl":
        prim, trc = _cicp(space)
        return bytes(ic.jpegxl_encode(np.ascontiguousarray(arr), effort=int(opts.jxl_effort),
                                      distance=None if opts.jxl_lossless else _jxl_distance(q),
                                      lossless=bool(opts.jxl_lossless), bitspersample=bits,
                                      primaries=prim if prim != 12 else 11, transfer=trc))
    if fmt == "avif" and (bits > 8 or not _pil_has("avif")):
        prim, trc = _cicp(space)
        data = arr if bits == 8 else (arr.astype(np.uint32) * ((1 << bits) - 1) // 65535).astype(np.uint16)
        return bytes(ic.avif_encode(np.ascontiguousarray(data), level=q, speed=int(opts.avif_speed),
                                    bitspersample=bits, primaries=prim, transfer=trc, matrix=1,
                                    pixelformat="yuv444" if opts.avif_subsampling == "4:4:4" else "yuv420"))
    if fmt == "jp2" and bits == 16:
        return bytes(ic.jpeg2k_encode(np.ascontiguousarray(arr), level=None if opts.jp2_lossless else q,
                                      reversible=bool(opts.jp2_lossless), codecformat="JP2"))
    if fmt == "heif" and bits > 8:
        heif = _optional("pillow_heif")
        hf = heif.from_bytes(mode="RGB;16", size=(arr.shape[1], arr.shape[0]),
                             data=np.ascontiguousarray(arr, dtype="<u2").tobytes())
        buf = io.BytesIO()
        kw = {"quality": q, "chroma": 444 if opts.heif_subsampling == "4:4:4" else 420}
        if icc:
            kw["icc_profile"] = icc
        if exif:
            kw["exif"] = exif
        hf.save(buf, **kw)
        return buf.getvalue()

    img = Image.fromarray(np.ascontiguousarray(arr))
    kw = {}
    if icc and fmt != "bmp":
        kw["icc_profile"] = icc
    buf = io.BytesIO()
    if fmt == "png":
        if exif:
            kw["exif"] = exif
        img.save(buf, format="PNG", compress_level=int(opts.png_level), dpi=(dpi, dpi), **kw)
    elif fmt == "jpeg":
        if exif:
            kw["exif"] = b"Exif\0\0" + exif
        img.save(buf, format="JPEG", quality=q, dpi=(round(dpi), round(dpi)),
                 subsampling={"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}[opts.jpeg_subsampling],
                 progressive=bool(opts.jpeg_progressive), optimize=True, **kw)
    elif fmt == "webp":
        if exif:
            kw["exif"] = exif
        img.save(buf, format="WEBP", quality=q, lossless=bool(opts.webp_lossless),
                 method=int(opts.webp_effort), exact=bool(opts.webp_lossless), **kw)
    elif fmt == "avif":
        if exif:
            kw["exif"] = exif
        img.save(buf, format="AVIF", quality=q, speed=int(opts.avif_speed),
                 subsampling=opts.avif_subsampling, **kw)
    elif fmt == "heif":
        if exif:
            kw["exif"] = exif
        img.save(buf, format="HEIF", quality=q, chroma=444 if opts.heif_subsampling == "4:4:4" else 420, **kw)
    elif fmt == "jp2":
        if opts.jp2_lossless:
            img.save(buf, format="JPEG2000", irreversible=False)
        else:
            img.save(buf, format="JPEG2000", irreversible=True, quality_mode="dB", quality_layers=[q])
    elif fmt == "bmp":
        img.save(buf, format="BMP", dpi=(dpi, dpi))
    else:
        raise ValueError(f"Unknown export format {fmt!r}")
    return buf.getvalue()


def decode_image(data, opts, like):
    """Pixels back out of a file encode_image wrote, in the same layout and scale
    as the array that went in (`like`)."""
    fmt, bits = opts.fmt, opts.effective_bits()
    ic = _optional("imagecodecs")
    if fmt == "exr":
        exr = _optional("OpenEXR")
        f = exr.File(io.BytesIO(data))
        return np.asarray(f.channels()["RGB"].pixels, dtype=np.float32)
    if fmt == "jxl":
        out = ic.jpegxl_decode(data)
    elif fmt == "avif" and (bits > 8 or not _pil_has("avif")):
        out = ic.avif_decode(data)
        if bits > 8:
            out = (out.astype(np.uint32) * 65535 // ((1 << bits) - 1)).astype(np.uint16)
    elif fmt == "jp2" and bits == 16:
        out = ic.jpeg2k_decode(data)
    elif fmt == "heif" and bits > 8:
        heif = _optional("pillow_heif")
        hf = heif.open_heif(io.BytesIO(data), convert_hdr_to_8bit=False)
        out = np.asarray(hf)
        if out.dtype == np.uint16 and int(out.max(initial=0)) <= 1023:
            out = (out.astype(np.uint32) * 65535 // 1023).astype(np.uint16)
    else:
        out = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    out = np.asarray(out)[..., :3]
    return out.astype(like.dtype) if out.dtype != like.dtype else out


def encode_fitting(arr, opts, out_meta):
    """encode_image, choosing the highest quality whose file fits opts.target_kb
    for the lossy formats. Returns (bytes, quality used or None)."""
    field = QUALITY_FIELD.get(opts.fmt)
    if not opts.target_kb or field is None or not format_lossy(opts):
        return encode_image(arr, opts, out_meta), None
    limit = int(opts.target_kb) * 1024
    lo, hi = QUALITY_RANGE.get(opts.fmt, (1, 100))
    hi = min(hi, int(getattr(opts, field)))          # never above the quality the user set
    best = None
    while lo <= hi:                                   # binary search, about 7 encodes
        mid = (lo + hi) // 2
        data = encode_image(arr, opts, out_meta, quality=mid)
        if len(data) <= limit:
            best, lo = (data, mid), mid + 1
        else:
            hi = mid - 1
    if best is None:                                  # even the lowest quality is too big
        q = QUALITY_RANGE.get(opts.fmt, (1, 100))[0]
        return encode_image(arr, opts, out_meta, quality=q), q
    return best


def save_image(dst, arr, opts, out_meta):
    """Write `arr` to `dst` (a path or a binary file object) as `opts` describes.
    Returns the quality used when a target file size chose it, else None."""
    data, used = encode_fitting(arr, opts, out_meta)
    if isinstance(dst, (str, os.PathLike)):
        with open(dst, "wb") as f:
            f.write(data)
    else:
        dst.write(data)
    return used


# ==========================================
# --- DNG EXPORT ---
# ==========================================
# A DNG is a TIFF whose pixels are still camera data, plus tags that tell the
# raw converter how to interpret them.  The tags written here are this app's own
# colour description of the F828:
#
#   ColorMatrix1   XYZ -> camera RGBE (D65). Only used for Kelvin <-> neutral.
#   ForwardMatrix1 white-balanced camera RGBE -> XYZ D50: the colour rendering,
#                  i.e. the same matrix this app develops with.
#   AsShotNeutral  the white balance chosen in the app.
#
# "linear": demosaiced here and written as four colour planes (R, G, B, E).
# "mosaic": the untouched sensor mosaic; the raw converter demosaics it.
# Exposure, black point, roll-off and IR mode are not applied: it is a raw.

DNG_PROFILE_NAME = "F828 Converter matrix"
DNG_CAMERA_PLAIN = "Sony DSC-F828"
DNG_CAMERA_OWN = "Sony DSC-F828 RGBE (F828 Converter)"


def _rationals(values, den=10000, signed=False):
    return (10 if signed else 5, tuple((int(round(float(v) * den)), den) for v in np.ravel(values)))


def _ascii_tag(text):
    return (2, str(text).encode("ascii", "replace") + b"\0")


def tiff_orientation(rot_k, flip):
    """TIFF / EXIF orientation code for 'mirror left-right if flip, then turn
    clockwise rot_k quarter turns'."""
    k = int(rot_k or 0) % 4
    return ((1, 6, 3, 8), (2, 7, 4, 5))[1 if flip else 0][k]


def write_raw_tiff(dst, data, tags, sub_ifds):
    """Minimal little-endian TIFF: header, uncompressed pixel strips, the
    sub-directories (EXIF, GPS), then the main directory.
    data: uint16, (h, w) or (h, w, planes).  tags: {tag: (type, value)}.
    sub_ifds: {pointer tag: {tag: (type, value)}}."""
    data = np.ascontiguousarray(data, dtype="<u2")
    h, w = data.shape[:2]
    planes = data.shape[2] if data.ndim == 3 else 1
    row_bytes = w * planes * 2
    rows_per_strip = max(1, min(h, (2 << 20) // row_bytes))
    counts = [min(rows_per_strip, h - y) * row_bytes for y in range(0, h, rows_per_strip)]
    offsets, pos = [], 8
    for n in counts:
        offsets.append(pos)
        pos += n
    tags = dict(tags)
    tags.update({256: (4, (w,)), 257: (4, (h,)), 258: (3, (16,) * planes), 259: (3, (1,)),
                 273: (4, tuple(offsets)), 277: (3, (planes,)), 278: (4, (rows_per_strip,)),
                 279: (4, tuple(counts)), 284: (3, (1,))})
    close = isinstance(dst, (str, os.PathLike))
    f = open(dst, "wb") if close else dst
    try:
        f.write(b"II*\0" + struct.pack("<I", 0))
        f.write(data.tobytes())
        pos += pos % 2
        f.write(b"\0" * (pos - (8 + sum(counts))))
        for pointer, entries in sub_ifds.items():
            if not entries:
                continue
            block = serialise_ifd(entries, pos)
            block += b"\0" * (len(block) % 2)
            f.write(block)
            tags[pointer] = (4, (pos,))
            pos += len(block)
        f.write(serialise_ifd(tags, pos))
        f.seek(4)
        f.write(struct.pack("<I", pos))
        f.seek(0, os.SEEK_END)
    finally:
        if close:
            f.close()


def export_dng(sensor, edit, opts, dst, meta=None):
    """Write one image as a DNG. Returns (width, height) as it will be displayed."""
    e = resolve_edit(edit.copy(), sensor)
    black = np.asarray(black_for(sensor, e.black_mode), dtype=np.float64)
    white = float(sensor["white"])
    neutral = 1.0 / np.asarray(wb_from_temp_tint(e.temp, e.tint), dtype=np.float64)     # largest = 1
    cmap = sensor["cmap"]

    if opts.dng_kind == "mosaic":
        data = np.clip(np.rint(sensor_mosaic(sensor, e.hot_pixels)), 0, 65535).astype(np.uint16)
        h, w = data.shape
        order = [int(c) for c in cmap[:2, :2].ravel()]
        shape_tags = {
            262: (3, (32803,)),                              # PhotometricInterpretation = CFA
            274: (3, (tiff_orientation(e.rot_k, e.flip),)),
            33421: (3, (2, 2)), 33422: (1, bytes(order)),    # CFARepeatPatternDim, CFAPattern
            50710: (1, bytes([0, 1, 2, 3])),                 # CFAPlaneColor: R, G, B, 4th (emerald)
            50711: (3, (1,)),                                # CFALayout: rectangular
            50713: (3, (2, 2)),                              # BlackLevelRepeatDim
            50714: _rationals([black[c] for c in order], 1000),
            50717: (4, (int(round(white)),)),
        }
        margin = 4                      # raw converters need a few spare pixels round a mosaic to interpolate
        crop_origin, crop_size = (margin, margin), (w - 2 * margin, h - 2 * margin)
        shown = oriented_size(crop_size[0], crop_size[1], e.rot_k)
    else:
        dn = sensor_rgbe_dn(sensor, "full", e.demosaic, e.hot_pixels, (e.ca_red, e.ca_blue))
        if dn.shape[2] > 4:                                  # IDC's demosaic: its sharp luminance into the data
            dn = idc_frame_to_dn(dn, sensor, black)
        dn = apply_orientation(dn, e.rot_k, e.flip)
        fh, fw = dn.shape[:2]
        rect = crop_to_pixels(effective_crop(e.crop, e.straighten, fw, fh), fw, fh)
        dn = rotate_crop(dn, e.straighten, rect, order=3)
        gain = max(1, min(4, int(65535 // white)))           # keep fractions of a DN from the interpolation
        data = np.clip(np.rint(dn * gain), 0, 65535).astype(np.uint16)
        del dn
        h, w = data.shape[:2]
        shape_tags = {
            262: (3, (34892,)),                              # PhotometricInterpretation = LinearRaw
            274: (3, (1,)),
            50714: _rationals(black * gain, 1000),
            50717: (4, (int(round(white * gain)),) * 4),
        }
        crop_origin, crop_size = (0, 0), (w, h)
        shown = (w, h)

    if meta is None:
        meta = read_metadata(sensor["path"]) if opts.copy_metadata and sensor.get("path") else None
    out_meta = output_metadata(meta, shown[0], shown[1], "raw", opts.copy_metadata)
    out_meta["exif"].pop(40961, None)                        # ColorSpace does not apply to raw data
    tags = {t: v for t, v in out_meta["ifd0"].items() if t in (270, 306, 315, 33432)}
    tags.update(shape_tags)
    tags.update({
        254: (4, (0,)),                                      # NewSubFileType: the main image
        271: out_meta["ifd0"].get(271) or _ascii_tag("SONY"),
        272: out_meta["ifd0"].get(272) or _ascii_tag("DSC-F828"),
        305: _ascii_tag(SOFTWARE_TAG),
        50706: (1, bytes([1, 4, 0, 0])),                     # DNGVersion
        50707: (1, bytes([1, 2, 0, 0])),                     # DNGBackwardVersion
        50708: _ascii_tag(DNG_CAMERA_OWN if opts.dng_own_profile else DNG_CAMERA_PLAIN),
        50718: _rationals([1, 1], 1),                        # DefaultScale
        50719: (4, crop_origin), 50720: (4, crop_size),      # DefaultCropOrigin, DefaultCropSize
        50721: _rationals(XYZ_TO_RGBE, signed=True),         # ColorMatrix1
        50728: _rationals(neutral, 1000000),                 # AsShotNeutral
        50730: _rationals([0], 100, signed=True),            # BaselineExposure
        50731: _rationals([1], 1), 50732: _rationals([1], 1),    # BaselineNoise, BaselineSharpness
        50734: _rationals([1], 1),                           # LinearResponseLimit
        50778: (3, (21,)),                                   # CalibrationIlluminant1 = D65
        50936: _ascii_tag(DNG_PROFILE_NAME),
        50941: (4, (3,)),                                    # ProfileEmbedPolicy: no restrictions
        50964: _rationals(RGBE_TO_XYZ_D50, signed=True),     # ForwardMatrix1
    })
    if sensor.get("path"):
        tags[50827] = _ascii_tag(os.path.basename(sensor["path"]))      # OriginalRawFileName
    look = opts.dng_look if opts.dng_look in DCP_FILES and dcp_path(opts.dng_look) else None
    if opts.dng_linear_curve and look is None:
        tags[50940] = (11, (0.0, 0.0, 1.0, 1.0))             # ProfileToneCurve: straight line
        tags[51110] = (4, (1,))                              # DefaultBlackRender: none
    if look is not None:                                     # the Sony look's camera profile
        tags.update(dng_look_tags(look, sensor, black, white, neutral))
    write_raw_tiff(dst, data, tags, {_TAG_EXIF_IFD: out_meta["exif"], _TAG_GPS_IFD: out_meta["gps"]})
    return shown


# ==========================================
# --- EXPORT PIPELINE ---
# ==========================================

def export_size(w, h, opts):
    """Output size in pixels for a picture of w x h under the resize settings.
    Never enlarges, except by an explicit percentage (up to 200 %)."""
    v = int(opts.long_edge or 0)
    if v <= 0 or opts.fmt == "dng":
        return w, h
    mode = opts.size_mode
    if mode == "percent":
        s = min(max(v, 1), 200) / 100.0
    else:
        ref = {"long": max(w, h), "short": min(w, h), "width": w, "height": h}.get(mode, max(w, h))
        s = min(1.0, v / float(ref))
    if abs(s - 1.0) < 1e-9:
        return w, h
    return max(1, int(round(w * s))), max(1, int(round(h * s)))


# Output sharpening: (gaussian radius in output pixels, amount for low / standard / high).
# It runs after any resize, on brightness only (no colour fringes), in a
# gamma-2.2 domain so that shadows and highlights are sharpened alike.
OUTPUT_SHARPENING = {"screen": (0.6, (0.35, 0.6, 0.9)),
                     "glossy": (0.9, (0.6, 0.9, 1.25)),
                     "matte": (1.2, (0.8, 1.2, 1.6))}


def output_sharpen(lin, kind, amount="standard", scale=1.0):
    """Unsharp mask on luminance for the output medium. `scale` shrinks the
    radius for a preview shown smaller than the export."""
    if kind not in OUTPUT_SHARPENING:
        return lin
    radius, amounts = OUTPUT_SHARPENING[kind]
    a = np.float32(amounts[{"low": 0, "standard": 1, "high": 2}.get(amount, 1)])
    g = np.power(np.clip(lin, 0.0, 1.0), np.float32(1.0 / 2.2))
    y = g @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    detail = y - gaussian_filter(y, sigma=max(0.3, radius * scale), mode="mirror")
    t = np.float32(0.002)                                   # leave the finest noise alone
    detail = np.sign(detail) * np.maximum(np.abs(detail) - t, 0.0)
    np.clip(detail, -0.12, 0.12, out=detail)                # no bright / dark halos
    g += (a * detail)[..., None]
    np.clip(g, 0.0, 1.0, out=g)
    return np.power(g, np.float32(2.2)).astype(np.float32)


def develop_export(sensor, edit, opts, oog=None):
    """The picture an export holds, before encoding: linear float32 RGB in the
    export colour space, oriented, straightened, cropped, resized and output-sharpened.
    `oog` (optional list) receives a mask of colours outside the export space."""
    edit = resolve_edit(edit.copy(), sensor)
    space = opts.effective_space()
    if opts.resolution == "half":
        dn = sensor_rgbe_dn(sensor, "half", hot_pixels=edit.hot_pixels)
    else:
        dn = sensor_rgbe_dn(sensor, "full", edit.demosaic, edit.hot_pixels, (edit.ca_red, edit.ca_blue))
    rgbe = normalise_rgbe(dn, black_for(sensor, edit.black_mode), sensor["white"])
    del dn
    wb = wb_from_temp_tint(edit.temp, edit.tint)
    matrix = output_matrix(edit, space)
    dsp = sony_dsp_setup(sensor, edit)
    s12 = dsp_input_scale(sensor, edit)
    scale = 1.0 if opts.resolution != "half" else 0.5
    exact = dsp is not None and scale == 1.0 and dsp[0].get("stage") is not None

    if exact:                   # IDC's processor runs along the sensor's rows: the frame as it lies,
        full = develop_finished(rgbe, edit, wb, matrix, dsp=dsp, scale=scale, oog=oog, scale12=s12,
                                exact=True)
        del rgbe                # then turned and cropped
        full = apply_orientation(full, edit.rot_k, edit.flip)
        if oog:
            oog[0] = apply_orientation(oog[0], edit.rot_k, edit.flip)
        h, w = full.shape[:2]
        rect = crop_to_pixels(effective_crop(edit.crop, edit.straighten, w, h), w, h)
        if abs(edit.straighten) < 1e-3:
            x0, y0, x1, y1 = rect
            lin = np.ascontiguousarray(full[y0:y1, x0:x1])
            if oog:
                oog[0] = np.ascontiguousarray(oog[0][y0:y1, x0:x1])
        else:
            lin = rotate_crop(full, edit.straighten, rect, order=3)
            np.clip(lin, 0.0, 1.0, out=lin)
            if oog:
                oog[0] = rotate_crop(oog[0].astype(np.float32), edit.straighten, rect, order=0) > 0.5
        del full
    else:
        rgbe = apply_orientation(rgbe, edit.rot_k, edit.flip)
        h, w = rgbe.shape[:2]
        rect = crop_to_pixels(effective_crop(edit.crop, edit.straighten, w, h), w, h)
        if abs(edit.straighten) < 1e-3 and not edit.highlights:
            x0, y0, x1, y1 = rect
            lin = develop_finished(rgbe[y0:y1, x0:x1], edit, wb, matrix, dsp=dsp, scale=scale, oog=oog,
                                   scale12=s12)
        else:                   # (highlight reconstruction looks at the whole frame, like the preview)
            full = develop_finished(rgbe, edit, wb, matrix, dsp=dsp, scale=scale, oog=oog, scale12=s12)
            lin = rotate_crop(full, edit.straighten, rect, order=3)
            del full
            np.clip(lin, 0.0, 1.0, out=lin)
            if oog:
                oog[0] = rotate_crop(oog[0].astype(np.float32), edit.straighten, rect, order=0) > 0.5
        del rgbe

    nw, nh = export_size(lin.shape[1], lin.shape[0], opts)
    if (nw, nh) != (lin.shape[1], lin.shape[0]):
        lin = resize_linear(lin, nw, nh, opts.resample)
        if oog:
            oog[0] = np.asarray(Image.fromarray(oog[0]).resize((nw, nh), Image.Resampling.NEAREST))
    if opts.sharpen != "none":
        lin = output_sharpen(lin, opts.sharpen, opts.sharpen_amount)
    return lin


def finish_export(lin, opts, strip=256):
    """develop_export's picture -> the array the file stores: encoded integers,
    encoded 32-bit float (TIFF), or linear float (OpenEXR)."""
    if opts.fmt == "exr":
        return lin
    bits = opts.effective_bits()
    space = opts.effective_space()
    if bits == 32:
        out = np.empty(lin.shape, dtype=np.float32)
        for y in range(0, lin.shape[0], strip):
            out[y:y + strip] = encode_trc(lin[y:y + strip], space)
        return out
    if bits in (10, 12):              # stored as 16-bit; the encoder rescales to 10 / 12 bits
        bits = 16
    return encode_quantise(lin, space, bits)


def render_export(sensor, edit, opts):
    """Full-quality render of one image with its edits -> the array the file stores."""
    return finish_export(develop_export(sensor, edit, opts), opts)


def export_metadata(sensor, opts, meta, w, h):
    if meta is None:
        meta = read_metadata(sensor["path"]) if opts.copy_metadata and sensor.get("path") else None
    return output_metadata(meta, w, h, opts.effective_space(), opts.copy_metadata,
                           gps=not opts.strip_gps, dpi=opts.dpi)


def export_image(sensor, edit, opts, dst, meta=None):
    """Render and write one image. `dst` is a path or a binary file object.
    Returns (width, height, quality chosen by a target file size or None)."""
    if opts.fmt == "dng":
        return export_dng(sensor, edit, opts, dst, meta) + (None,)
    arr = render_export(sensor, edit, opts)
    h, w = arr.shape[:2]
    used = save_image(dst, arr, opts, export_metadata(sensor, opts, meta, w, h))
    return w, h, used


def export_path_for(src_path, opts):
    """Destination file name for a source raw, never silently overwriting unless asked."""
    folder = opts.out_dir or os.path.dirname(src_path)
    stem = os.path.splitext(os.path.basename(src_path))[0] + opts.suffix
    ext = EXPORT_FORMATS[opts.fmt][1]
    path = os.path.join(folder, stem + ext)
    n = 1
    while not opts.overwrite and os.path.exists(path):
        path = os.path.join(folder, f"{stem}_{n}{ext}")
        n += 1
    return path


def estimate_export_size(sensor, edit, opts, meta=None):
    """Exact size in bytes of the file this export would produce (encodes in memory).
    Returns (bytes, width, height, quality chosen by a target size or None)."""
    buf = io.BytesIO()
    w, h, used = export_image(sensor, edit, opts, buf, meta)
    return buf.getbuffer().nbytes, w, h, used


def human_size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024.0


def find_raw_files(paths, recursive=False):
    """Expand a mix of files and folders into a sorted, de-duplicated list of raws."""
    found = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _, names in os.walk(p):
                    found += [os.path.join(root, n) for n in names]
            else:
                found += [os.path.join(p, n) for n in os.listdir(p)]
        else:
            found.append(p)
    seen, out = set(), []
    for p in sorted(found, key=lambda s: (os.path.dirname(s).lower(), os.path.basename(s).lower())):
        key = os.path.normcase(os.path.abspath(p))
        if p.lower().endswith(RAW_EXTENSIONS) and key not in seen and os.path.isfile(p):
            seen.add(key)
            out.append(p)
    return out


def embedded_thumbnail(path, size=160):
    """Small RGB uint8 thumbnail: the camera's embedded preview when there is
    one, otherwise None (the caller falls back to a quick develop)."""
    try:
        with RAW_LOCK, rawpy.imread(path) as raw:
            thumb = raw.extract_thumb()
            kind = thumb.format
            data = bytes(thumb.data) if kind == rawpy.ThumbFormat.JPEG else np.array(thumb.data)
        if kind == rawpy.ThumbFormat.JPEG:
            img = Image.open(io.BytesIO(data))
        else:
            img = Image.fromarray(data)
        img = img.convert("RGB")
        img.thumbnail((size, size))
        return np.ascontiguousarray(np.asarray(img))
    except Exception:
        return None


def make_thumbnail(path, edit, use_embedded=True, size=160):
    """Thumbnail for the library (runs on the thumbnail thread)."""
    arr = embedded_thumbnail(path, size) if use_embedded else None
    if arr is None:
        arr = developed_thumbnail(read_sensor(path), edit, size)
    return {"path": path, "thumb": arr}


def developed_thumbnail(sensor, edit, size=160):
    """Thumbnail rendered through the real pipeline with the image's own edits."""
    sp = cfa_to_superpixel(sensor["cfa"], sensor["cmap"])
    k = max(1, min(sp.shape[0], sp.shape[1]) // size)
    dn = block_mean(sp, k).astype(np.float32) if k > 1 else sp
    e = resolve_edit(edit.copy(), sensor)
    rgbe = normalise_rgbe(dn, black_for(sensor, e.black_mode), sensor["white"])
    rgbe = apply_orientation(rgbe, e.rot_k, e.flip)
    lin = develop(rgbe, e, wb_from_temp_tint(e.temp, e.tint), output_matrix(e, "srgb"),
                  dsp=sony_dsp_setup(sensor, e))
    img = encode_quantise(lin, "srgb", 8)
    h, w = img.shape[:2]
    rect = crop_to_pixels(effective_crop(e.crop, e.straighten, w, h), w, h)
    return np.ascontiguousarray(rotate_crop(img, e.straighten, rect, order=1))


# ==========================================
# --- PREVIEW RENDERING (pure numpy; runs on the preview thread) ---
# ==========================================

def clipping_overlay(img, oog=None):
    """Blown highlights red, crushed blacks blue and, when a mask is given,
    colours outside the export colour space magenta."""
    out = img.copy()
    if oog is not None and oog.shape == img.shape[:2]:
        out[oog] = (230, 60, 230)
    out[(img >= 255).any(axis=2)] = (255, 45, 45)
    out[(img <= 0).all(axis=2)] = (45, 110, 255)
    return out


def decode_trc(enc, space):
    """Inverse of encode_trc: encoded [0,1] -> linear."""
    trc = COLOR_SPACES[space]["trc"]
    v = np.clip(enc, 0.0, 1.0).astype(np.float32)
    if trc[0] == "linear":
        return v
    if trc[0] == "srgb":
        return np.where(v <= 0.04045, v / 12.92, np.power((v + 0.055) / 1.055, 2.4)).astype(np.float32)
    if trc[0] == "rec709":
        return np.where(v < 0.081, v / 4.5, np.power((v + 0.099) / 1.099, 1.0 / 0.45)).astype(np.float32)
    return np.power(v, np.float32(trc[1])).astype(np.float32)


def export_to_display(arr, opts):
    """What a screen shows for an exported picture: the stored values decoded
    with the export space's curve, converted to sRGB, 8-bit."""
    space = opts.effective_space()
    if opts.fmt == "exr":
        lin = np.asarray(arr, dtype=np.float32)
    elif arr.dtype == np.float32:
        lin = decode_trc(arr, space)
    else:
        lin = decode_trc(arr.astype(np.float32) / (255.0 if arr.dtype == np.uint8 else 65535.0), space)
    if space not in ("srgb", "linsrgb"):
        to_srgb = np.linalg.inv(srgb_to_rgb(space).astype(np.float64)).astype(np.float32)
        lin = (lin.reshape(-1, 3) @ to_srgb.T).reshape(lin.shape)
    return encode_quantise(np.clip(lin, 0.0, 1.0), "srgb", 8)


def proof_export(lin, opts):
    """Run a developed picture (linear, export space, already at the export's
    size) through the export's encoding: bit depth, colour space, and the real
    encoder at its settings, then back. Returns the picture as the file will
    show it (8-bit sRGB), the same without the compression, the encoded size in
    bytes (lossy formats; None for lossless ones) and any quality a target file
    size chose."""
    enc = finish_export(lin, opts)
    clean = export_to_display(enc, opts)
    if not format_lossy(opts):
        return clean, clean, None, None
    data, used = encode_fitting(enc, opts, None)
    shown = export_to_display(decode_image(data, opts, enc), opts)
    return shown, clean, len(data), used


def develop_preview(dn, sensor, edit, parallel=True):
    """RGBE frame in DN -> developed 8-bit sRGB picture, before rotation and crop."""
    rgbe = normalise_rgbe(dn, black_for(sensor, edit.black_mode), sensor["white"])
    scale = dn.shape[1] / float(sensor["cfa"].shape[1])
    lin = develop_finished(rgbe, edit, wb_from_temp_tint(edit.temp, edit.tint), output_matrix(edit, "srgb"),
                           dsp=sony_dsp_setup(sensor, edit), scale=scale, parallel=parallel,
                           scale12=dsp_input_scale(sensor, edit))
    return encode_quantise(lin, "srgb", 8)


def compose_view(base8, edit, frame, cropping=False, clip=False):
    """Orientation, straighten, crop and overlays on a developed picture.
    frame = (width, height) of the oriented picture at full resolution.
    Returns what the viewer needs: 'img' (the picture), 'view' (the picture with
    any overlay), its full-resolution size, and the crop that was applied."""
    img = apply_orientation(base8, edit.rot_k, edit.flip)
    h, w = img.shape[:2]
    fw, fh = frame
    eff = effective_crop(edit.crop, edit.straighten, fw, fh)
    crop_size = (max(1, round((eff[2] - eff[0]) * fw)), max(1, round((eff[3] - eff[1]) * fh)))
    if cropping:                                   # whole frame, the crop is drawn on top by the viewer
        img = rotate_crop(img, edit.straighten, (0, 0, w, h), order=1, edge="constant")
        shown = (fw, fh)
    else:
        img = rotate_crop(img, edit.straighten, crop_to_pixels(eff, w, h), order=1)
        shown = crop_size
    img = np.ascontiguousarray(img)
    return {"img": img, "view": clipping_overlay(img) if clip else img, "frame": shown,
            "crop_size": crop_size, "crop": eff, "cropping": cropping, "proof": None}


def proof_level(edit, frame, opts):
    """The preview level an export preview renders at, and the export's size:
    (level, width, height). It is the export's own resolution, so that resizing,
    sharpening and compression act on exactly the pixels the file will hold."""
    fw, fh = frame
    if opts.resolution == "half":                   # the export crops the 2x2-binned frame
        fw, fh = fw // 2, fh // 2
    x0, y0, x1, y1 = crop_to_pixels(effective_crop(edit.crop, edit.straighten, fw, fh), fw, fh)
    cw, ch = max(1, x1 - x0), max(1, y1 - y0)
    if opts.resolution == "half":
        ow, oh = export_size(cw, ch, opts)
        return "half", ow, oh
    ow, oh = export_size(cw, ch, opts)
    return "full", ow, oh


PROOF_VIEWS = [("export", "The export"), ("difference", "What the compression changed (x8)")]


class PreviewRenderer:
    """Does the heavy preview work for the preview thread and remembers what is
    expensive to rebuild between jobs: the demosaiced frame of the open image
    (full size, and the smaller preview frames averaged down from it) and the
    developed picture. Only ever used from that one thread."""

    def __init__(self):
        self.forget()

    def forget(self):
        self._dem_key = self._dem = None
        self._full_key = self._full = None
        self._frame_key = self._frame = None
        self._base_key = self._base = None
        self._proof_key = self._proof = None

    def _full_frame(self, sensor, token, e):
        """The full-size RGBE frame (DN). The demosaic is kept on its own, so
        moving the chromatic aberration sliders only redoes the CA fix."""
        key = (token, e.demosaic, e.hot_pixels, e.ca_red, e.ca_blue)
        if key != self._full_key:
            self._full = self._frame = self._base = self._proof = None      # free the old image first
            self._full_key = self._frame_key = self._base_key = self._proof_key = None
            if key[:3] != self._dem_key:
                self._dem = None
                self._dem = sensor_rgbe_dn(sensor, "full", e.demosaic, e.hot_pixels)
                self._dem_key = key[:3]
            if abs(e.ca_red) >= 1e-3 or abs(e.ca_blue) >= 1e-3:
                self._full = correct_lateral_ca(self._dem.copy(), e.ca_red, e.ca_blue)
            else:
                self._full = self._dem
            self._full_key = key
        return self._full

    def _frame_dn(self, sensor, token, level, e, binning=False):
        """The RGBE frame (DN) at `level`. Previews average the full demosaic
        down; a 'half resolution' export is previewed with its own 2x2 binning."""
        key = (token, level, binning, e.demosaic, e.hot_pixels, e.ca_red, e.ca_blue)
        if key != self._frame_key:
            self._frame = self._base = self._proof = None
            self._base_key = self._proof_key = None
            if binning:
                self._frame = sensor_rgbe_dn(sensor, "half", hot_pixels=e.hot_pixels)
            else:
                self._frame = frame_from_full(self._full_frame(sensor, token, e), level)
            self._frame_key = key
        return self._frame

    @staticmethod
    def _develop_key(e):
        return (e.black_mode, e.wb_preset, e.temp, e.tint, e.exposure, e.black_point, e.rolloff,
                e.highlights, e.ir, e.render, e.sony_curve, e.chroma_nr, e.defringe)

    def render(self, job):
        sensor, e, level = job["sensor"], job["edit"], job["level"]
        opts = job.get("proof")
        if opts is not None and opts.fmt != "dng" and not job["options"].get("cropping"):
            return self._render_proof(job)
        frame = self._frame_dn(sensor, job["token"], level, e)
        base_key = (self._frame_key, self._develop_key(e))
        if base_key != self._base_key:
            self._base = develop_preview(frame, sensor, e)
            self._base_key = base_key
        out = compose_view(self._base, e, job["frame"], **job["options"])
        out.update(gen=job["gen"], token=job["token"], level=level)
        return out

    def _render_proof(self, job):
        """The export preview: the picture as the export file will show it."""
        sensor, e, opts = job["sensor"], job["edit"], job["proof"]
        level, ow, oh = proof_level(e, job["frame"], opts)
        frame = self._frame_dn(sensor, job["token"], level, e, binning=opts.resolution == "half")
        space = opts.effective_space()
        key = (self._frame_key, self._develop_key(e), space)
        if key != self._proof_key:
            rgbe = normalise_rgbe(frame, black_for(sensor, e.black_mode), sensor["white"])
            oog = []
            scale = frame.shape[1] / float(sensor["cfa"].shape[1])
            lin = develop_finished(rgbe, e, wb_from_temp_tint(e.temp, e.tint), output_matrix(e, space),
                                   dsp=sony_dsp_setup(sensor, e), scale=scale, oog=oog,
                                   scale12=dsp_input_scale(sensor, e), exact=level == "full")
            self._proof = (lin, oog[0] if oog else None)
            self._proof_key = key
        lin, oog = self._proof

        # orientation, straighten and crop, as the export does them
        img = apply_orientation(lin, e.rot_k, e.flip)
        mask = apply_orientation(oog, e.rot_k, e.flip) if oog is not None else None
        h, w = img.shape[:2]
        fw, fh = job["frame"]
        eff = effective_crop(e.crop, e.straighten, fw, fh)
        rect = crop_to_pixels(eff, w, h)
        img = np.ascontiguousarray(rotate_crop(img, e.straighten, rect, order=3 if level == "full" else 1))
        np.clip(img, 0.0, 1.0, out=img)
        if mask is not None:
            mask = rotate_crop(mask.astype(np.float32), e.straighten, rect, order=0) > 0.5
        if (img.shape[1], img.shape[0]) != (ow, oh):
            img = resize_linear(img, ow, oh, opts.resample)
            if mask is not None:
                mask = np.asarray(Image.fromarray(mask).resize((ow, oh), Image.Resampling.NEAREST))
        if opts.sharpen != "none":
            img = output_sharpen(img, opts.sharpen, opts.sharpen_amount)

        shown, clean, nbytes, used = proof_export(img, opts)
        if job.get("proof_view") == "difference":
            diff = np.abs(shown.astype(np.int16) - clean.astype(np.int16)) * 8
            shown = np.clip(diff, 0, 255).astype(np.uint8)
        crop_size = (max(1, round((eff[2] - eff[0]) * fw)), max(1, round((eff[3] - eff[1]) * fh)))
        clip = job["options"].get("clip")
        return {"img": shown, "view": clipping_overlay(shown, mask) if clip else shown,
                "frame": (ow, oh), "crop_size": crop_size, "crop": eff, "cropping": False,
                "proof": {"bytes": nbytes, "quality": used, "size": (ow, oh), "level": level,
                          "fmt": opts.fmt, "space": space, "target_kb": opts.target_kb,
                          "out_of_gamut": float(mask.mean()) if mask is not None else 0.0},
                "gen": job["gen"], "token": job["token"], "level": level}


# =====================================================================
# PART 2  -  INTERFACE
# =====================================================================

ACCENT = "#0A84FF"
TEXT = "#ECECF1"


def array_to_pixmap(arr):
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape[:2]
    buf = arr.tobytes()                      # must stay alive until the pixmap has copied it
    return QPixmap.fromImage(QImage(buf, w, h, 3 * w, QImage.Format.Format_RGB888))


def dark_palette():
    pal = QPalette()
    role = QPalette.ColorRole
    for r, col in ((role.Window, "#15161B"), (role.WindowText, TEXT), (role.Base, "#1B1C22"),
                   (role.AlternateBase, "#20222A"), (role.ToolTipBase, "#1C1D22"),
                   (role.ToolTipText, TEXT), (role.Text, TEXT), (role.Button, "#24262E"),
                   (role.ButtonText, TEXT), (role.BrightText, "#FFFFFF"),
                   (role.Highlight, ACCENT), (role.HighlightedText, "#FFFFFF"),
                   (role.Link, "#5AB0FF"), (role.PlaceholderText, "#8A8B94")):
        pal.setColor(r, QColor(col))
    for r in (role.Text, role.ButtonText, role.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, r, QColor("#6C6D76"))
    return pal


def _arrow_image_path():
    """Small down-arrow for combo boxes (Qt style sheets need it as an image file)."""
    path = os.path.join(tempfile.gettempdir(), "f828_converter_arrow.png")
    try:
        pix = QPixmap(20, 12)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(236, 236, 241, 200))
        p.drawPolygon(QPolygonF([QPointF(4.0, 3.0), QPointF(16.0, 3.0), QPointF(10.0, 10.0)]))
        p.end()
        pix.save(path, "PNG")
    except Exception:
        pass
    return path.replace("\\", "/")


def _check_image_path():
    """White tick for the library's check boxes."""
    path = os.path.join(tempfile.gettempdir(), "f828_converter_check.png")
    try:
        pix = QPixmap(32, 32)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(255, 255, 255), 4.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(7.0, 16.5), QPointF(13.5, 23.0), QPointF(25.0, 9.5)]))
        p.end()
        pix.save(path, "PNG")
    except Exception:
        pass
    return path.replace("\\", "/")


def build_stylesheet():
    arrow = _arrow_image_path()
    check = _check_image_path()
    # Qt 6.12 squeezes the text field of a spin box without buttons to 1 px when a style sheet
    # sets its padding, so every number box shows blank. Anchoring the hidden buttons to the
    # border gives the text its width back (with less left padding). Earlier Qt needs nothing.
    qt = tuple(int(p) for p in qVersion().split(".")[:2])
    spin_fix = ("QAbstractSpinBox::up-button, QAbstractSpinBox::down-button "
                "{ width: 0px; subcontrol-origin: border; }" if qt >= (6, 12) else "")
    return f"""
    QWidget {{ color: {TEXT}; font-family: "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Inter", "Helvetica Neue", sans-serif; font-size: 13px; }}
    QDialog {{ background: #16171C; }}
    QToolTip {{ background: #1C1D22; color: {TEXT}; border: 1px solid rgba(255,255,255,45); padding: 4px 8px; }}
    QLabel {{ background: transparent; }}
    QLabel#h1 {{ font-size: 16px; font-weight: 600; }}
    QLabel#cardTitle {{ color: rgba(236,236,241,150); font-size: 11px; font-weight: 600; }}
    QLabel#muted {{ color: rgba(236,236,241,150); font-size: 12px; }}
    QLabel#mono {{ font-family: Consolas, "Cascadia Mono", "Courier New", monospace; font-size: 11px; color: rgba(236,236,241,185); }}
    QLabel#zoom {{ color: rgba(236,236,241,215); font-size: 12px; }}

    QFrame#glass {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(255,255,255,40), stop:0.03 rgba(255,255,255,20), stop:1 rgba(255,255,255,11));
        border: 1px solid rgba(255,255,255,42); border-radius: 20px; }}
    QFrame#card {{ background: rgba(255,255,255,13); border: 1px solid rgba(255,255,255,24); border-radius: 14px; }}
    QFrame#pill {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(70,72,84,190), stop:1 rgba(30,31,38,190));
        border: 1px solid rgba(255,255,255,60); border-radius: 21px; }}
    QFrame#status {{ background: rgba(30,31,38,200); border: 1px solid rgba(255,255,255,55); border-radius: 13px; }}
    QFrame#popover {{ background: rgba(27,28,35,236); border: 1px solid rgba(255,255,255,60); border-radius: 16px; }}
    QFrame#sep {{ background: rgba(255,255,255,45); border: none; }}
    QWidget#clear {{ background: transparent; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}

    QPushButton {{ background: rgba(255,255,255,22); border: 1px solid rgba(255,255,255,40); border-radius: 10px; padding: 6px 10px; }}
    QPushButton:hover {{ background: rgba(255,255,255,38); }}
    QPushButton:pressed {{ background: rgba(255,255,255,14); }}
    QPushButton:disabled {{ color: rgba(236,236,241,85); background: rgba(255,255,255,9); border-color: rgba(255,255,255,18); }}
    QPushButton#glyph {{ font-size: 17px; padding: 3px 4px; }}
    QPushButton#primary {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3D9DFF, stop:1 #0A6BE6);
        border: 1px solid rgba(255,255,255,80); border-radius: 12px; padding: 10px 14px; font-weight: 600; color: white; }}
    QPushButton#primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #57ABFF, stop:1 #1F7BF2); }}
    QPushButton#primary:disabled {{ background: rgba(10,132,255,60); color: rgba(255,255,255,110); border-color: rgba(255,255,255,25); }}

    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 10px; padding: 5px 10px; }}
    QToolButton:hover {{ background: rgba(255,255,255,30); }}
    QToolButton:pressed {{ background: rgba(255,255,255,16); }}
    QToolButton:checked {{ background: rgba(10,132,255,170); border-color: rgba(255,255,255,70); }}
    QToolButton:disabled {{ color: rgba(236,236,241,80); }}

    QComboBox {{ background: rgba(255,255,255,22); border: 1px solid rgba(255,255,255,40); border-radius: 9px; padding: 5px 10px; min-height: 18px; }}
    QComboBox:hover {{ background: rgba(255,255,255,34); }}
    QComboBox:disabled {{ color: rgba(236,236,241,85); }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none; }}
    QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 6px; }}
    QComboBox QAbstractItemView {{ background: #22232A; border: 1px solid rgba(255,255,255,50); selection-background-color: {ACCENT}; outline: none; padding: 4px; }}

    QLineEdit, QAbstractSpinBox {{ background: rgba(0,0,0,70); border: 1px solid rgba(255,255,255,32); border-radius: 8px; padding: 4px 8px; selection-background-color: {ACCENT}; }}
    QLineEdit:focus, QAbstractSpinBox:focus {{ border-color: {ACCENT}; }}
    {spin_fix}

    QSlider:horizontal {{ min-height: 22px; }}
    QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: rgba(255,255,255,48); }}
    QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {ACCENT}; }}
    QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 8px; background: #F6F6F8; border: 1px solid rgba(0,0,0,90); }}
    QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}
    QSlider::handle:horizontal:disabled {{ background: #8E8F97; }}
    QSlider#centered::sub-page:horizontal {{ background: rgba(255,255,255,48); }}
    QSlider#temp::groove:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3F86FF, stop:0.5 #EDEDED, stop:1 #FFB13B); }}
    QSlider#temp::sub-page:horizontal {{ background: transparent; }}
    QSlider#tint::groove:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #34C759, stop:0.5 #EDEDED, stop:1 #E24FD8); }}
    QSlider#tint::sub-page:horizontal {{ background: transparent; }}

    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item {{ border-radius: 10px; padding: 5px; margin: 1px 0; }}
    QListWidget::item:selected {{ background: rgba(10,132,255,120); color: white; }}
    QListWidget::item:hover:!selected {{ background: rgba(255,255,255,18); }}
    QListView::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid rgba(255,255,255,105); background: rgba(0,0,0,80); }}
    QListView::indicator:hover {{ border-color: rgba(255,255,255,180); }}
    QListView::indicator:checked {{ background: {ACCENT}; border-color: rgba(255,255,255,140); image: url("{check}"); }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: rgba(255,255,255,55); border-radius: 3px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,90); }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QSplitter::handle {{ background: transparent; }}
    QProgressBar {{ border: 1px solid rgba(255,255,255,40); border-radius: 7px; background: rgba(0,0,0,70); text-align: center; height: 14px; }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}
    """


# ---------------------------------------------------------------------
# Small reusable widgets
# ---------------------------------------------------------------------

class Switch(QAbstractButton):
    """An on/off pill switch."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setFixedSize(40, 22)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def sizeHint(self):
        return QSize(40, 22)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        track = QColor(10, 132, 255) if on else QColor(255, 255, 255, 50)
        knob = QColor(255, 255, 255)
        if not self.isEnabled():
            track.setAlpha(60)
            knob.setAlpha(120)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0.0, 0.0, 40.0, 22.0), 11.0, 11.0)
        p.setBrush(knob)
        p.drawEllipse(QRectF(20.0 if on else 2.0, 2.0, 18.0, 18.0))
        p.end()


class SwitchRow(QWidget):
    """A label with a switch on the right; clicking the label toggles it too."""
    toggled = Signal(bool)

    def __init__(self, text, checked=False, tip=""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.switch = Switch(checked)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.switch, 0)
        self.switch.toggled.connect(self.toggled.emit)
        if tip:
            self.setToolTip(tip)

    def isChecked(self):
        return self.switch.isChecked()

    def setChecked(self, value):
        self.switch.setChecked(bool(value))

    def mouseReleaseEvent(self, _event):
        if self.isEnabled():
            self.switch.toggle()


class ParamSlider(QWidget):
    """Title + editable number on one line, slider underneath.
    Double-click the title to return to the default value."""
    changed = Signal(float)

    def __init__(self, title, lo, hi, default, step=1.0, decimals=0, suffix="", style="",
                 to_value=None, to_frac=None, positions=None, tip=""):
        super().__init__()
        self._lo, self._hi, self._default, self._step = float(lo), float(hi), float(default), float(step)
        self._to_value, self._to_frac = to_value, to_frac
        self._n = int(positions or round((hi - lo) / step))
        self._value = float(default)
        self._busy = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        top = QHBoxLayout()
        self.title = QLabel(title)
        self.spin = QDoubleSpinBox()
        self.spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.spin.setDecimals(int(decimals))
        self.spin.setRange(self._lo, self._hi)
        self.spin.setSingleStep(self._step)
        self.spin.setSuffix(suffix)
        self.spin.setKeyboardTracking(False)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.spin.setFixedWidth(84)
        top.addWidget(self.title, 1)
        top.addWidget(self.spin, 0)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, self._n)
        if style:
            self.slider.setObjectName(style)
        lay.addWidget(self.slider)
        self.setToolTip(tip or "Double-click the name to reset")

        self.set_value(default)
        self.slider.valueChanged.connect(self._slider_moved)
        self.spin.valueChanged.connect(self._spin_edited)

    def value(self):
        return self._value

    def is_dragging(self):
        return self.slider.isSliderDown()

    def set_range(self, lo, hi, suffix=None):
        """New limits (for a linear slider) without emitting `changed`."""
        self._lo, self._hi = float(lo), float(hi)
        self._default = min(max(self._default, self._lo), self._hi)
        self._n = max(1, int(round((self._hi - self._lo) / self._step)))
        self._busy = True
        try:
            self.slider.setRange(0, self._n)
            self.spin.setRange(self._lo, self._hi)
            if suffix is not None:
                self.spin.setSuffix(suffix)
        finally:
            self._busy = False
        self.set_value(self._value)

    def _frac(self, v):
        if self._to_frac:
            return self._to_frac(v)
        return (v - self._lo) / (self._hi - self._lo)

    def _from_frac(self, f):
        v = self._to_value(f) if self._to_value else self._lo + (self._hi - self._lo) * f
        v = round(v / self._step) * self._step
        return min(max(v, self._lo), self._hi)

    def set_value(self, v, emit=False):
        v = self._default if v is None else float(v)
        v = min(max(v, self._lo), self._hi)
        self._value = v
        self._busy = True
        try:
            self.slider.setValue(int(round(self._frac(v) * self._n)))
            self.spin.setValue(v)
        finally:
            self._busy = False
        if emit:
            self.changed.emit(v)

    def _slider_moved(self, pos):
        if self._busy:
            return
        self._value = self._from_frac(pos / float(self._n))
        self._busy = True
        try:
            self.spin.setValue(self._value)
        finally:
            self._busy = False
        self.changed.emit(self._value)

    def _spin_edited(self, v):
        if self._busy:
            return
        self._value = float(v)
        self._busy = True
        try:
            self.slider.setValue(int(round(self._frac(self._value) * self._n)))
        finally:
            self._busy = False
        self.changed.emit(self._value)

    def mouseDoubleClickEvent(self, _event):
        if self.isEnabled():
            self.set_value(self._default, emit=True)


def make_card(title=""):
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 12, 14, 14)
    lay.setSpacing(9)
    if title:
        label = QLabel(title.upper())
        label.setObjectName("cardTitle")
        lay.addWidget(label)
    return frame, lay


def muted_label(text="", mono=False):
    label = QLabel(text)
    label.setObjectName("mono" if mono else "muted")
    label.setWordWrap(not mono)
    if mono:
        label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def compact(combo):
    """Let a combo box be narrower than its longest entry (otherwise one long
    entry forces the whole side panel wider than the window allows)."""
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(8)
    return combo


def labelled_row(text, widget, label_width=96):
    row = QHBoxLayout()
    label = QLabel(text)
    label.setFixedWidth(label_width)
    row.addWidget(label, 0)
    row.addWidget(widget, 1)
    return row


class Histogram(QWidget):
    """Additive RGB histogram of what is currently shown (square-root scaled)."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(104)
        self._curves = None

    def set_image(self, img8):
        if img8 is None or img8.size == 0:
            self._curves = None
        else:
            step = max(1, int(math.sqrt(img8.shape[0] * img8.shape[1] / 250_000.0)))
            px = img8[::step, ::step].reshape(-1, 3)
            hist = np.stack([np.bincount(px[:, c], minlength=256)[:256] for c in range(3)]).astype(np.float64)
            peak = max(1.0, float(hist[:, 2:254].max()))        # ignore the clipped spikes when scaling
            self._curves = np.sqrt(np.minimum(hist / peak, 1.0))
        self.update()

    def paintEvent(self, _event):
        w, h = float(self.width()), float(self.height())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QPainterPath()
        box.addRoundedRect(QRectF(0.0, 0.0, w, h), 9.0, 9.0)
        p.fillPath(box, QBrush(QColor(0, 0, 0, 95)))
        p.setClipPath(box)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        for i in (1, 2, 3):
            p.drawLine(QPointF(w * i / 4.0, 0.0), QPointF(w * i / 4.0, h))
        if self._curves is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            for c, colour in enumerate((QColor(235, 60, 60, 215), QColor(60, 215, 90, 215), QColor(70, 120, 255, 215))):
                path = QPainterPath()
                path.moveTo(0.0, h)
                for i in range(256):
                    path.lineTo(w * i / 255.0, h - float(self._curves[c, i]) * (h - 4.0))
                path.lineTo(w, h)
                path.closeSubpath()
                p.fillPath(path, QBrush(colour))
        p.end()


# ---------------------------------------------------------------------
# Image viewer: zoom, drag-to-pan, crop overlay, white-balance picker
# ---------------------------------------------------------------------

class ImageView(QWidget):
    zoomChanged = Signal(float)          # percent, 100 = one screen pixel per full-res pixel
    cropChanged = Signal(object)         # (x0, y0, x1, y1) while dragging
    cropFinished = Signal()
    pointPicked = Signal(float, float)   # fractions of the displayed image
    applyRequested = Signal()            # Enter / Esc / double-click inside the crop frame

    HANDLE = 14.0
    MIN_CROP = 0.03

    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pix = None
        self._iw = self._ih = 1              # size of the pixmap
        self._fw = self._fh = 1              # size of the same picture at full resolution
        self._zoom = 1.0                     # device pixels per full-resolution pixel
        self._cx = self._cy = 0.5            # which part of the picture sits in the middle
        self._fit = True
        self.tool = "pan"
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self._aspect_k = None
        self._drag = None                    # ("pan"|"crop"|"pick", ...)
        self._message = "Add .SRF files with the buttons on the left,\nor drop files and folders anywhere in this window."
        self._apply_cursor(None)

    # ----- geometry (plain floats, logical pixels) -----
    def _dpr(self):
        try:
            return max(1.0, float(self.devicePixelRatioF()))
        except Exception:
            return 1.0

    def _pad(self):
        return 44.0 if self.tool == "crop" else 22.0

    def _fit_zoom(self):
        pad = self._pad()
        avail_w = max(32.0, self.width() - 2.0 * pad)
        avail_h = max(32.0, self.height() - 2.0 * pad)
        return min(avail_w / self._fw, avail_h / self._fh) * self._dpr()

    def _image_rect(self):
        s = self._zoom / self._dpr()
        w, h = self._fw * s, self._fh * s
        return self.width() / 2.0 - self._cx * w, self.height() / 2.0 - self._cy * h, w, h

    def _clamp_centre(self):
        s = self._zoom / self._dpr()
        for axis, (size, view) in enumerate(((self._fw * s, float(self.width())),
                                             (self._fh * s, float(self.height())))):
            if size <= view:
                c = 0.5
            else:
                half = 0.5 * view / size
                c = min(max(self._cx if axis == 0 else self._cy, half), 1.0 - half)
            if axis == 0:
                self._cx = c
            else:
                self._cy = c

    # ----- public API -----
    def has_image(self):
        return self._pix is not None

    def set_message(self, text):
        self._message = text
        self.update()

    def clear(self):
        self._pix = None
        self.update()

    def set_image(self, arr, full_w, full_h):
        """Show `arr` (uint8 RGB). full_w x full_h is the size this picture has at
        full resolution, so zoom percentages stay true at any preview resolution."""
        self._pix = array_to_pixmap(arr)
        self._ih, self._iw = int(arr.shape[0]), int(arr.shape[1])
        resized = (int(full_w), int(full_h)) != (self._fw, self._fh)
        self._fw, self._fh = max(1, int(full_w)), max(1, int(full_h))
        if self._fit or resized:
            self._fit = True
            self._zoom = self._fit_zoom()
            self._cx = self._cy = 0.5
            self.zoomChanged.emit(self._zoom * 100.0)
        self._clamp_centre()
        self.update()

    def set_tool(self, tool):
        self.tool = tool
        self._drag = None
        self._apply_cursor(None)
        self.update()

    def set_crop(self, crop):
        self._crop = tuple(float(v) for v in crop)
        self.update()

    def crop(self):
        return self._crop

    def set_aspect(self, k):
        """k = (crop height / frame height) / (crop width / frame width), or None for free."""
        self._aspect_k = k

    def zoom_percent(self):
        return self._zoom * 100.0

    def request_fit(self):
        """Fit the next picture that is shown (used when its shape is about to change)."""
        self._fit = True

    def fit(self):
        self._fit = True
        self._zoom = self._fit_zoom()
        self._cx = self._cy = 0.5
        self.zoomChanged.emit(self._zoom * 100.0)
        self.update()

    def set_zoom(self, zoom, at=None):
        """Zoom keeping the point under `at` (widget x, y) fixed; default is the centre."""
        if self._pix is None:
            return
        fit = self._fit_zoom()
        zoom = min(max(zoom, min(fit, 1.0)), 16.0)       # never smaller than "fit" (or 100% for tiny crops)
        ax, ay = at if at else (self.width() / 2.0, self.height() / 2.0)
        left, top, w, h = self._image_rect()
        u, v = (ax - left) / w, (ay - top) / h
        self._zoom = zoom
        self._fit = False
        s = zoom / self._dpr()
        w, h = self._fw * s, self._fh * s
        self._cx = (self.width() / 2.0 - (ax - u * w)) / w
        self._cy = (self.height() / 2.0 - (ay - v * h)) / h
        if abs(zoom - fit) <= fit * 0.001:
            self._zoom = fit
            self._fit = True
            self._cx = self._cy = 0.5
        self._clamp_centre()
        self.zoomChanged.emit(self._zoom * 100.0)
        self.update()

    def zoom_step(self, direction, at=None):
        self.set_zoom(self._zoom * (1.25 if direction > 0 else 0.8), at)

    def actual_size(self, at=None):
        self.set_zoom(1.0, at)

    # ----- painting -----
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        clip = QPainterPath()
        clip.addRoundedRect(area, 20.0, 20.0)
        p.setClipPath(clip)
        p.fillRect(area, QColor(0, 0, 0, 120))
        if self._pix is None:
            p.setPen(QColor(236, 236, 241, 140))
            p.drawText(area, self._message, QTextOption(Qt.AlignmentFlag.AlignCenter))
            p.end()
            return
        left, top, w, h = self._image_rect()
        # smooth when shrinking or mildly enlarging; crisp pixels when zoomed far in
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, (w / self._iw) < 3.0)
        p.drawPixmap(QRectF(left, top, w, h), self._pix, QRectF(0.0, 0.0, float(self._iw), float(self._ih)))
        if self.tool == "crop":
            self._paint_crop(p, left, top, w, h)
        p.end()

    def _crop_rect_px(self):
        left, top, w, h = self._image_rect()
        x0, y0, x1, y1 = self._crop
        return left + x0 * w, top + y0 * h, left + x1 * w, top + y1 * h

    def _paint_crop(self, p, left, top, w, h):
        cx0, cy0, cx1, cy1 = self._crop_rect_px()
        shade = QColor(0, 0, 0, 160)
        p.fillRect(QRectF(left, top, w, cy0 - top), shade)
        p.fillRect(QRectF(left, cy1, w, top + h - cy1), shade)
        p.fillRect(QRectF(left, cy0, cx0 - left, cy1 - cy0), shade)
        p.fillRect(QRectF(cx1, cy0, left + w - cx1, cy1 - cy0), shade)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 75), 1.0))
        for i in (1, 2):
            x = cx0 + (cx1 - cx0) * i / 3.0
            y = cy0 + (cy1 - cy0) * i / 3.0
            p.drawLine(QPointF(x, cy0), QPointF(x, cy1))
            p.drawLine(QPointF(cx0, y), QPointF(cx1, y))
        p.setPen(QPen(QColor(255, 255, 255, 235), 1.5))
        p.drawRect(QRectF(cx0, cy0, cx1 - cx0, cy1 - cy0))
        p.setPen(QPen(QColor(255, 255, 255), 3.5))
        n = min(18.0, (cx1 - cx0) / 3.0, (cy1 - cy0) / 3.0)
        for x, sx in ((cx0, 1.0), (cx1, -1.0)):
            for y, sy in ((cy0, 1.0), (cy1, -1.0)):
                p.drawLine(QPointF(x, y), QPointF(x + sx * n, y))
                p.drawLine(QPointF(x, y), QPointF(x, y + sy * n))
        mx, my = 0.5 * (cx0 + cx1), 0.5 * (cy0 + cy1)
        p.drawLine(QPointF(mx - n / 2.0, cy0), QPointF(mx + n / 2.0, cy0))
        p.drawLine(QPointF(mx - n / 2.0, cy1), QPointF(mx + n / 2.0, cy1))
        p.drawLine(QPointF(cx0, my - n / 2.0), QPointF(cx0, my + n / 2.0))
        p.drawLine(QPointF(cx1, my - n / 2.0), QPointF(cx1, my + n / 2.0))

    # ----- mouse -----
    def _hit(self, x, y):
        """Which part of the crop frame is under the pointer: a handle name, 'move' or None."""
        cx0, cy0, cx1, cy1 = self._crop_rect_px()
        r = self.HANDLE
        if x < cx0 - r or x > cx1 + r or y < cy0 - r or y > cy1 + r:
            return None
        name = ""
        if abs(y - cy0) <= r:
            name += "t"
        elif abs(y - cy1) <= r:
            name += "b"
        if abs(x - cx0) <= r:
            name += "l"
        elif abs(x - cx1) <= r:
            name += "r"
        return name or "move"

    def _apply_cursor(self, hit):
        shape = Qt.CursorShape.OpenHandCursor
        if self._drag and self._drag[0] == "pan":
            shape = Qt.CursorShape.ClosedHandCursor
        elif self.tool == "pick":
            shape = Qt.CursorShape.CrossCursor
        elif self.tool == "crop" and hit:
            shape = {"move": Qt.CursorShape.SizeAllCursor,
                     "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
                     "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                     "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                     "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor}[hit]
        self.setCursor(QCursor(shape))

    def mousePressEvent(self, event):
        if self._pix is None:
            return
        pos = event.position()
        x, y = pos.x(), pos.y()
        button = event.button()
        if button == Qt.MouseButton.LeftButton and self.tool == "crop":
            hit = self._hit(x, y)
            if hit:
                self._drag = ("crop", hit, x, y, self._crop)
                return
        if button == Qt.MouseButton.LeftButton and self.tool == "pick":
            self._drag = ("pick", x, y)
            return
        if button in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._drag = ("pan", x, y, self._cx, self._cy)
            self._apply_cursor(None)

    def mouseMoveEvent(self, event):
        pos = event.position()
        x, y = pos.x(), pos.y()
        if not self._drag:
            if self._pix is not None:
                self._apply_cursor(self._hit(x, y) if self.tool == "crop" else None)
            return
        left, top, w, h = self._image_rect()
        if self._drag[0] == "pan":
            _, sx, sy, cx, cy = self._drag
            self._cx, self._cy = cx - (x - sx) / w, cy - (y - sy) / h
            self._fit = False
            self._clamp_centre()
            self.update()
        elif self._drag[0] == "crop":
            _, hit, sx, sy, start = self._drag
            self._crop = self._dragged_crop(hit, start, (x - sx) / w, (y - sy) / h)
            self.cropChanged.emit(self._crop)
            self.update()

    def _dragged_crop(self, hit, start, du, dv):
        x0, y0, x1, y1 = start
        m = self.MIN_CROP
        if hit == "move":
            du = min(max(du, -x0), 1.0 - x1)
            dv = min(max(dv, -y0), 1.0 - y1)
            return (x0 + du, y0 + dv, x1 + du, y1 + dv)
        if "l" in hit:
            x0 = min(max(x0 + du, 0.0), x1 - m)
        if "r" in hit:
            x1 = max(min(x1 + du, 1.0), x0 + m)
        if "t" in hit:
            y0 = min(max(y0 + dv, 0.0), y1 - m)
        if "b" in hit:
            y1 = max(min(y1 + dv, 1.0), y0 + m)
        rect = (x0, y0, x1, y1)
        return constrain_aspect(rect, hit, self._aspect_k) if self._aspect_k else rect

    def mouseReleaseEvent(self, event):
        drag, self._drag = self._drag, None
        if not drag:
            return
        pos = event.position()
        x, y = pos.x(), pos.y()
        if drag[0] == "crop":
            self.cropFinished.emit()
        elif drag[0] == "pick" and abs(x - drag[1]) < 5 and abs(y - drag[2]) < 5:
            left, top, w, h = self._image_rect()
            u, v = (x - left) / w, (y - top) / h
            if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
                self.pointPicked.emit(u, v)
        self._apply_cursor(self._hit(x, y) if self.tool == "crop" else None)

    def mouseDoubleClickEvent(self, event):
        if self._pix is None or self.tool == "pick":
            return
        pos = event.position()
        if self.tool == "crop" and self._hit(pos.x(), pos.y()):
            self._drag = None
            self.applyRequested.emit()
            return
        if self._fit:
            self.actual_size((pos.x(), pos.y()))
        else:
            self.fit()

    def wheelEvent(self, event):
        if self._pix is None:
            return
        dy = event.angleDelta().y()
        if dy:
            pos = event.position()
            self.set_zoom(self._zoom * (1.2 ** (dy / 120.0)), (pos.x(), pos.y()))
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        wanted = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape)
        if self.tool != "pan" and any(key == k or key == getattr(k, "value", None) for k in wanted):
            self.applyRequested.emit()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._pix is not None:
            if self._fit:
                self._zoom = self._fit_zoom()
                self.zoomChanged.emit(self._zoom * 100.0)
            self._clamp_centre()


class ViewerArea(QWidget):
    """The image view with the floating glass controls laid over it."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 320)
        self.view = ImageView()
        self.view.setParent(self)

        # --- top toolbar ---
        self.toolbar = QFrame(self)
        self.toolbar.setObjectName("pill")
        self.toolbar.setFixedHeight(44)
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(8, 5, 8, 5)
        bar.setSpacing(2)

        def tool_button(text, tip, checkable=False):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            bar.addWidget(b)
            return b

        def separator():
            line = QFrame()
            line.setObjectName("sep")
            line.setFixedSize(1, 20)
            bar.addSpacing(5)
            bar.addWidget(line)
            bar.addSpacing(5)

        self.btn_pan = tool_button("Move", "Drag to move around the picture, scroll to zoom  (V)", True)
        self.btn_crop = tool_button("Crop", "Drag the frame to crop; switch tool to apply  (C)", True)
        self.btn_pick = tool_button("Pick white", "Click something that should be neutral grey  (W)", True)
        self.tools = QButtonGroup(self)
        self.tools.setExclusive(True)
        for b in (self.btn_pan, self.btn_crop, self.btn_pick):
            self.tools.addButton(b)
        self.btn_pan.setChecked(True)
        separator()
        self.btn_zoom_out = tool_button("\u2212", "Zoom out  (-)")
        self.lbl_zoom = QLabel("\u2013")
        self.lbl_zoom.setObjectName("zoom")
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_zoom.setFixedWidth(52)
        bar.addWidget(self.lbl_zoom)
        self.btn_zoom_in = tool_button("+", "Zoom in  (+)")
        self.btn_fit = tool_button("Fit", "Fit the picture in the window  (F)")
        self.btn_100 = tool_button("1:1", "One screen pixel per image pixel  (1)")
        separator()
        self.btn_clip = tool_button("Clipping", "Paint blown highlights red and crushed blacks blue  (J)", True)
        self.btn_preview = tool_button("Preview \u25BE", "Preview resolution and quality", True)

        # --- status pill, bottom left ---
        self.status = QFrame(self)
        self.status.setObjectName("status")
        self.status.setFixedHeight(30)
        sl = QHBoxLayout(self.status)
        sl.setContentsMargins(14, 0, 14, 0)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("zoom")
        sl.addWidget(self.lbl_status)
        self.status.hide()

        # --- preview quality popover, top right ---
        self.popover = QFrame(self)
        self.popover.setObjectName("popover")
        self.popover.setFixedWidth(336)
        pl = QVBoxLayout(self.popover)
        pl.setContentsMargins(16, 14, 16, 16)
        pl.setSpacing(9)
        head = QLabel("PREVIEW QUALITY")
        head.setObjectName("cardTitle")
        pl.addWidget(head)
        self.sl_res = QSlider(Qt.Orientation.Horizontal)
        self.sl_res.setRange(0, len(RESOLUTIONS) - 1)
        self.sl_res.setPageStep(1)
        self.sl_res.setObjectName("centered")
        pl.addWidget(QLabel("Resolution"))
        pl.addWidget(self.sl_res)
        self.lbl_res = muted_label("")
        pl.addWidget(self.lbl_res)
        self.sw_draft = SwitchRow("Draft while adjusting", True,
                                  "Use the fast 25% preview while a slider moves, then sharpen")
        pl.addWidget(self.sw_draft)
        self.btn_full = QPushButton("Full quality")
        self.btn_full.setToolTip("100% resolution, no draft step: every pixel exactly as a full-size "
                                 "lossless export holds it")
        pl.addWidget(self.btn_full)

        line = QFrame()
        line.setObjectName("sep")
        line.setFixedHeight(1)
        pl.addSpacing(4)
        pl.addWidget(line)
        pl.addSpacing(2)
        head = QLabel("EXPORT PREVIEW")
        head.setObjectName("cardTitle")
        pl.addWidget(head)
        self.sw_proof = SwitchRow(
            "Show the picture as the export file", False,
            "Renders the picture exactly as the export will hold it: its size, resampling, output "
            "sharpening, colour space, bit depth and compression, through the real encoder and back. "
            "At 1:1 one screen pixel is one pixel of the file.")
        pl.addWidget(self.sw_proof)
        self.cmb_proof_fmt = compact(QComboBox())
        self.cmb_proof_fmt.setToolTip("The export's file type (the same setting as in Export settings)")
        pl.addLayout(labelled_row("Format", self.cmb_proof_fmt, 70))
        self.sl_proof_q = ParamSlider("Quality", 1, 100, 92, tip="The export's quality setting")
        pl.addWidget(self.sl_proof_q)
        self.cmb_proof_view = compact(QComboBox())
        for key, label in PROOF_VIEWS:
            self.cmb_proof_view.addItem(label, key)
        self.cmb_proof_view.setToolTip("'What the compression changed' shows the difference between the "
                                       "compressed file and the same pixels uncompressed, 8 times brighter")
        pl.addLayout(labelled_row("Show", self.cmb_proof_view, 70))
        self.lbl_proof_opts = muted_label("")
        pl.addWidget(self.lbl_proof_opts)
        self.lbl_proof = muted_label("")
        pl.addWidget(self.lbl_proof)
        self.btn_export_settings = QPushButton("Export settings\u2026")
        self.btn_export_settings.setToolTip("Every export setting: size, sharpening, colour space, bit "
                                            "depth, compression, metadata, destination")
        pl.addWidget(self.btn_export_settings)
        self.popover.hide()
        self.btn_preview.toggled.connect(self._toggle_popover)

        self.view.zoomChanged.connect(lambda z: self.lbl_zoom.setText(f"{z:.0f}%"))
        self._place()

    def _toggle_popover(self, on):
        self.popover.setVisible(bool(on))
        self._place()

    def set_status(self, text):
        self.lbl_status.setText(text)
        self.status.setVisible(bool(text))
        self._place()

    def _place(self):
        w, h = self.width(), self.height()
        self.view.setGeometry(0, 0, w, h)
        self.toolbar.adjustSize()
        self.toolbar.move(max(8, (w - self.toolbar.width()) // 2), 14)
        self.status.adjustSize()
        self.status.move(16, h - self.status.height() - 16)
        self.popover.adjustSize()
        self.popover.move(max(8, w - self.popover.width() - 16), 66)
        for overlay in (self.toolbar, self.status, self.popover):
            overlay.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()


# ---------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------

class ExportDialog(QDialog):
    """Everything about the output file: type, bit depth, compression, colour
    space, size, sharpening, metadata and destination. With settings_only it
    edits the settings without exporting (the export preview uses them too)."""

    def __init__(self, parent, opts, heading, estimate=None, settings_only=False):
        super().__init__(parent)
        self.setWindowTitle("Export settings" if settings_only else "Export")
        self.setMinimumWidth(1000)
        self._estimate = estimate
        self._out_dir = opts.out_dir
        self._base = opts.copy()                 # anything this dialog has no control for is kept
        self._bits_wanted = int(opts.bits)       # the depth asked for, kept while a format cannot hold it
        self._space_wanted = opts.space          # ... and the colour space
        self._pct = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        top = QHBoxLayout()
        head = QLabel(heading)
        head.setObjectName("h1")
        head.setWordWrap(True)
        top.addWidget(head, 1)
        self.cmb_preset = compact(QComboBox())
        self.cmb_preset.addItem("Start from a preset…", -1)
        for i, (label, _values) in enumerate(EXPORT_PRESETS):
            self.cmb_preset.addItem(label, i)
        self.cmb_preset.setMinimumWidth(360)
        self.cmb_preset.setToolTip("Fills in the settings for a common use. The destination and the file "
                                   "names stay as they are.")
        top.addWidget(self.cmb_preset, 0)
        lay.addLayout(top)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(12)
        right.setSpacing(12)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        lay.addLayout(columns)

        def combo(items, tip=""):
            c = compact(QComboBox())
            for key, label in items:
                c.addItem(label, key)
            if tip:
                c.setToolTip(tip)
            return c

        def spin(lo, hi, step, suffix="", special="", tip=""):
            s = QSpinBox()
            s.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setSuffix(suffix)
            if special:
                s.setSpecialValueText(special)
            if tip:
                s.setToolTip(tip)
            return s

        def pair(a, b):
            box = QWidget()
            box.setObjectName("clear")
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            h.addWidget(a, 1)
            h.addWidget(b, 1)
            return box

        def row_widget(text, widget):
            box = QWidget()
            box.setObjectName("clear")
            r = labelled_row(text, widget)
            r.setContentsMargins(0, 0, 0, 0)
            box.setLayout(r)
            return box

        # ---- file type ----
        card, cl = make_card("File type and quality")
        self.cmb_fmt = compact(QComboBox())
        for key, (label, _ext, _bits) in EXPORT_FORMATS.items():
            ok = format_available(key)
            self.cmb_fmt.addItem(label if ok else f"{label}  (needs {FORMAT_NEEDS.get(key, 'imagecodecs')})", key)
            if not ok:
                self.cmb_fmt.model().item(self.cmb_fmt.count() - 1).setEnabled(False)
        cl.addLayout(labelled_row("Format", self.cmb_fmt))
        self.cmb_bits = compact(QComboBox())
        cl.addLayout(labelled_row("Bit depth", self.cmb_bits))
        self.stack = QStackedWidget()
        cl.addWidget(self.stack)
        self._pages = {}

        def page(key):
            wdg = QWidget()
            wdg.setObjectName("clear")
            v = QVBoxLayout(wdg)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(8)
            self.stack.addWidget(wdg)
            self._pages[key] = self.stack.count() - 1
            return v

        v = page("tiff")
        tiff_items = [("none", "None  (largest file, opens fastest)"),
                      ("deflate", "Deflate / ZIP  (lossless, smaller)")]
        if lzw_available():
            tiff_items.append(("lzw", "LZW  (lossless, widest compatibility)"))
        if zstd_available():
            tiff_items.append(("zstd", "Zstandard  (lossless, fast; newer programs only)"))
        self.cmb_tiff = combo(tiff_items)
        v.addLayout(labelled_row("Compression", self.cmb_tiff))
        v.addWidget(muted_label(
            "TIFF is lossless whatever the compression; it only changes the file size. 32-bit float "
            "is for HDR merging and very heavy editing." +
            ("" if lzw_available() else "  LZW and Zstandard appear once the optional 'imagecodecs' "
                                        "package is installed.")))

        v = page("dng")
        self.cmb_dng = combo([("linear", "Linear: demosaiced here, 4 colour planes (about 64 MB)"),
                              ("mosaic", "Mosaic: untouched sensor data, the raw converter demosaics "
                                         "(about 16 MB)")])
        v.addLayout(labelled_row("Contents", self.cmb_dng))
        self.cmb_dng_look = combo(DNG_LOOKS, "Embeds a camera profile fitted to Image Data Converter, so "
                                             "that Lightroom (or Camera Raw) renders the DNG with Sony's "
                                             "colour and tone curve, and sets the exposure to match IDC's. "
                                             "None: the raw converter's own profiles apply.")
        look_row = row_widget("Look", self.cmb_dng_look)
        look_row.setVisible(dng_looks_available())
        v.addWidget(look_row)
        self.sw_dng_profile = SwitchRow(
            "Make Lightroom use the embedded profile", True,
            "Names the camera 'Sony DSC-F828 RGBE (F828 Converter)', which Lightroom has no profile of "
            "its own for, so it has to use the profile in the file. Off: plain 'Sony DSC-F828', and "
            "Lightroom may prefer its built-in Adobe profile.")
        v.addWidget(self.sw_dng_profile)
        self.sw_dng_curve = SwitchRow(
            "Straight tone curve (render like this app)", False,
            "Embeds a linear tone curve so the raw converter adds no contrast of its own. Off: the "
            "raw converter's normal default curve. A Sony look brings its own curve instead.")
        v.addWidget(self.sw_dng_curve)
        v.addWidget(muted_label("A DNG stays raw. It carries your white balance, rotation and colour "
                                "matrices; a linear DNG also carries the demosaic, hot-pixel and "
                                "chromatic aberration fixes, crop and straighten. Exposure, black point, "
                                "roll-off, highlight reconstruction, IR mode, noise reduction and "
                                "defringe are left to the raw converter."))

        v = page("png")
        self.sl_png = ParamSlider("Compression effort", 1, 9, 6)
        v.addWidget(self.sl_png)
        v.addWidget(muted_label("PNG is always lossless. Higher effort = smaller file, slower to save."))

        v = page("jpeg")
        self.sl_jpeg = ParamSlider("Quality", 1, 100, 92)
        v.addWidget(self.sl_jpeg)
        self.cmb_sub = combo([("4:4:4", "4:4:4  (full colour detail, larger)"), ("4:2:2", "4:2:2"),
                              ("4:2:0", "4:2:0  (smallest)")])
        v.addLayout(labelled_row("Colour detail", self.cmb_sub))
        self.sw_prog = SwitchRow("Progressive (loads in passes on the web)")
        v.addWidget(self.sw_prog)

        v = page("webp")
        self.sl_webp = ParamSlider("Quality", 1, 100, 90)
        v.addWidget(self.sl_webp)
        self.sl_webp_effort = ParamSlider("Effort", 0, 6, 4,
                                          tip="Higher = smaller file at the same quality, slower to save")
        v.addWidget(self.sl_webp_effort)
        self.sw_webp_lossless = SwitchRow("Lossless")
        v.addWidget(self.sw_webp_lossless)
        v.addWidget(muted_label("WebP is 8-bit, and halves the colour resolution (4:2:0) when lossy."))

        v = page("avif")
        self.sl_avif = ParamSlider("Quality", 1, 100, 80)
        v.addWidget(self.sl_avif)
        self.sl_avif_speed = ParamSlider("Speed", 0, 10, 6,
                                         tip="Lower = smaller file at the same quality, much slower to save")
        v.addWidget(self.sl_avif_speed)
        self.cmb_avif_sub = combo([("4:4:4", "4:4:4  (full colour detail)"), ("4:2:0", "4:2:0  (smaller)")])
        v.addLayout(labelled_row("Colour detail", self.cmb_avif_sub))
        v.addWidget(muted_label("AVIF gives the smallest files at a given quality but is slow to encode."
                                + ("" if _ic_has("AVIF") else "  10 and 12-bit need the optional "
                                                             "'imagecodecs' package.")))

        v = page("jxl")
        self.sl_jxl = ParamSlider("Quality", 1, 100, 90, tip="90 looks the same as the original for most "
                                                             "pictures; 100 is near-lossless")
        v.addWidget(self.sl_jxl)
        self.sl_jxl_effort = ParamSlider("Effort", 1, 9, 7, tip="Higher = smaller file, slower to save")
        v.addWidget(self.sl_jxl_effort)
        self.sw_jxl_lossless = SwitchRow("Lossless")
        v.addWidget(self.sw_jxl_lossless)
        v.addWidget(muted_label("JPEG XL keeps 16-bit precision in a fraction of a TIFF's size. Photoshop, "
                                "Lightroom, macOS and iOS open it; some older programs do not."))

        v = page("heif")
        self.sl_heif = ParamSlider("Quality", 1, 100, 85)
        v.addWidget(self.sl_heif)
        self.cmb_heif_sub = combo([("4:2:0", "4:2:0  (what phones write)"), ("4:4:4", "4:4:4  (full colour detail)")])
        v.addLayout(labelled_row("Colour detail", self.cmb_heif_sub))
        v.addWidget(muted_label("HEIF / HEIC is what iPhones save. 10-bit keeps smooth skies free of banding."))

        v = page("jp2")
        self.sl_jp2 = ParamSlider("Quality", 20, 70, 45, suffix=" dB",
                                  tip="Target signal-to-noise ratio: 40 dB is good, 50 dB is very hard to "
                                      "tell from the original")
        v.addWidget(self.sl_jp2)
        self.sw_jp2_lossless = SwitchRow("Lossless")
        v.addWidget(self.sw_jp2_lossless)
        v.addWidget(muted_label("JPEG 2000 is used by archives and digital cinema."
                                + ("" if _ic_has("JPEG2K") else "  16-bit needs the optional "
                                                               "'imagecodecs' package.")))

        v = page("exr")
        self.cmb_exr = combo(EXR_COMPRESSIONS)
        v.addLayout(labelled_row("Compression", self.cmb_exr))
        v.addWidget(muted_label("OpenEXR stores linear light as floating point, for compositing, 3D and "
                                "HDR work. Its colour space sets the primaries written into the file."))

        v = page("bmp")
        v.addWidget(muted_label("BMP is uncompressed 8-bit sRGB, with no metadata or colour profile."))

        self.spin_target = spin(0, 1000000, 50, " KB", "Off",
                                "Lossy formats: use the highest quality (up to the one set above) whose "
                                "file fits in this size. Off: the quality as set.")
        cl.addLayout(labelled_row("Fit in", self.spin_target))
        left.addWidget(card)

        # ---- size and sharpening ----
        card, cl = make_card("Size and sharpening")
        self.cmb_res = combo([("full", "Full: every pixel (8 MP), using the image's demosaic"),
                              ("half", "Half: 2x2 pixel binning, no interpolation, no moiré (2 MP)")])
        cl.addLayout(labelled_row("Resolution", self.cmb_res))
        self.cmb_size_mode = combo(SIZE_MODES)
        self.spin_edge = spin(0, 20000, 100, " px", "Keep size",
                              "Shrink to this size. Nothing is ever enlarged, except by a percentage.")
        cl.addLayout(labelled_row("Resize", pair(self.cmb_size_mode, self.spin_edge)))
        self.cmb_resample = combo(RESAMPLE_MODES, "How pixels are combined when the picture is made smaller")
        cl.addLayout(labelled_row("Resampling", self.cmb_resample))
        self.cmb_sharpen = combo(SHARPEN_MODES)
        self.cmb_amount = combo(SHARPEN_AMOUNTS)
        cl.addLayout(labelled_row("Sharpen for", pair(self.cmb_sharpen, self.cmb_amount)))
        cl.addWidget(muted_label("Output sharpening runs after resizing, on brightness only, tuned for "
                                 "the screen or paper the picture is made for. The export preview shows it."))
        left.addWidget(card)
        left.addStretch(1)

        # ---- colour ----
        card, cl = make_card("Colour space")
        self.cmb_space = compact(QComboBox())
        cl.addLayout(labelled_row("Convert to", self.cmb_space))
        self.lbl_space = muted_label("")
        cl.addWidget(self.lbl_space)
        self.lbl_chain = muted_label("", mono=True)
        cl.addWidget(self.lbl_chain)
        self.sw_icc = SwitchRow("Embed the matching ICC profile", True,
                                "Without it, other programs have to guess the colour space (they assume sRGB)")
        cl.addWidget(self.sw_icc)
        right.addWidget(card)

        # ---- metadata ----
        card, cl = make_card("Metadata")
        self.sw_meta = SwitchRow("Copy EXIF from the raw file", True,
                                 "Camera, lens, exposure and date. Sony's private maker notes are left out "
                                 "because their internal pointers are only valid inside the .SRF.")
        cl.addWidget(self.sw_meta)
        self.sw_gps = SwitchRow("Leave out the GPS location", False,
                                "Keeps everything else and drops where the picture was taken. Worth "
                                "turning on for pictures you share.")
        cl.addWidget(self.sw_gps)
        self.spin_dpi = spin(0, 4800, 10, " ppi", "As in the raw file",
                             "Print resolution written into the file. It does not change the pixels.")
        cl.addLayout(labelled_row("Print ppi", self.spin_dpi))
        right.addWidget(card)

        # ---- destination ----
        card, cl = make_card("Save to")
        self.lbl_dest = muted_label("")
        cl.addWidget(self.lbl_dest)
        row = QHBoxLayout()
        self.btn_same = QPushButton("Next to each raw file")
        self.btn_choose = QPushButton("Choose folder…")
        row.addWidget(self.btn_same)
        row.addWidget(self.btn_choose)
        cl.addLayout(row)
        self.edit_suffix = QLineEdit()
        self.edit_suffix.setPlaceholderText("optional, e.g.  _edit")
        cl.addLayout(labelled_row("Name suffix", self.edit_suffix))
        self.sw_overwrite = SwitchRow("Replace files that already exist", False,
                                      "Off: a number is added to the name instead (DSC0001_1.tiff)")
        cl.addWidget(self.sw_overwrite)
        right.addWidget(card)
        right.addStretch(1)

        # ---- estimate + buttons ----
        row = QHBoxLayout()
        self.btn_estimate = QPushButton("Estimate file size")
        self.btn_estimate.setEnabled(estimate is not None)
        self.lbl_estimate = muted_label("")
        row.addWidget(self.btn_estimate, 0)
        row.addWidget(self.lbl_estimate, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Save settings" if settings_only else "Export")
        self.btn_ok.setObjectName("primary")
        self.btn_ok.setDefault(True)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_ok)
        lay.addLayout(row)

        self._load(opts)
        self.cmb_preset.currentIndexChanged.connect(self._preset_chosen)
        self.cmb_fmt.currentIndexChanged.connect(self._format_changed)
        self.cmb_bits.currentIndexChanged.connect(self._bits_changed)
        self.cmb_space.currentIndexChanged.connect(self._space_changed)
        self.cmb_size_mode.currentIndexChanged.connect(self._size_mode_changed)
        for wdg in (self.sw_webp_lossless, self.sw_jxl_lossless, self.sw_jp2_lossless, self.sw_meta):
            wdg.toggled.connect(self._refresh_notes)
        for wdg in (self.cmb_exr, self.cmb_sharpen, self.cmb_res):
            wdg.currentIndexChanged.connect(self._refresh_notes)
        self.spin_edge.valueChanged.connect(self._refresh_notes)
        self.btn_same.clicked.connect(self._use_same_folder)
        self.btn_choose.clicked.connect(self._choose_folder)
        self.btn_estimate.clicked.connect(self._run_estimate)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)

    @staticmethod
    def _select(combo, data):
        i = combo.findData(data)
        combo.setCurrentIndex(i if i >= 0 else 0)

    def _load(self, o):
        quiet = (self.cmb_fmt, self.cmb_bits, self.cmb_space, self.cmb_size_mode, self.spin_edge)
        for wdg in quiet:
            wdg.blockSignals(True)
        try:
            self._select(self.cmb_fmt, o.fmt if format_available(o.fmt) else "tiff")
            self._bits_wanted = int(o.bits)
            self._fill_bits()
            self._space_wanted = o.space
            self._fill_spaces()
            self._select(self.cmb_tiff, o.tiff_compression)
            self._select(self.cmb_dng, o.dng_kind)
            self._select(self.cmb_dng_look, o.dng_look)
            self.sw_dng_profile.setChecked(o.dng_own_profile)
            self.sw_dng_curve.setChecked(o.dng_linear_curve)
            self.sl_png.set_value(o.png_level)
            self.sl_jpeg.set_value(o.jpeg_quality)
            self._select(self.cmb_sub, o.jpeg_subsampling)
            self.sw_prog.setChecked(o.jpeg_progressive)
            self.sl_webp.set_value(o.webp_quality)
            self.sl_webp_effort.set_value(o.webp_effort)
            self.sw_webp_lossless.setChecked(o.webp_lossless)
            self.sl_avif.set_value(o.avif_quality)
            self.sl_avif_speed.set_value(o.avif_speed)
            self._select(self.cmb_avif_sub, o.avif_subsampling)
            self.sl_jxl.set_value(o.jxl_quality)
            self.sl_jxl_effort.set_value(o.jxl_effort)
            self.sw_jxl_lossless.setChecked(o.jxl_lossless)
            self.sl_heif.set_value(o.heif_quality)
            self._select(self.cmb_heif_sub, o.heif_subsampling)
            self.sl_jp2.set_value(o.jp2_quality)
            self.sw_jp2_lossless.setChecked(o.jp2_lossless)
            self._select(self.cmb_exr, o.exr_compression)
            self.spin_target.setValue(int(o.target_kb))
            self.sw_icc.setChecked(o.embed_icc)
            self._select(self.cmb_res, o.resolution)
            self._select(self.cmb_size_mode, o.size_mode)
            self._pct = None
            self._size_mode_changed()
            self.spin_edge.setValue(int(o.long_edge))
            self._select(self.cmb_resample, o.resample)
            self._select(self.cmb_sharpen, o.sharpen)
            self._select(self.cmb_amount, o.sharpen_amount)
            self.sw_meta.setChecked(o.copy_metadata)
            self.sw_gps.setChecked(o.strip_gps)
            self.spin_dpi.setValue(int(o.dpi))
            self._out_dir = o.out_dir
            self.edit_suffix.setText(o.suffix)
            self.sw_overwrite.setChecked(o.overwrite)
        finally:
            for wdg in quiet:
                wdg.blockSignals(False)
        self.stack.setCurrentIndex(self._pages[self.cmb_fmt.currentData()])
        self._refresh_notes()

    def _fill_bits(self):
        fmt = self.cmb_fmt.currentData()
        depths = format_bits(fmt)
        self.cmb_bits.blockSignals(True)
        self.cmb_bits.clear()
        for d in depths:
            if fmt == "exr":
                text = {16: "16-bit half float  (plenty for photographs)", 32: "32-bit full float"}[d]
            else:
                text = BIT_LABELS[d] + " per channel" + ("  (best for further editing)" if d == 16 else "")
            self.cmb_bits.addItem(text, d)
        self._select(self.cmb_bits, self._bits_wanted if self._bits_wanted in depths else depths[0])
        self.cmb_bits.setEnabled(len(depths) > 1)
        self.cmb_bits.blockSignals(False)

    def _fill_spaces(self):
        fmt, bits = self.cmb_fmt.currentData(), int(self.cmb_bits.currentData() or 8)
        allowed = format_spaces(fmt, bits) or COLOR_SPACE_ORDER
        self.cmb_space.blockSignals(True)
        self.cmb_space.clear()
        for key in COLOR_SPACE_ORDER:
            if key in allowed:
                self.cmb_space.addItem(COLOR_SPACES[key]["label"], key)
        self._select(self.cmb_space, self._space_wanted if self._space_wanted in allowed else allowed[0])
        self.cmb_space.blockSignals(False)

    def _format_changed(self, *_):
        self._fill_bits()
        self._fill_spaces()
        self.stack.setCurrentIndex(self._pages[self.cmb_fmt.currentData()])
        self._refresh_notes()

    def _bits_changed(self, *_):
        self._bits_wanted = int(self.cmb_bits.currentData() or self._bits_wanted)
        self._fill_spaces()
        self._refresh_notes()

    def _space_changed(self, *_):
        self._space_wanted = self.cmb_space.currentData() or self._space_wanted
        self._refresh_notes()

    def _size_mode_changed(self, *_):
        pct = self.cmb_size_mode.currentData() == "percent"
        if pct == self._pct:
            return
        switched = self._pct is not None
        self._pct = pct
        self.spin_edge.blockSignals(True)
        self.spin_edge.setRange(0, 200 if pct else 20000)
        self.spin_edge.setSingleStep(5 if pct else 100)
        self.spin_edge.setSuffix(" %" if pct else " px")
        if switched:                         # a pixel count means nothing as a percentage, and back
            self.spin_edge.setValue(50 if pct else 0)
        self.spin_edge.blockSignals(False)
        self._refresh_notes()

    def _preset_chosen(self, index):
        i = self.cmb_preset.itemData(index)
        if i is None or i < 0:
            return
        o = self.options()
        for key, value in EXPORT_PRESETS[i][1].items():
            setattr(o, key, value)
        if o.dng_look != "none" and not dng_looks_available():
            o.dng_look = "none"
        self._base = o.copy()
        self._load(o)
        self.cmb_preset.blockSignals(True)
        self.cmb_preset.setCurrentIndex(0)           # a starting point, not a mode: edits are welcome
        self.cmb_preset.blockSignals(False)

    def _refresh_notes(self, *_):
        o = self.options()
        fmt = o.fmt
        raw = fmt == "dng"
        for wdg in (self.cmb_space, self.cmb_res, self.cmb_size_mode, self.spin_edge, self.cmb_sharpen,
                    self.spin_dpi):
            wdg.setEnabled(not raw)                  # none of these apply to a raw file
        self.cmb_resample.setEnabled(not raw and o.long_edge > 0)
        self.cmb_amount.setEnabled(not raw and o.sharpen != "none")
        self.spin_target.setEnabled(format_lossy(o) and fmt in QUALITY_FIELD)
        self.sl_webp.setEnabled(not o.webp_lossless)
        self.sl_jxl.setEnabled(not o.jxl_lossless)
        self.sl_jp2.setEnabled(not o.jp2_lossless)
        self.sw_gps.setEnabled(o.copy_metadata)
        self.sw_dng_curve.setEnabled(o.dng_look == "none" or not dng_looks_available())   # a look has its own curve
        bits = o.effective_bits()
        cicp = fmt == "jxl" or (fmt == "avif" and (bits > 8 or not _pil_has("avif")))
        self.sw_icc.setEnabled(not raw and not cicp and fmt not in ("jp2", "bmp", "exr"))
        key = self.cmb_space.currentData() or "srgb"
        if raw:
            self.lbl_space.setText("Not used for DNG: the pixels stay in the camera's own colour and the "
                                   "file carries the matrices that describe it.")
            self.lbl_chain.setText("camera RGBE  +  ColorMatrix (Kelvin)\n"
                                   "+  ForwardMatrix (colour)  +  as-shot neutral"
                                   + ("\n+  the Sony look (embedded camera profile)"
                                      if o.dng_look != "none" and dng_looks_available() else ""))
        else:
            if fmt == "exr":
                note = ("OpenEXR is always linear light; this sets the primaries written into the file. "
                        + COLOR_SPACES[key]["note"])
            else:
                note = COLOR_SPACES[key]["note"]
                if key != "srgb" and bits == 8:
                    note += "  At 8-bit this space can show banding; prefer 16-bit."
            if cicp:
                note += ("  This format records its colour space as a code in the file instead of an "
                         "ICC profile, which exists for sRGB, Display P3, Rec. 2020 and linear sRGB only.")
            elif fmt in ("jp2", "bmp"):
                note += "  These files carry no colour profile here, so they are always sRGB."
            self.lbl_space.setText(note)
            trc = COLOR_SPACES[key]["trc"]
            curve = "no curve (linear)" if fmt == "exr" else {
                "srgb": "sRGB curve", "rec709": "Rec.709 curve", "linear": "no curve (linear)"}.get(
                trc[0], f"gamma {trc[1]:.2f}" if len(trc) > 1 else "")
            self.lbl_chain.setText("camera RGBE > white balance > XYZ (D50)\n"
                                   f"> {COLOR_SPACES[key]['label']} primaries > {curve}\n"
                                   "Sony colour images: Sony matrix > white balance > camera tone curve\n"
                                   "> camera Y / colour-difference stage\n"
                                   f"> sRGB > {COLOR_SPACES[key]['label']} primaries > {curve}")
        self.lbl_dest.setText(self._out_dir if self._out_dir else "Next to each raw file")
        self.lbl_estimate.setText("")

    def _use_same_folder(self, *_):
        self._out_dir = ""
        self._refresh_notes()

    def _choose_folder(self, *_):
        folder = QFileDialog.getExistingDirectory(self, "Export to folder", self._out_dir or "")
        if folder:
            self._out_dir = folder
            self._refresh_notes()

    def _run_estimate(self, *_):
        if not self._estimate:
            return
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            o = self.options()
            size, w, h, used = self._estimate(o)
            text = f"{human_size(size)}   ·   {w} × {h} px"
            if used is not None:
                text += (f"   ·   quality {used}{' dB' if o.fmt == 'jp2' else ''} is the highest "
                         f"that fits {o.target_kb} KB")
            self.lbl_estimate.setText(text + "   (the image on screen)")
        except Exception as exc:
            self.lbl_estimate.setText(f"Could not estimate: {exc}")
        finally:
            QApplication.restoreOverrideCursor()

    def options(self):
        o = self._base.copy()
        o.fmt = self.cmb_fmt.currentData()
        o.bits = int(self._bits_wanted)
        o.space = self.cmb_space.currentData() or o.space
        o.embed_icc = self.sw_icc.isChecked()
        o.copy_metadata = self.sw_meta.isChecked()
        o.strip_gps = self.sw_gps.isChecked()
        o.dpi = int(self.spin_dpi.value())
        o.resolution = self.cmb_res.currentData()
        o.size_mode = self.cmb_size_mode.currentData()
        o.long_edge = int(self.spin_edge.value())
        o.resample = self.cmb_resample.currentData()
        o.sharpen = self.cmb_sharpen.currentData()
        o.sharpen_amount = self.cmb_amount.currentData()
        o.tiff_compression = self.cmb_tiff.currentData()
        o.jpeg_quality = int(round(self.sl_jpeg.value()))
        o.jpeg_subsampling = self.cmb_sub.currentData()
        o.jpeg_progressive = self.sw_prog.isChecked()
        o.png_level = int(round(self.sl_png.value()))
        o.webp_quality = int(round(self.sl_webp.value()))
        o.webp_lossless = self.sw_webp_lossless.isChecked()
        o.webp_effort = int(round(self.sl_webp_effort.value()))
        o.avif_quality = int(round(self.sl_avif.value()))
        o.avif_speed = int(round(self.sl_avif_speed.value()))
        o.avif_subsampling = self.cmb_avif_sub.currentData()
        o.jxl_quality = int(round(self.sl_jxl.value()))
        o.jxl_effort = int(round(self.sl_jxl_effort.value()))
        o.jxl_lossless = self.sw_jxl_lossless.isChecked()
        o.heif_quality = int(round(self.sl_heif.value()))
        o.heif_subsampling = self.cmb_heif_sub.currentData()
        o.jp2_quality = int(round(self.sl_jp2.value()))
        o.jp2_lossless = self.sw_jp2_lossless.isChecked()
        o.exr_compression = self.cmb_exr.currentData()
        o.target_kb = int(self.spin_target.value())
        o.dng_kind = self.cmb_dng.currentData()
        o.dng_own_profile = self.sw_dng_profile.isChecked()
        o.dng_linear_curve = self.sw_dng_curve.isChecked()
        o.dng_look = self.cmb_dng_look.currentData() if dng_looks_available() else "none"
        o.suffix = self.edit_suffix.text().strip()
        o.out_dir = self._out_dir
        o.overwrite = self.sw_overwrite.isChecked()
        return o


class SyncDialog(QDialog):
    """Choose which parts of the current image's edits are copied to the ticked images."""

    def __init__(self, parent, count):
        super().__init__(parent)
        self.setWindowTitle("Sync edits")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        head = QLabel(f"Copy this image's edits to {count} ticked image{'s' if count != 1 else ''}")
        head.setObjectName("h1")
        head.setWordWrap(True)
        lay.addWidget(head)
        card, cl = make_card("What to copy")
        self._rows = []
        for label, _fields in SYNC_GROUPS:
            row = SwitchRow(label, not label.startswith("Crop"))
            self._rows.append((label, row))
            cl.addWidget(row)
        cl.addWidget(muted_label("'As shot' and 'Auto' white balance are re-measured for every image "
                                 "rather than copied as a fixed Kelvin value."))
        lay.addWidget(card)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        ok = QPushButton("Sync")
        ok.setObjectName("primary")
        ok.setDefault(True)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)

    def groups(self):
        return {label for label, row in self._rows if row.isChecked()}


# ---------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------
# Nothing on these threads touches a widget. They run plain numpy functions and
# hand the result back through a signal, which Qt delivers on the interface
# thread. numpy releases Python's lock while it works, so the window keeps
# responding in the meantime.

class Worker(QThread):
    """Runs submitted jobs one after another. With latest_only, a newly
    submitted job replaces any that are still waiting (used for the preview:
    only the newest request matters)."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, latest_only=False, parent=None):
        super().__init__(parent)
        self._latest_only = latest_only
        self._cond = threading.Condition()
        self._jobs = collections.deque()
        self._busy = False
        self._quit = False
        self.start()

    def submit(self, fn, *args):
        with self._cond:
            if self._latest_only:
                self._jobs.clear()
            self._jobs.append((fn, args))
            self._cond.notify()

    def idle(self):
        with self._cond:
            return not self._jobs and not self._busy

    def run(self):
        while True:
            with self._cond:
                self._busy = False
                while not self._jobs and not self._quit:
                    self._cond.wait()
                if self._quit:
                    return
                fn, args = self._jobs.popleft()
                self._busy = True
            try:
                result = fn(*args)
            except Exception:
                self.failed.emit(traceback.format_exc())
                continue
            del fn, args
            if result is not None:
                self.done.emit(result)
            del result

    def shutdown(self):
        with self._cond:
            self._quit = True
            self._jobs.clear()
            self._cond.notify()
        self.wait(10000)


class ExportThread(QThread):
    """Exports a list of images one by one. Cancelling takes effect between images."""
    progress = Signal(int, str)              # index of the image being started, its file name
    finished_all = Signal(object, object)    # files written, error messages

    def __init__(self, jobs, opts, parent=None):
        super().__init__(parent)
        self._jobs = jobs                        # [(path, edit, sensor or None, metadata or None)]
        self._opts = opts
        self._cancel = False
        self.total = len(jobs)

    def cancel(self, *_):
        self._cancel = True

    def run(self):
        done, errors = [], []
        try:
            for i, (path, edit, sensor, meta) in enumerate(self._jobs):
                if self._cancel:
                    break
                name = os.path.basename(path)
                self.progress.emit(i, name)
                try:
                    if sensor is None:
                        sensor = read_sensor(path)
                    dst = export_path_for(path, self._opts)
                    export_image(sensor, edit, self._opts, dst, meta)
                    done.append(dst)
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                sensor = None
                gc.collect()
        finally:
            self._jobs = []
            self.finished_all.emit(done, errors)


class Entry:
    """One raw file in the library and its (non-destructive) edits."""

    def __init__(self, path):
        self.path = path
        self.edit = Edit()
        self.edited = False


ASPECTS = [("Free", None), ("Original", "original"), ("1 : 1", 1.0), ("4 : 3", 4 / 3), ("3 : 2", 3 / 2),
           ("16 : 9", 16 / 9), ("5 : 4", 5 / 4), ("3 : 4  (portrait)", 3 / 4), ("2 : 3  (portrait)", 2 / 3),
           ("9 : 16  (portrait)", 9 / 16), ("4 : 5  (portrait)", 4 / 5)]


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Sony F828 RGBE Converter (v{APP_VERSION})")
        self.resize(1500, 930)
        self.setMinimumSize(1240, 700)
        self.setAcceptDrops(True)
        self.settings = QSettings("F828Converter", "F828Converter")

        self.entries = {}                # path -> Entry
        self.items = {}                  # path -> QListWidgetItem
        self.current = None              # path of the image being edited
        self.sensor = None
        self.meta = None
        self.edit = Edit()
        self._quarter = None             # 25% RGBE frame of the open image (DN)
        self._shown = None               # the picture currently on screen
        self._shown_level = None         # ... and the preview level it was rendered at
        self._crop_size = (1, 1)
        self._token = 0                  # changes every time an image is opened
        self._gen = 0                    # changes every time the picture needs re-rendering
        self._waiting = False            # a sharper render has been requested and not arrived yet
        self._loading = False
        self._loading_proof = False
        self._shown_proof = None         # what the export preview reported for the picture on screen
        self._backdrop, self._backdrop_size = None, None
        self.preview_level = "half"
        self.export_opts = self._load_export_options()

        self.renderer = PreviewRenderer()            # only ever used on the preview thread
        self.preview_worker = Worker(latest_only=True, parent=self)
        self.preview_worker.done.connect(self._preview_done)
        self.preview_worker.failed.connect(self._preview_failed)
        self.thumb_worker = Worker(parent=self)
        self.thumb_worker.done.connect(self._thumb_done)
        self._export_thread = None
        self._export_dialog = None

        self._refine = QTimer(self)                  # waits for a pause in slider movement
        self._refine.setSingleShot(True)
        self._refine.setInterval(160)
        self._refine.timeout.connect(self._refine_now)

        self._build_ui()
        self._connect()
        self._load_controls()
        self._update_enabled()
        self._update_counts()

    # ------------------------------------------------------------ backdrop
    def _paint_backdrop(self, w, h):
        """The dark, softly coloured background the glass panels sit on. Painted
        once per window size and then reused, so panning the picture stays cheap."""
        pix = QPixmap(max(1, w), max(1, h))
        p = QPainter(pix)
        area = QRectF(0.0, 0.0, float(w), float(h))
        base = QLinearGradient(0.0, 0.0, 0.0, float(h))
        base.setColorAt(0.0, QColor(22, 24, 33))
        base.setColorAt(1.0, QColor(9, 10, 14))
        p.fillRect(area, QBrush(base))
        for fx, fy, fr, rgb in ((0.08, 0.10, 0.50, (10, 132, 255)), (0.95, 0.20, 0.45, (175, 82, 222)),
                                (0.62, 1.05, 0.55, (48, 209, 188)), (0.02, 0.95, 0.38, (255, 149, 0))):
            glow = QRadialGradient(QPointF(w * fx, h * fy), max(w, h) * fr)
            glow.setColorAt(0.0, QColor(rgb[0], rgb[1], rgb[2], 70))
            glow.setColorAt(1.0, QColor(rgb[0], rgb[1], rgb[2], 0))
            p.fillRect(area, QBrush(glow))
        p.end()
        return pix

    def paintEvent(self, _event):
        size = (int(self.width()), int(self.height()))
        if self._backdrop is None or self._backdrop_size != size:
            self._backdrop, self._backdrop_size = self._paint_backdrop(*size), size
        p = QPainter(self)
        p.drawPixmap(0, 0, self._backdrop)
        p.end()

    # ------------------------------------------------------------ construction
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(12)
        split.setChildrenCollapsible(False)
        root.addWidget(split)
        split.addWidget(self._build_library())
        self.viewer = ViewerArea()
        self.view = self.viewer.view
        split.addWidget(self.viewer)
        split.addWidget(self._build_inspector())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([270, 870, 350])

    def _build_library(self):
        panel = QFrame()
        panel.setObjectName("glass")
        panel.setMinimumWidth(240)
        panel.setMaximumWidth(400)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        title = QLabel("Library")
        title.setObjectName("h1")
        lay.addWidget(title)
        row = QHBoxLayout()
        self.btn_add_files = QPushButton("Add files\u2026")
        self.btn_add_folder = QPushButton("Add folder\u2026")
        row.addWidget(self.btn_add_files)
        row.addWidget(self.btn_add_folder)
        lay.addLayout(row)
        self.sw_subfolders = SwitchRow("Include subfolders", False)
        lay.addWidget(self.sw_subfolders)
        self.edit_filter = QLineEdit()
        self.edit_filter.setPlaceholderText("Filter by name")
        self.edit_filter.setClearButtonEnabled(True)
        lay.addWidget(self.edit_filter)
        self.list = QListWidget()
        self.list.setIconSize(QSize(76, 57))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.list, 1)
        self.lbl_count = muted_label("")
        lay.addWidget(self.lbl_count)
        row = QHBoxLayout()
        self.btn_check_all = QPushButton("Tick all")
        self.btn_check_none = QPushButton("Untick all")
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setToolTip("Remove the highlighted images from this list (files are not deleted)")
        for b in (self.btn_check_all, self.btn_check_none, self.btn_remove):
            row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(muted_label("Click an image to edit it. Tick images to include them in "
                                  "batch export and sync."))
        return panel

    def _build_inspector(self):
        panel = QFrame()
        panel.setObjectName("glass")
        panel.setMinimumWidth(330)
        panel.setMaximumWidth(440)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(8, 14, 8, 14)
        outer.setSpacing(10)
        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 8, 0)
        title = QLabel("Develop")
        title.setObjectName("h1")
        self.btn_reset = QPushButton("Reset edits")
        head.addWidget(title, 1)
        head.addWidget(self.btn_reset, 0)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setObjectName("clear")
        col = QVBoxLayout(inner)
        col.setContentsMargins(6, 0, 6, 0)
        col.setSpacing(10)

        # histogram
        card, cl = make_card("Histogram")
        self.histogram = Histogram()
        cl.addWidget(self.histogram)
        col.addWidget(card)

        # white balance
        card, cl = make_card("White balance")
        self.cmb_wb = compact(QComboBox())
        for label, key, _tt in WB_PRESETS:
            self.cmb_wb.addItem(label, key)
        self.cmb_wb.setToolTip("With Sony colour, Daylight, Cloudy, Shade, Tungsten, Fluorescent and Flash "
                               "are Image Data Converter's own presets, exactly as it applies them.")
        self.btn_auto_wb = QPushButton("Auto")
        self.btn_auto_wb.setToolTip("Estimate the light from the picture itself")
        row = QHBoxLayout()
        row.addWidget(self.cmb_wb, 1)
        row.addWidget(self.btn_auto_wb, 0)
        cl.addLayout(row)
        mired_lo, mired_hi = 1e6 / TEMP_MAX, 1e6 / TEMP_MIN
        self.sl_temp = ParamSlider(
            "Temperature", TEMP_MIN, TEMP_MAX, D65_TEMP_TINT[0], step=10.0, suffix=" K", style="temp",
            positions=900,
            to_value=lambda f: 1e6 / (mired_hi - (mired_hi - mired_lo) * f),
            to_frac=lambda v: (mired_hi - 1e6 / v) / (mired_hi - mired_lo),
            tip="Colour temperature of the light, in Kelvin. Left = cooler picture, right = warmer.")
        cl.addWidget(self.sl_temp)
        self.sl_tint = ParamSlider("Tint", TINT_MIN, TINT_MAX, D65_TEMP_TINT[1], step=1.0, style="tint",
                                   tip="Left = greener picture, right = more magenta.")
        cl.addWidget(self.sl_tint)
        self.lbl_asshot = muted_label("")
        cl.addWidget(self.lbl_asshot)
        col.addWidget(card)

        # colour rendering
        card, cl = make_card("Colour")
        self.sw_sony = SwitchRow(
            "Sony 'Real' colour (Image Data Converter)", False,
            "Off: the default rendering (Adobe-style colour matrix, sRGB curve, roll-off). "
            "On: Sony's own rendering as Image Data Converter SR does it: the camera's colour "
            "processor, set up from the registers stored in the .SRF (matrix, white balance, "
            "tone curve and the per-shot 'Real' colour coefficients).")
        cl.addWidget(self.sw_sony)
        self.cmb_sony_curve = compact(QComboBox())
        for text, key in SONY_CURVE_MODES:
            self.cmb_sony_curve.addItem(text, key)
        self.cmb_sony_curve.setToolTip("Which of the camera's two colour modes to reproduce. Use the one "
                                       "the camera was set to (Real or Standard).")
        cl.addLayout(labelled_row("Camera mode", self.cmb_sony_curve, 96))
        self.lbl_render = muted_label("")
        cl.addWidget(self.lbl_render)
        col.addWidget(card)

        # light
        card, cl = make_card("Light")
        self.sl_exp = ParamSlider("Exposure", -5.0, 5.0, 0.0, step=0.1, decimals=1, suffix=" EV", style="centered")
        cl.addWidget(self.sl_exp)
        self.sl_black = ParamSlider("Black point", 0, 500, 0, step=1.0,
                                    tip="Neutral black point (equal in R, G and B). Double-click the name to reset.")
        cl.addWidget(self.sl_black)
        self.sw_rolloff = SwitchRow("Highlight soft roll-off", True)
        cl.addWidget(self.sw_rolloff)
        self.sw_highlights = SwitchRow(
            "Highlight reconstruction", False,
            "Rebuilds channels that hit the sensor's limit from the ones that did not, instead of "
            "cutting everything at white. Gives the roll-off (or a lower exposure) real detail to "
            "bring back. Fully blown areas stay white.")
        cl.addWidget(self.sw_highlights)
        self.sw_ir = SwitchRow("IR false colour (Aerochrome)", False)
        cl.addWidget(self.sw_ir)
        col.addWidget(card)

        # lens
        card, cl = make_card("Lens corrections")
        row = QHBoxLayout()
        row.addWidget(QLabel("Chromatic aberration"), 1)
        self.btn_auto_ca = QPushButton("Auto")
        self.btn_auto_ca.setToolTip("Measure the colour fringing from the picture itself and correct it")
        row.addWidget(self.btn_auto_ca, 0)
        cl.addLayout(row)
        self.sl_ca_red = ParamSlider(
            "Red / cyan", -60.0, 60.0, 0.0, step=0.5, decimals=1, style="centered",
            tip="Scales the red image against the green one, in 1/10000 of the frame. Removes red / cyan "
                "fringes that grow towards the corners.")
        cl.addWidget(self.sl_ca_red)
        self.sl_ca_blue = ParamSlider(
            "Blue / yellow", -60.0, 60.0, 0.0, step=0.5, decimals=1, style="centered",
            tip="Scales the blue (and partly the emerald) image against the green one. Removes blue / "
                "yellow fringes that grow towards the corners.")
        cl.addWidget(self.sl_ca_blue)
        self.lbl_ca = muted_label("")
        cl.addWidget(self.lbl_ca)
        self.sl_defringe = ParamSlider(
            "Defringe", 0, 100, 0,
            tip="Removes the purple (and green) fringes along high-contrast edges, such as branches or "
                "window frames against a bright sky. Blue sky and purple objects away from such edges "
                "are left alone.")
        cl.addWidget(self.sl_defringe)
        col.addWidget(card)

        # noise
        card, cl = make_card("Noise reduction")
        self.sl_chroma_nr = ParamSlider(
            "Colour noise", 0, 100, 0,
            tip="Smooths the coloured speckles and blotches of noise, guided by the picture's own edges so "
                "colours do not bleed. Brightness detail is left alone.")
        cl.addWidget(self.sl_chroma_nr)
        col.addWidget(card)

        # geometry
        card, cl = make_card("Crop and straighten")
        self.cmb_aspect = compact(QComboBox())
        for label, value in ASPECTS:
            self.cmb_aspect.addItem(label, value)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_ccw = QPushButton("\u21BA")
        self.btn_ccw.setToolTip("Rotate 90\u00B0 left  (Shift+R)")
        self.btn_cw = QPushButton("\u21BB")
        self.btn_cw.setToolTip("Rotate 90\u00B0 right  (R)")
        self.btn_fliph = QPushButton("\u2194")
        self.btn_fliph.setToolTip("Mirror left-right")
        self.btn_flipv = QPushButton("\u2195")
        self.btn_flipv.setToolTip("Flip top-bottom")
        for b in (self.btn_ccw, self.btn_cw, self.btn_fliph, self.btn_flipv):
            b.setObjectName("glyph")
            b.setMinimumWidth(40)
            row.addWidget(b)
        cl.addLayout(row)
        cl.addLayout(labelled_row("Shape", self.cmb_aspect, 70))
        self.sl_straighten = ParamSlider("Straighten", -20.0, 20.0, 0.0, step=0.1, decimals=1,
                                         suffix="\u00B0", style="centered",
                                         tip="Positive turns the picture clockwise. The crop shrinks "
                                             "automatically so no empty corners show.")
        cl.addWidget(self.sl_straighten)
        row = QHBoxLayout()
        self.btn_crop_tool = QPushButton("Edit crop")
        self.btn_crop_tool.setCheckable(True)
        self.btn_reset_crop = QPushButton("Reset")
        row.addWidget(self.btn_crop_tool, 1)
        row.addWidget(self.btn_reset_crop, 0)
        cl.addLayout(row)
        self.lbl_crop = muted_label("")
        cl.addWidget(self.lbl_crop)
        col.addWidget(card)

        # sensor
        card, cl = make_card("Sensor")
        self.cmb_demosaic = compact(QComboBox())
        for text, key in DEMOSAIC_MODES:
            if key != "idc" or idc_demosaic_available():
                self.cmb_demosaic.addItem(text, key)
        self.cmb_demosaic.setToolTip(
            "How the full-colour picture is rebuilt from the sensor's RGBE mosaic.\n"
            "IDC: Sony Image Data Converter's own method, recreated. Smooth colour planes "
            "with a sharp brightness taken from the green and emerald photosites, and colour removed "
            "on the very hardest edges. With the Sony colour rendering it reproduces IDC's own files "
            "to within 1/255. Least colour noise and no colour fringes on fine lines.\n"
            "Edge-aware: follows edges in every colour. A little more fine detail than IDC, but more "
            "colour noise and some colour fringing on fine lines.\n"
            "Bilinear: v15's. The softest.")
        cl.addLayout(labelled_row("Demosaic", self.cmb_demosaic, 80))
        self.lbl_demosaic = muted_label("")
        cl.addWidget(self.lbl_demosaic)
        self._update_demosaic_note()
        self.sw_hot = SwitchRow(
            "Remove hot pixels", False,
            "Replaces stuck bright (or dead dark) photosites with their neighbours before the demosaic. "
            "Real points of light such as stars or glints are kept: they spread over photosites of "
            "every colour, a hot pixel does not.")
        cl.addWidget(self.sw_hot)
        self.lbl_hot = muted_label("")
        cl.addWidget(self.lbl_hot)
        cl.addWidget(QLabel("Black level"))
        self.cmb_black = compact(QComboBox())
        for text, key in BLACK_MODES:
            self.cmb_black.addItem(text, key)
        cl.addWidget(self.cmb_black)
        self.lbl_black_info = muted_label("", mono=True)
        cl.addWidget(self.lbl_black_info)
        col.addWidget(card)

        # info
        card, cl = make_card("Image info")
        self.lbl_info = muted_label("No image loaded")
        cl.addWidget(self.lbl_info)
        col.addWidget(card)

        col.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        foot = QVBoxLayout()
        foot.setContentsMargins(8, 0, 8, 0)
        foot.setSpacing(8)
        self.lbl_batch = muted_label("")
        foot.addWidget(self.lbl_batch)
        row = QHBoxLayout()
        self.btn_sync = QPushButton("Sync edits to ticked\u2026")
        self.btn_export_checked = QPushButton("Export ticked\u2026")
        row.addWidget(self.btn_sync)
        row.addWidget(self.btn_export_checked)
        foot.addLayout(row)
        self.btn_export = QPushButton("Export this image\u2026")
        self.btn_export.setObjectName("primary")
        foot.addWidget(self.btn_export)
        outer.addLayout(foot)
        return panel

    def _connect(self):
        v = self.viewer
        self.btn_add_files.clicked.connect(self._add_files)
        self.btn_add_folder.clicked.connect(self._add_folder)
        self.edit_filter.textChanged.connect(self._apply_filter)
        self.list.currentItemChanged.connect(self._current_item_changed)
        self.list.itemChanged.connect(self._update_counts)
        self.btn_check_all.clicked.connect(lambda *_: self._tick_all(True))
        self.btn_check_none.clicked.connect(lambda *_: self._tick_all(False))
        self.btn_remove.clicked.connect(self._remove_selected)

        self.btn_reset.clicked.connect(self._reset_edits)
        self.cmb_wb.currentIndexChanged.connect(self._wb_preset_changed)
        self.btn_auto_wb.clicked.connect(lambda *_: self._apply_wb_preset("auto"))
        self.sl_temp.changed.connect(self._temp_tint_changed)
        self.sl_tint.changed.connect(self._temp_tint_changed)
        self.sl_exp.changed.connect(self._tone_changed)
        self.sl_black.changed.connect(self._tone_changed)
        self.sw_rolloff.toggled.connect(self._tone_changed)
        self.sw_highlights.toggled.connect(self._tone_changed)
        self.sw_ir.toggled.connect(self._tone_changed)
        self.sw_sony.toggled.connect(self._render_changed)
        self.cmb_sony_curve.currentIndexChanged.connect(self._render_changed)
        self.cmb_demosaic.currentIndexChanged.connect(self._demosaic_changed)
        self.sw_hot.toggled.connect(self._hot_changed)
        self.sl_ca_red.changed.connect(self._lens_changed)
        self.sl_ca_blue.changed.connect(self._lens_changed)
        self.btn_auto_ca.clicked.connect(self._auto_ca)
        self.sl_defringe.changed.connect(self._detail_changed)
        self.sl_chroma_nr.changed.connect(self._detail_changed)
        self.cmb_black.currentIndexChanged.connect(self._black_mode_changed)
        self.cmb_aspect.currentIndexChanged.connect(self._aspect_changed)
        self.sl_straighten.changed.connect(self._straighten_changed)
        self.btn_crop_tool.clicked.connect(lambda *_: self._set_tool("crop" if self.btn_crop_tool.isChecked() else "pan"))
        self.btn_reset_crop.clicked.connect(self._reset_crop)

        v.btn_pan.clicked.connect(lambda *_: self._set_tool("pan"))
        v.btn_crop.clicked.connect(lambda *_: self._set_tool("crop"))
        v.btn_pick.clicked.connect(lambda *_: self._set_tool("pick"))
        self.btn_cw.clicked.connect(lambda *_: self._orient("cw"))
        self.btn_ccw.clicked.connect(lambda *_: self._orient("ccw"))
        self.btn_fliph.clicked.connect(lambda *_: self._orient("fliph"))
        self.btn_flipv.clicked.connect(lambda *_: self._orient("flipv"))
        v.btn_zoom_in.clicked.connect(lambda *_: self.view.zoom_step(+1))
        v.btn_zoom_out.clicked.connect(lambda *_: self.view.zoom_step(-1))
        v.btn_fit.clicked.connect(lambda *_: self.view.fit())
        v.btn_100.clicked.connect(lambda *_: self.view.actual_size())
        v.btn_clip.toggled.connect(self._request)
        v.sl_res.valueChanged.connect(self._preview_level_changed)
        v.sw_proof.toggled.connect(self._proof_toggled)
        v.cmb_proof_fmt.currentIndexChanged.connect(self._proof_format_changed)
        v.sl_proof_q.changed.connect(self._proof_quality_changed)
        v.cmb_proof_view.currentIndexChanged.connect(lambda *_: self._request_proof())
        v.btn_export_settings.clicked.connect(self._export_settings)
        v.btn_full.clicked.connect(self._full_quality)
        self.view.cropChanged.connect(self._crop_dragged)
        self.view.cropFinished.connect(self._crop_finished)
        self.view.pointPicked.connect(self._picked)
        self.view.applyRequested.connect(lambda *_: self._set_tool("pan"))

        v.sl_res.setValue([k for k, _, _ in RESOLUTIONS].index(self.preview_level))
        self._sync_proof_controls()
        self._preview_level_changed()

        self.btn_sync.clicked.connect(self._sync)
        self.btn_export.clicked.connect(lambda *_: self._export(False))
        self.btn_export_checked.clicked.connect(lambda *_: self._export(True))

        for keys, slot in (("Ctrl+O", self._add_files), ("Ctrl+Shift+O", self._add_folder),
                           ("Ctrl+E", lambda: self._export(False)), ("Ctrl+Shift+E", lambda: self._export(True)),
                           ("V", lambda: self._set_tool("pan")), ("C", lambda: self._set_tool("crop")),
                           ("W", lambda: self._set_tool("pick")), ("R", lambda: self._orient("cw")),
                           ("Shift+R", lambda: self._orient("ccw")), ("F", lambda: self.view.fit()),
                           ("1", lambda: self.view.actual_size()), ("+", lambda: self.view.zoom_step(+1)),
                           ("=", lambda: self.view.zoom_step(+1)), ("-", lambda: self.view.zoom_step(-1)),
                           ("J", lambda: self.viewer.btn_clip.toggle())):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)

    # ------------------------------------------------------------ settings
    def _load_export_options(self):
        opts = ExportOptions()
        try:
            stored = json.loads(self.settings.value("export_options", "") or "{}")
            for key, value in stored.items():
                if hasattr(opts, key) and isinstance(value, type(getattr(opts, key))):
                    setattr(opts, key, value)
        except Exception:
            pass
        # anything stored by another version, or that this installation cannot write, goes back to default
        default = ExportOptions()
        choices = {
            "fmt": available_formats(), "space": COLOR_SPACE_ORDER, "resolution": ("full", "half"),
            "size_mode": [k for k, _ in SIZE_MODES], "resample": [k for k, _ in RESAMPLE_MODES],
            "sharpen": [k for k, _ in SHARPEN_MODES], "sharpen_amount": [k for k, _ in SHARPEN_AMOUNTS],
            "tiff_compression": ["none", "deflate"] + (["lzw"] if lzw_available() else [])
                                + (["zstd"] if zstd_available() else []),
            "jpeg_subsampling": ("4:4:4", "4:2:2", "4:2:0"), "avif_subsampling": ("4:4:4", "4:2:0"),
            "heif_subsampling": ("4:4:4", "4:2:0"), "exr_compression": [k for k, _ in EXR_COMPRESSIONS],
            "dng_kind": ("linear", "mosaic"),
            "dng_look": [k for k, _ in DNG_LOOKS] if dng_looks_available() else ("none",)}
        for key, allowed in choices.items():
            if getattr(opts, key) not in allowed:
                setattr(opts, key, getattr(default, key))
        for key, lo, hi in (("jpeg_quality", 1, 100), ("webp_quality", 1, 100), ("avif_quality", 1, 100),
                            ("jxl_quality", 1, 100), ("heif_quality", 1, 100), ("jp2_quality", 20, 70),
                            ("png_level", 1, 9), ("webp_effort", 0, 6), ("avif_speed", 0, 10),
                            ("jxl_effort", 1, 9), ("dpi", 0, 4800), ("long_edge", 0, 20000),
                            ("target_kb", 0, 1000000), ("bits", 8, 32)):
            setattr(opts, key, int(min(max(int(getattr(opts, key)), lo), hi)))
        if opts.size_mode == "percent":
            opts.long_edge = min(opts.long_edge, 200)
        return opts

    def _save_export_options(self):
        try:
            self.settings.setValue("export_options", json.dumps(self.export_opts.__dict__))
        except Exception:
            pass

    def _last_dir(self):
        return str(self.settings.value("last_dir", "") or "")

    # ------------------------------------------------------------ library
    def _add_files(self, *_):
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "Add raw files", self._last_dir(), "Sony RAW (*.srf *.SRF);;All files (*)")
        if paths:
            self.settings.setValue("last_dir", os.path.dirname(paths[0]))
            self.add_paths(paths)

    def _add_folder(self, *_):
        folder = QFileDialog.getExistingDirectory(self, "Add a folder of raw files", self._last_dir())
        if folder:
            self.settings.setValue("last_dir", folder)
            self.add_paths([folder])

    def add_paths(self, paths):
        """Add files and/or folders to the library. Returns how many new images were added."""
        files = find_raw_files(paths, recursive=self.sw_subfolders.isChecked())
        new = [p for p in files if p not in self.entries]
        if not files:
            QMessageBox.information(self, "No raw files",
                                    "No .SRF files were found there." +
                                    ("" if self.sw_subfolders.isChecked()
                                     else "\n\nTurn on 'Include subfolders' to look deeper."))
            return 0
        first = None
        for p in new:
            self.entries[p] = Entry(p)
            self.entries[p].edit.render, self.entries[p].edit.sony_curve = self._render_default()
            self.entries[p].edit.demosaic = self._demosaic_default()
            item = QListWidgetItem(os.path.basename(p))
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setSizeHint(QSize(200, 68))
            self.items[p] = item
            self.list.addItem(item)
            if first is None:
                first = item
        for p in new:
            self._queue_thumb(p)
        self._apply_filter()
        self._update_counts()
        if first is not None and self.current is None:
            self.list.setCurrentItem(first)
        return len(new)

    def _apply_filter(self, *_):
        needle = self.edit_filter.text().strip().lower()
        for path, item in self.items.items():
            item.setHidden(bool(needle) and needle not in os.path.basename(path).lower())

    def _checked_paths(self):
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def _tick_all(self, on):
        self.list.blockSignals(True)
        for item in self.items.values():
            if not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self._update_counts()

    def _remove_selected(self, *_):
        paths = [item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()]
        if not paths:
            return
        closing_current = self.current in paths
        if closing_current:
            self._close_current()
        self.list.blockSignals(True)
        for p in paths:
            item = self.items.pop(p, None)
            self.entries.pop(p, None)
            if item is not None:
                self.list.takeItem(self.list.row(item))
        self.list.blockSignals(False)
        self._update_counts()
        if closing_current and self.list.count():
            row = max(0, self.list.currentRow())
            self.list.setCurrentRow(-1)
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _update_counts(self, *_):
        n, k = len(self.entries), len(self._checked_paths())
        self.lbl_count.setText(f"{n} image{'s' if n != 1 else ''}   \u00B7   {k} ticked" if n else
                               "Nothing here yet")
        self.lbl_batch.setText(f"Batch: {k} image{'s' if k != 1 else ''} ticked" if k else
                               "Batch: tick images in the library to export or sync several at once")
        self.btn_export_checked.setEnabled(k > 0 and self._export_thread is None)
        self.btn_sync.setEnabled(self.sensor is not None and k > 0)

    def _queue_thumb(self, path):
        """Ask the thumbnail thread for this image's thumbnail (camera preview
        for untouched images, a real develop once it has edits)."""
        entry = self.entries.get(path)
        if entry is not None:
            self.thumb_worker.submit(make_thumbnail, path, entry.edit.copy(), not entry.edited)

    def _thumb_done(self, result):
        item = self.items.get(result["path"])
        if item is not None and result["thumb"] is not None and result["path"] != self.current:
            item.setIcon(QIcon(array_to_pixmap(result["thumb"])))

    def _update_current_thumb(self):
        if self._shown is None or self.current not in self.items or self.view.tool == "crop":
            return
        step = max(1, max(self._shown.shape[:2]) // 160)
        self.items[self.current].setIcon(QIcon(array_to_pixmap(self._shown[::step, ::step])))

    # ------------------------------------------------------------ opening an image
    def _current_item_changed(self, item, _previous=None):
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and path != self.current:
            self._open(path)

    def _close_current(self):
        self._refine.stop()
        self._token += 1
        self._gen += 1
        self._waiting = False
        self.current, self.sensor, self.meta = None, None, None
        self._quarter, self._shown, self._shown_level, self._shown_proof = None, None, None, None
        self.preview_worker.submit(self.renderer.forget)        # let go of the old image's memory
        self.edit = Edit()
        self.view.clear()
        self.viewer.set_status("")
        self.histogram.set_image(None)
        self.lbl_info.setText("No image loaded")
        self.lbl_black_info.setText("")
        self.lbl_asshot.setText("")
        self.lbl_render.setText("")
        self.lbl_crop.setText("")
        self.lbl_hot.setText("")
        self.lbl_ca.setText("")
        self._update_enabled()
        gc.collect()

    def _open(self, path):
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            sensor = read_sensor(path)
            meta = read_metadata(path)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Could not open the file", f"{os.path.basename(path)}\n\n{exc}")
            previous = self.items.get(self.current)
            self.list.blockSignals(True)
            if previous is None:
                self.list.setCurrentRow(-1)
            else:
                self.list.setCurrentItem(previous)
            self.list.blockSignals(False)
            return
        try:
            self._refine.stop()
            self._token += 1
            self.current, self.sensor, self.meta = path, sensor, meta
            self._quarter, self._shown, self._shown_level, self._shown_proof = None, None, None, None
            entry = self.entries[path]
            self.edit = entry.edit
            resolve_edit(self.edit, sensor, self._quarter_dn())
            if self.view.tool == "crop":
                self._set_tool("pan", refresh=False)
            self._load_controls()
            self._update_info()
            self._update_enabled()
            self.view.request_fit()
            self._request()
            self._update_current_thumb()
        finally:
            QApplication.restoreOverrideCursor()
            gc.collect()

    def _update_enabled(self):
        on = self.sensor is not None
        v = self.viewer
        for wdg in (self.btn_reset, self.cmb_wb, self.btn_auto_wb, self.sl_temp, self.sl_tint, self.sl_exp,
                    self.sl_black, self.sw_rolloff, self.sw_highlights, self.sw_ir, self.cmb_black,
                    self.sw_sony, self.cmb_sony_curve, self.cmb_demosaic, self.sw_hot, self.cmb_aspect,
                    self.btn_auto_ca, self.sl_ca_red, self.sl_ca_blue, self.sl_defringe, self.sl_chroma_nr,
                    self.sl_straighten, self.btn_crop_tool, self.btn_reset_crop, self.btn_export,
                    v.btn_pan, v.btn_crop, v.btn_pick, self.btn_cw, self.btn_ccw, self.btn_fliph, self.btn_flipv,
                    v.btn_zoom_in, v.btn_zoom_out, v.btn_fit, v.btn_100, v.btn_clip):
            wdg.setEnabled(on)
        self.btn_export.setEnabled(on and self._export_thread is None)
        self.btn_sync.setEnabled(on and bool(self._checked_paths()))
        self._update_render_controls()

    def _update_render_controls(self):
        """Controls that only mean something for one of the two renderings."""
        on = self.sensor is not None
        sony = on and self.edit.render == "sony"
        self.cmb_sony_curve.setEnabled(sony and not self.edit.ir)
        self.sw_rolloff.setEnabled(on and not sony)       # Sony's curve has its own highlight shoulder
        if not on:
            self.lbl_render.setText("")
        elif self.edit.ir and sony:
            self.lbl_render.setText("IR false colour is on, so it is used instead of Sony colour.")
        elif sony and self.sensor.get("camera") is not None:
            self.lbl_render.setText("The camera's own colour processor, set up from this file's registers, "
                                    "as Image Data Converter SR renders it. Full-size exports and their "
                                    "export preview use IDC's own arithmetic, so they match its files. "
                                    "Highlight roll-off is not used (the curve has its own).")
        elif sony:
            self.lbl_render.setText("This file has no readable camera registers, so the approximate Sony "
                                    "rendering (matrix + tone curve) is used.")
        else:
            self.lbl_render.setText("Default rendering: Adobe-style colour matrix, sRGB curve.")

    def _load_controls(self):
        """Push the current image's edits into the controls without triggering re-renders."""
        e = self.edit
        self._loading = True
        try:
            self.cmb_wb.setCurrentIndex(max(0, self.cmb_wb.findData(e.wb_preset)))
            self.sl_temp.set_value(e.temp)
            self.sl_tint.set_value(e.tint)
            self.sl_exp.set_value(e.exposure)
            self.sl_black.set_value(e.black_point)
            self.sw_rolloff.setChecked(e.rolloff)
            self.sw_highlights.setChecked(e.highlights)
            self.sw_ir.setChecked(e.ir)
            self.sw_sony.setChecked(e.render == "sony")
            self.cmb_sony_curve.setCurrentIndex(max(0, self.cmb_sony_curve.findData(e.sony_curve)))
            self.cmb_demosaic.setCurrentIndex(max(0, self.cmb_demosaic.findData(e.demosaic)))
            self._update_demosaic_note()
            self.sw_hot.setChecked(e.hot_pixels)
            self.sl_ca_red.set_value(e.ca_red)
            self.sl_ca_blue.set_value(e.ca_blue)
            self.sl_defringe.set_value(e.defringe)
            self.sl_chroma_nr.set_value(e.chroma_nr)
            self.lbl_ca.setText("")
            self.sl_straighten.set_value(e.straighten)
            self.cmb_black.setCurrentIndex(max(0, self.cmb_black.findData(e.black_mode)))
            self.cmb_aspect.setCurrentIndex(0)
            self.view.set_aspect(None)
        finally:
            self._loading = False
        self._update_render_controls()
        self._update_hot_info()

    def _update_info(self):
        s = self.sensor
        if s is None:
            return
        h, w = s["cfa"].shape
        rows = [("File", os.path.basename(self.current)), ("Sensor", f"{w} \u00D7 {h}  RGBE")]
        rows += describe_metadata(self.meta or {})
        if not (self.meta or {}).get("exif"):
            rows.append(("EXIF", "none found in this file"))
        self.lbl_info.setText("\n".join(f"{k}:  {v}" for k, v in rows))
        tt = sensor_asshot(s)
        if tt and s.get("asshot_source") == "camera":
            self.lbl_asshot.setText(f"Camera as-shot (from its registers): {tt[0]:.0f} K, tint {tt[1]:+.0f}")
        elif tt and s.get("asshot_source") == "preview":
            self.lbl_asshot.setText(f"Camera as-shot (from its preview): {tt[0]:.0f} K, tint {tt[1]:+.0f}")
        elif tt:
            self.lbl_asshot.setText(f"Stored WB tags: {tt[0]:.0f} K, tint {tt[1]:+.0f}. The preview could "
                                    "not be measured; these tags are the same in every F828 file, so "
                                    "check the colour.")
        else:
            self.lbl_asshot.setText("No as-shot white balance could be found; 'As shot' uses the "
                                    f"matrix-neutral {D65_TEMP_TINT[0]:.0f} K.")
        self._update_black_info()

    def _update_black_info(self):
        if self.sensor is None:
            self.lbl_black_info.setText("")
            return
        active = self.edit.black_mode
        lines = [" " * 8 + " ".join(f"{n:>6}" for n in "RGBE")]
        for key in ("camera", "border", "fixed", "libraw"):
            val = self.sensor["black"].get(key)
            vals = "   n/a" if val is None else " ".join(f"{x:6.1f}" for x in val)
            lines.append(f"{'>' if key == active else ' '}{key:<6} {vals}")
        lines.append(f" white  {self.sensor['white']:6.0f}")
        self.lbl_black_info.setText("\n".join(lines))

    # ------------------------------------------------------------ rendering
    def _quarter_dn(self):
        """The 25% frame. Cheap, so it stays on this thread: drafts, auto white
        balance and the white picker all use it."""
        if self._quarter is None:
            self._quarter = sensor_rgbe_dn(self.sensor, "quarter")
        return self._quarter

    def _frame_size(self):
        h, w = self.sensor["cfa"].shape
        return oriented_size(w, h, self.edit.rot_k)

    def _view_options(self):
        return {"cropping": self.view.tool == "crop", "clip": self.viewer.btn_clip.isChecked()}

    def _proof_options(self):
        """The export settings when the picture should be shown as the export file, else None."""
        if not self.viewer.sw_proof.isChecked() or self.export_opts.fmt == "dng" or self.view.tool == "crop":
            return None
        return self.export_opts.copy()

    def _needs_full_frame(self):
        """Edits that the quick 25% superpixel frame cannot show."""
        e = self.edit
        return e.hot_pixels or abs(e.ca_red) >= 1e-3 or abs(e.ca_blue) >= 1e-3

    def _request(self, *_):
        """Something that changes the picture happened. Show a 25% draft straight
        away (a few hundredths of a second, done here) and ask the preview thread
        for the real thing; the window never waits for it."""
        if self.sensor is None:
            return
        proof = self._proof_options()
        final = self.preview_level == "quarter" and proof is None and not self._needs_full_frame()
        if final or self.viewer.sw_draft.isChecked() or self._shown is None:
            base = develop_preview(self._quarter_dn(), self.sensor, self.edit, parallel=False)
            draft = compose_view(base, self.edit, self._frame_size(), **self._view_options())
            if proof is not None:            # keep the zoom in export pixels while the draft shows
                _level, ow, oh = proof_level(self.edit, self._frame_size(), proof)
                draft["frame"] = (ow, oh)
            self._display(draft, "quarter")
        self._request_proof(final)

    def _request_proof(self, final=False):
        """Ask for a new render without a draft: for changes to the export settings
        only, where the picture on screen is still right until the new one arrives."""
        if self.sensor is None:
            return
        self._gen += 1                       # whatever is still being rendered is now out of date
        self._waiting = not final
        self._refine.start()
        self._update_status()

    def _refine_now(self):
        """A moment after the last change: hand the job to the preview thread."""
        if self.sensor is None:
            return
        proof = self._proof_options()
        if self.preview_level == "quarter" and proof is None and not self._needs_full_frame():
            self._waiting = False
            self._update_status()
            self._update_current_thumb()
            return
        job = {"gen": self._gen, "token": self._token, "sensor": self.sensor, "edit": self.edit.copy(),
               "level": self.preview_level, "frame": self._frame_size(), "options": self._view_options(),
               "proof": proof, "proof_view": self.viewer.cmb_proof_view.currentData()}
        self.preview_worker.submit(self.renderer.render, job)

    def _preview_done(self, result):
        if result.get("gen") != self._gen or result.get("token") != self._token or self.sensor is None:
            return                           # superseded by a newer change, or another image is open now
        self._waiting = False
        self._display(result, result["level"])
        self._update_current_thumb()
        self._update_hot_info()

    def _preview_failed(self, text):
        sys.stderr.write(text)
        self._waiting = False
        self.viewer.set_status("The preview could not be rendered: " + text.strip().splitlines()[-1])

    def _display(self, r, level):
        if r["cropping"]:
            self.view.set_crop(r["crop"])
        img, frame = r["img"], r["frame"]
        self._shown_proof = r.get("proof")
        if self._shown_proof is not None:
            self._show_proof_result(self._shown_proof)
        self._shown, self._shown_level, self._crop_size = img, level, r["crop_size"]
        self.histogram.set_image(img)
        self.view.set_image(r["view"], frame[0], frame[1])
        cw, ch = r["crop_size"]
        self.lbl_crop.setText(f"Output: {cw} \u00D7 {ch} px" + ("   Double-click inside the frame, or "
                              "press 'Done cropping', to apply." if r["cropping"] else ""))
        self._update_status()

    def _update_status(self):
        if self.sensor is None or self._shown_level is None:
            return
        fw, fh = self._frame_size()
        cw, ch = self._crop_size
        size = f"{fw} \u00D7 {fh}" if (cw, ch) == (fw, fh) else f"{fw} \u00D7 {fh}  \u2192  crop {cw} \u00D7 {ch}"
        name = os.path.basename(self.current)
        proof = self._shown_proof
        if proof is not None:
            ow, oh = proof["size"]
            text = (f"{name}   \u00B7   export preview: {EXPORT_FORMATS[proof['fmt']][0].split('  (')[0]}, "
                    f"{ow} \u00D7 {oh} px")
        else:
            text = f"{name}   \u00B7   {size}   \u00B7   preview {int(RESOLUTION_SCALE[self._shown_level] * 100)}%"
        if self._waiting:
            text += ("   \u00B7   rendering the export\u2026" if self._proof_options() is not None else
                     f"   \u00B7   rendering {int(RESOLUTION_SCALE[self.preview_level] * 100)}%\u2026")
        self.viewer.set_status(text)

    def _edit_changed(self):
        """A develop setting changed."""
        if self.sensor is None:
            return
        self.entries[self.current].edited = True
        self._request()

    # ------------------------------------------------------------ develop controls
    def _busy(self):
        return self._loading or self.sensor is None

    def _wb_preset_changed(self, *_):
        if not self._busy():
            self._apply_wb_preset(self.cmb_wb.currentData())

    def _apply_wb_preset(self, key):
        if self.sensor is None:
            return
        e = self.edit
        e.wb_preset = key
        if key != "custom":
            e.temp = e.tint = None
            resolve_edit(e, self.sensor, self._quarter_dn())
        self._loading = True
        try:
            self.cmb_wb.setCurrentIndex(max(0, self.cmb_wb.findData(key)))
            self.sl_temp.set_value(e.temp)
            self.sl_tint.set_value(e.tint)
        finally:
            self._loading = False
        self._edit_changed()

    def _temp_tint_changed(self, *_):
        if self._busy():
            return
        e = self.edit
        e.temp, e.tint, e.wb_preset = self.sl_temp.value(), self.sl_tint.value(), "custom"
        self._loading = True
        try:
            self.cmb_wb.setCurrentIndex(max(0, self.cmb_wb.findData("custom")))
        finally:
            self._loading = False
        self._edit_changed()

    def _tone_changed(self, *_):
        if self._busy():
            return
        e = self.edit
        e.exposure = round(self.sl_exp.value(), 2)
        e.black_point = int(round(self.sl_black.value()))
        e.rolloff = self.sw_rolloff.isChecked()
        e.highlights = self.sw_highlights.isChecked()
        e.ir = self.sw_ir.isChecked()
        self._update_render_controls()
        self._edit_changed()

    def _render_default(self):
        """(render, sony_curve) for newly added images: the last choice made."""
        render = str(self.settings.value("render", "default") or "default")
        curve = str(self.settings.value("sony_curve", "real") or "real")
        return (render if render in ("default", "sony") else "default",
                curve if curve in _SONY_CURVE_Y else "real")

    def _render_changed(self, *_):
        if self._busy():
            return
        e = self.edit
        e.render = "sony" if self.sw_sony.isChecked() else "default"
        e.sony_curve = self.cmb_sony_curve.currentData() or "real"
        self.settings.setValue("render", e.render)           # new images start with this choice
        self.settings.setValue("sony_curve", e.sony_curve)
        self._update_render_controls()
        if e.wb_preset in IDC_WB_PRESETS:                   # Sony colour uses IDC's own preset gains
            self._apply_wb_preset(e.wb_preset)
            return
        self._edit_changed()

    def _demosaic_default(self):
        """The demosaic for newly added images: the last choice made."""
        keys = [self.cmb_demosaic.itemData(i) for i in range(self.cmb_demosaic.count())]
        key = str(self.settings.value("demosaic", keys[0]) or keys[0])
        return key if key in keys else keys[0]

    def _update_demosaic_note(self):
        key = self.cmb_demosaic.currentData() or "idc"
        self.lbl_demosaic.setText(DEMOSAIC_NOTES.get(key, "") + " Used by every preview size and by "
                                  "full-size exports; half-size exports bin 2x2 pixels instead.")

    def _demosaic_changed(self, *_):
        self._update_demosaic_note()
        if self._busy():
            return
        self.edit.demosaic = self.cmb_demosaic.currentData() or "guided"
        self.settings.setValue("demosaic", self.edit.demosaic)    # new images start with this choice
        self._edit_changed()

    def _hot_changed(self, *_):
        if self._busy():
            return
        self.edit.hot_pixels = self.sw_hot.isChecked()
        self._update_hot_info()
        self._edit_changed()

    def _update_hot_info(self):
        if self.sensor is None or not self.edit.hot_pixels:
            self.lbl_hot.setText("")
            return
        n = self.sensor.get("hot_pixels_found")
        if n is None:
            self.lbl_hot.setText("Looking for hot pixels\u2026")
        elif n == 0:
            self.lbl_hot.setText("No hot or dead pixels found in this picture.")
        else:
            self.lbl_hot.setText(f"{n} hot or dead pixel{'s' if n != 1 else ''} replaced.")

    def _lens_changed(self, *_):
        if self._busy():
            return
        self.edit.ca_red = round(self.sl_ca_red.value(), 2)
        self.edit.ca_blue = round(self.sl_ca_blue.value(), 2)
        self.lbl_ca.setText("")
        self._edit_changed()

    def _auto_ca(self, *_):
        if self.sensor is None:
            return
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            found = estimate_lateral_ca(self.sensor)
        finally:
            QApplication.restoreOverrideCursor()
        if found is None:
            self.lbl_ca.setText("This picture has too little fine detail to measure the fringing. Set it "
                                "by eye at 1:1, on edges near a corner.")
            return
        e = self.edit
        e.ca_red, e.ca_blue = round(found[0], 1), round(found[1], 1)
        self._loading = True
        try:
            self.sl_ca_red.set_value(e.ca_red)
            self.sl_ca_blue.set_value(e.ca_blue)
        finally:
            self._loading = False
        self.lbl_ca.setText(f"Measured in this picture: red {e.ca_red:+.1f}, blue {e.ca_blue:+.1f}. It "
                            "changes with the zoom, so measure each focal length.")
        self._edit_changed()

    def _detail_changed(self, *_):
        if self._busy():
            return
        self.edit.defringe = int(round(self.sl_defringe.value()))
        self.edit.chroma_nr = int(round(self.sl_chroma_nr.value()))
        self._edit_changed()

    def _black_mode_changed(self, *_):
        if self._busy():
            return
        self.edit.black_mode = self.cmb_black.currentData()
        self._update_black_info()
        self._edit_changed()

    def _picked(self, u, v):
        """White-balance picker: (u, v) are fractions of the picture as displayed."""
        if self.sensor is None:
            return
        e = self.edit
        dn = self._quarter_dn()
        rgbe = apply_orientation(
            normalise_rgbe(dn, black_for(self.sensor, e.black_mode), self.sensor["white"]), e.rot_k, e.flip)
        h, w = rgbe.shape[:2]
        fw, fh = self._frame_size()
        x0, y0, x1, y1 = effective_crop(e.crop, e.straighten, fw, fh)
        col, row = frame_point_to_source(x0 + u * (x1 - x0), y0 + v * (y1 - y0), e.straighten, w, h)
        c, r = int(round(col)), int(round(row))
        patch = rgbe[max(0, r - 3):r + 4, max(0, c - 3):c + 4]
        tt = picked_white_balance(patch) if patch.size else None
        if tt is None:
            self.viewer.set_status("That spot is clipped or too dark to measure. Try a mid-grey area.")
            return
        e.temp, e.tint, e.wb_preset = tt[0], tt[1], "custom"
        self._loading = True
        try:
            self.cmb_wb.setCurrentIndex(max(0, self.cmb_wb.findData("custom")))
            self.sl_temp.set_value(e.temp)
            self.sl_tint.set_value(e.tint)
        finally:
            self._loading = False
        self._edit_changed()

    def _reset_edits(self, *_):
        if self.sensor is None:
            return
        entry = self.entries[self.current]
        keep = Edit(render=self.edit.render, sony_curve=self.edit.sony_curve,     # the rendering and
                    demosaic=self.edit.demosaic)                                   # demosaic choices stay
        entry.edit = self.edit = resolve_edit(keep, self.sensor, self._quarter_dn())
        if self.view.tool == "crop":
            self._set_tool("pan", refresh=False)
        self._load_controls()
        self._update_black_info()
        self.view.request_fit()
        self._request()

    # ------------------------------------------------------------ geometry
    def _set_tool(self, tool, refresh=True):
        v = self.viewer
        if self.sensor is None:
            tool = "pan"
        was = self.view.tool
        self.view.set_tool(tool)
        {"pan": v.btn_pan, "crop": v.btn_crop, "pick": v.btn_pick}[tool].setChecked(True)
        self.btn_crop_tool.setChecked(tool == "crop")
        self.btn_crop_tool.setText("Done cropping" if tool == "crop" else "Edit crop")
        if self.sensor is None or not refresh:
            return
        if (was == "crop") != (tool == "crop"):
            if was == "crop":
                self._crop_finished()
                self.entries[self.current].edited = True
            self._sync_proof_controls()
            self.view.request_fit()
            self._request()

    def _orient(self, what):
        if self.sensor is None:
            return
        e = self.edit
        if what in ("cw", "ccw"):
            op_rotate(e, what == "cw")
        else:
            op_flip(e, what == "fliph")
        self._loading = True
        try:
            self.sl_straighten.set_value(e.straighten)
            self.cmb_aspect.setCurrentIndex(0)
            self.view.set_aspect(None)
        finally:
            self._loading = False
        self.entries[self.current].edited = True
        self.view.request_fit()
        self._request()

    def _straighten_changed(self, *_):
        if self._busy():
            return
        self.edit.straighten = round(self.sl_straighten.value(), 2)
        self.entries[self.current].edited = True
        self._request()

    def _aspect_changed(self, *_):
        if self._busy():
            return
        value = self.cmb_aspect.currentData()
        fw, fh = self._frame_size()
        if value is None:
            self.view.set_aspect(None)
            return
        ratio = fw / float(fh) if value == "original" else float(value)
        self.view.set_aspect(fw / (ratio * fh))
        self.edit.crop = fit_aspect(effective_crop(self.edit.crop, self.edit.straighten, fw, fh), ratio, fw, fh)
        self.entries[self.current].edited = True
        if self.view.tool != "crop":
            self._set_tool("crop")
        else:
            self._request()

    def _crop_dragged(self, rect):
        if self.sensor is None:
            return
        self.edit.crop = tuple(rect)
        fw, fh = self._frame_size()
        cw, ch = round((rect[2] - rect[0]) * fw), round((rect[3] - rect[1]) * fh)
        self.lbl_crop.setText(f"Output: {cw} \u00D7 {ch} px")

    def _crop_finished(self):
        if self.sensor is None:
            return
        fw, fh = self._frame_size()
        self.edit.crop = effective_crop(self.edit.crop, self.edit.straighten, fw, fh)
        self.view.set_crop(self.edit.crop)
        self.entries[self.current].edited = True

    def _reset_crop(self, *_):
        if self.sensor is None:
            return
        self.edit.crop = (0.0, 0.0, 1.0, 1.0)
        self.edit.straighten = 0.0
        self._loading = True
        try:
            self.sl_straighten.set_value(0.0)
            self.cmb_aspect.setCurrentIndex(0)
            self.view.set_aspect(None)
        finally:
            self._loading = False
        self.view.request_fit()
        self._request()

    # ------------------------------------------------------------ preview quality
    def _preview_level_changed(self, *_):
        v = self.viewer
        key, label, _scale = RESOLUTIONS[int(v.sl_res.value())]
        v.lbl_res.setText(label)
        if key != self.preview_level:
            self.preview_level = key
            self._request()

    def _full_quality(self, *_):
        v = self.viewer
        v.sw_draft.setChecked(False)
        v.sw_proof.setChecked(False)
        v.sl_res.setValue(len(RESOLUTIONS) - 1)
        self._preview_level_changed()

    def _sync_proof_controls(self):
        """Show the current export settings in the export preview panel."""
        v, o = self.viewer, self.export_opts
        self._loading_proof = True
        try:
            v.cmb_proof_fmt.clear()
            for key in available_formats():
                v.cmb_proof_fmt.addItem(EXPORT_FORMATS[key][0].split("  (")[0], key)
            v.cmb_proof_fmt.setCurrentIndex(max(0, v.cmb_proof_fmt.findData(o.fmt)))
            field = QUALITY_FIELD.get(o.fmt)
            if field:
                lo, hi = QUALITY_RANGE.get(o.fmt, (1, 100))
                v.sl_proof_q.set_range(lo, hi, " dB" if o.fmt == "jp2" else "")
                v.sl_proof_q.set_value(getattr(o, field))
            v.sl_proof_q.setEnabled(bool(field) and format_lossy(o))
            v.lbl_proof_opts.setText(export_summary(o))
        finally:
            self._loading_proof = False
        on = v.sw_proof.isChecked()
        v.cmb_proof_view.setEnabled(on and format_lossy(o) and o.fmt != "dng")
        if not on:
            v.lbl_proof.setText("")
        elif o.fmt == "dng":
            v.lbl_proof.setText("A DNG stays raw: Lightroom or another raw converter develops it, so the "
                                "picture shown is this app's own rendering.")
        elif self.view.tool == "crop":
            v.lbl_proof.setText("The export preview comes back when you finish cropping.")

    def _show_proof_result(self, p):
        """What the export preview found: the file's size, and anything to know about it."""
        v = self.viewer
        w, h = p["size"]
        lines = [f"{w} \u00D7 {h} px"]
        if p["bytes"] is not None:
            lines[0] += f"   \u00B7   about {human_size(p['bytes'])}"
            if p["quality"] is not None:
                lines.append(f"Quality {p['quality']}{' dB' if p['fmt'] == 'jp2' else ''} is the highest "
                             f"that fits {p['target_kb']} KB.")
        else:
            lines.append("Lossless: the file holds exactly these pixels.")
        if p["out_of_gamut"] >= 0.001:
            lines.append(f"{p['out_of_gamut'] * 100:.1f}% of the picture has colours outside "
                         f"{COLOR_SPACES[p['space']]['label']} (magenta with Clipping on).")
        v.lbl_proof.setText("\n".join(lines))

    def _proof_toggled(self, *_):
        self._sync_proof_controls()
        if not self.viewer.sw_proof.isChecked():
            self._shown_proof = None
        self.view.request_fit()
        self._request()

    def _export_options_changed(self):
        self._save_export_options()
        self._sync_proof_controls()
        if self._proof_options() is not None:
            self._request_proof()

    def _proof_format_changed(self, *_):
        if self._loading_proof:
            return
        fmt = self.viewer.cmb_proof_fmt.currentData()
        if fmt and fmt != self.export_opts.fmt:
            self.export_opts.fmt = fmt
            self.view.request_fit()
            self._export_options_changed()

    def _proof_quality_changed(self, *_):
        if self._loading_proof:
            return
        field = QUALITY_FIELD.get(self.export_opts.fmt)
        if field:
            setattr(self.export_opts, field, int(round(self.viewer.sl_proof_q.value())))
            self._export_options_changed()

    def _export_settings(self, *_):
        """The full export settings, without exporting (the export preview follows them)."""
        estimate = None
        if self.sensor is not None:
            estimate = lambda o: estimate_export_size(self.sensor, self.edit, o, self.meta)  # noqa: E731
        dlg = ExportDialog(self, self.export_opts, "Export settings", estimate, settings_only=True)
        if dlg.exec():
            self.export_opts = dlg.options()
            self.view.request_fit()
            self._export_options_changed()

    # ------------------------------------------------------------ batch: sync + export
    def _sync(self, *_):
        if self.sensor is None:
            return
        targets = [p for p in self._checked_paths() if p != self.current]
        if not targets:
            QMessageBox.information(self, "Sync edits", "Tick the other images you want to copy these edits to.")
            return
        dlg = SyncDialog(self, len(targets))
        if not dlg.exec():
            return
        groups = dlg.groups()
        if not groups:
            return
        for p in targets:
            entry = self.entries[p]
            entry.edit = sync_edit(self.edit, entry.edit, groups)
            entry.edited = True
            self._queue_thumb(p)
        self.viewer.set_status(f"Edits copied to {len(targets)} image{'s' if len(targets) != 1 else ''}")

    def _export(self, ticked):
        paths = self._checked_paths() if ticked else ([self.current] if self.current else [])
        if not paths:
            QMessageBox.information(self, "Export", "Tick some images in the library first." if ticked
                                    else "Open an image first.")
            return
        heading = (f"Export {len(paths)} ticked image{'s' if len(paths) != 1 else ''}" if ticked
                   else f"Export {os.path.basename(paths[0])}")
        estimate = None
        if self.sensor is not None:
            estimate = lambda o: estimate_export_size(self.sensor, self.edit, o, self.meta)  # noqa: E731
        dlg = ExportDialog(self, self.export_opts, heading, estimate)
        if not dlg.exec():
            return
        self.export_opts = dlg.options()
        self._export_options_changed()
        self._run_export(paths, self.export_opts)

    def _run_export(self, paths, opts):
        """Start the export on its own thread; the progress window only reports."""
        if self._export_thread is not None:
            QMessageBox.information(self, "Export", "An export is already running.")
            return
        if opts.out_dir:
            try:
                os.makedirs(opts.out_dir, exist_ok=True)
            except OSError as exc:
                QMessageBox.critical(self, "Export", f"Cannot use that folder:\n{exc}")
                return
        jobs = []
        for path in paths:                       # snapshot the edits: later changes do not affect this run
            current = path == self.current and self.sensor is not None
            jobs.append((path, self.entries[path].edit.copy(), self.sensor if current else None,
                         self.meta if current and opts.copy_metadata else None))
        progress = QProgressDialog("Preparing\u2026", "Cancel", 0, len(jobs), self)
        progress.setWindowTitle("Exporting")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        thread = ExportThread(jobs, opts, self)
        thread.progress.connect(self._export_progress)
        thread.finished_all.connect(self._export_finished)
        progress.canceled.connect(thread.cancel)
        self._export_thread, self._export_dialog = thread, progress
        self._update_enabled()
        self._update_counts()
        progress.setValue(0)
        progress.show()
        thread.start()

    def _export_progress(self, index, name):
        dialog = self._export_dialog
        if dialog is None or dialog.wasCanceled():
            return
        dialog.setValue(index)
        dialog.setLabelText(f"Exporting {index + 1} of {dialog.maximum()}\n{name}")

    def _export_finished(self, done, errors):
        thread, dialog = self._export_thread, self._export_dialog
        total = thread.total if thread is not None else len(done) + len(errors)
        self._export_thread = self._export_dialog = None
        if thread is not None:
            thread.wait(5000)
            thread.deleteLater()
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        self._update_enabled()
        self._update_counts()

        box = QMessageBox(self)
        box.setWindowTitle("Export finished")
        text = f"Exported {len(done)} of {total} image{'s' if total != 1 else ''}."
        if done:
            text += f"\n\n{os.path.dirname(done[-1])}"
        if errors:
            box.setIcon(QMessageBox.Icon.Warning)
            text += f"\n\n{len(errors)} failed; see the details."
            box.setDetailedText("\n".join(errors))
        else:
            box.setIcon(QMessageBox.Icon.Information)
        box.setText(text)
        open_button = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole) if done else None
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if open_button is not None and box.clickedButton() == open_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(done[-1])))

    # ------------------------------------------------------------ closing
    def stop_threads(self):
        """Stop the background threads (safe to call more than once). An export in
        progress finishes the image it is writing first."""
        if self._export_thread is not None:
            self._export_thread.cancel()
            self._export_thread.wait(60000)
        for worker in (self.preview_worker, self.thumb_worker):
            if worker.isRunning():
                worker.shutdown()

    def closeEvent(self, event):
        self.hide()                              # disappear at once; a job in flight may need a moment to end
        self.stop_threads()
        super().closeEvent(event)

    # ------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)


def _install_error_dialog():
    """Show unexpected errors in a window (with the traceback to copy) instead of
    closing the program."""
    state = {"showing": False, "seen": set()}

    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        sys.stderr.write(text)
        if state["showing"] or text in state["seen"]:
            return                       # e.g. an error inside painting would otherwise repeat forever
        state["seen"].add(text)
        state["showing"] = True
        try:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("Unexpected error")
            box.setText(f"{kind.__name__}: {value}\n\nThe program is still running. "
                        "Open the details to copy the full error.")
            box.setDetailedText(text)
            box.exec()
        except Exception:
            pass
        finally:
            state["showing"] = False
    sys.excepthook = hook


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(build_stylesheet())
    _install_error_dialog()
    window = MainWindow()
    screen = app.primaryScreen()
    if screen is not None:               # never open larger than the screen
        area = screen.availableGeometry()
        window.resize(max(window.minimumWidth(), min(1500, area.width() - 40)),
                      max(window.minimumHeight(), min(930, area.height() - 60)))
    window.show()
    app.aboutToQuit.connect(window.stop_threads)
    startup = [p for p in sys.argv[1:] if os.path.exists(p)]
    if startup:
        window.add_paths(startup)
    code = app.exec()
    window.stop_threads()
    sys.exit(code)


if __name__ == '__main__':
    main()
