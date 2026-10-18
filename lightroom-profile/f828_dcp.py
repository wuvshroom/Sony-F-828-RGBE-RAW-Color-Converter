#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Part of the Sony F-828 RGBE RAW Color Converter, https://github.com/wuvshroom/Sony-F-828-RGBE-RAW-Color-Converter (see LICENSE there).
"""Sony DSC-F828 camera profiles (.dcp) for Adobe Camera Raw and Lightroom that reproduce
the colour and tone of Sony Image Data Converter SR 1.1 (IDC), "Real" and "Standard".

    python3 f828_dcp.py build [--converter f828_converter19.py] SRF-or-folder ...
        rebuild "Sony DSC-F828 IDC Real.dcp" and "Sony DSC-F828 IDC Standard.dcp" from the
        raw files (fits the profile to the converter's model of IDC; needs rawpy and scipy)
    python3 f828_dcp.py info "Sony DSC-F828 IDC Real.dcp"
        print a profile and check that it reads back
    python3 f828_dcp.py tags FILE.SRF [--mode real|standard]
        print what build_dcp_tags() returns for one raw file
    python3 f828_dcp.py retag --camera-model "NAME" *.dcp
        give the profiles another camera name (if Lightroom calls the F828 something else)

From the converter's DNG export (PART 1 of f828_converter19.py):

    import f828_dcp
    tags.update(f828_dcp.build_dcp_tags(sensor["camera"], mode))   # just before write_raw_tiff()

What a profile holds (four planes, R G B E, the order of the converter's DNGs):
  ColorMatrix1/2     Adobe's XYZ -> camera matrix for the F828 (the converter's XYZ_TO_RGBE),
                     the same at both illuminants, so Lightroom's white balance (neutral, Temp,
                     Tint) is the converter's colour model exactly.
  ForwardMatrix1/2   IDC's own camera matrix (camera registers 0x634..) and its after-matrix
                     white balance, exact at illuminant A and at D65.
  ProfileHueSatMap   90 hues x 16 saturations, two of them (A and D65): the part of IDC's colour
                     that changes with the white balance (its chroma registers do).
  ProfileLookTable   90 hues x 16 saturations x 16 values (sRGB-encoded values): the rest of
                     IDC's colour, which depends on brightness (tone curve per channel, luma /
                     colour-difference processing, chroma suppression, clipping).
  ProfileToneCurve   IDC's neutral response: the Sony 12 -> 10 bit curve, luma contrast and the
                     0.9945 white of its YCbCr -> RGB step.
  DefaultBlackRender 1 (none): IDC has no black clip beyond its curve.
"""
import argparse
import glob
import math
import os
import re
import struct
import sys
import time
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import dng_ref as R                                   # noqa: E402  (the reference renderer)

UNIQUE_CAMERA_MODEL = "Sony DSC-F828"
PROFILE_NAME = {"real": "Sony DSC-F828 IDC Real", "standard": "Sony DSC-F828 IDC Standard"}
PROFILE_COPYRIGHT = ("Fitted to match Sony Image Data Converter SR 1.1. (c) 2026 wuvshroom, "
                     "PolyForm Noncommercial 1.0.0, https://github.com/wuvshroom/Sony-F-828-RGBE-RAW-Color-Converter")
MODES = ("real", "standard")


def dcp_filename(mode):
    return PROFILE_NAME[mode] + ".dcp"


# --------------------------------------------------------------------------------------------
# the camera

# Adobe's XYZ (D65) -> camera matrix for the DSC-F828, rows R G B E (dcraw / LibRaw adobe_coeff;
# the converter's XYZ_TO_RGBE).
XYZ_TO_RGBE = np.array([[0.7924, -0.1910, -0.0777],
                        [-0.8226, 1.5459, 0.2998],
                        [-0.1517, 0.2199, 0.6818],
                        [-0.7242, 1.1401, 0.3481]])
# IDC's RGBE -> "Sony RGB" matrix (camera registers 0x634..0x64a, / 1024). The same in all 13
# test files; build checks every file against it.
SONY_MATRIX = np.array([[1570, 0, 188, -583],
                        [-276, 1061, -558, 609],
                        [0, -174, 996, 0]], dtype=np.float64) / 1024.0
SONY_RGB_TO_PCS = R.SRGB_TO_PCS                       # IDC's output primaries are sRGB's
XY_A = (0.44757, 0.40745)                             # CIE illuminant A
XY_D65 = R.D65_XY
WHITE_DN = 16368.0                                    # 0x3FF0, the F828's 14-bit clipping point
DSP_INPUT_SCALE = 0.25                                # 14-bit sensor numbers -> 12-bit DSP units
BLACK_REF = 494.0
K_REF = (WHITE_DN - BLACK_REF) * DSP_INPUT_SCALE     # DSP units per normalised camera unit
XY_REF = R.temp_tint_to_xy(5000.0, 15.0)              # the white the profile's tone is set at


def neutral_for_xy(xy, cm=XYZ_TO_RGBE):
    """Camera neutral (largest = 1) of a light of chromaticity xy, through the colour matrix,
    which is what Lightroom computes from Temp / Tint."""
    n = np.asarray(cm, dtype=np.float64) @ R.xy_to_xyz(xy)
    return n / n.max()


def post_wb_for_neutral(n, matrix=SONY_MATRIX):
    """IDC's after-matrix gains (R, G, B, G = 1) that make the camera neutral n grey."""
    mn = np.asarray(matrix, dtype=np.float64) @ np.asarray(n, dtype=np.float64)
    return mn[1] / mn


def forward_matrix_for(xy, matrix=SONY_MATRIX, cm=XYZ_TO_RGBE):
    """The ForwardMatrix that is exactly IDC's matrix + white balance for the white xy:
    PCS = SonyRGB->PCS . diag(wb) . M . diag(n), scaled so that (1, 1, 1, 1) is D50."""
    n = neutral_for_xy(xy, cm)
    fm = SONY_RGB_TO_PCS @ np.diag(post_wb_for_neutral(n, matrix)) @ np.asarray(matrix, np.float64) @ np.diag(n)
    return np.diag(R.PCS_XYZ / (fm @ np.ones(fm.shape[1]))) @ fm


def asshot_xy(cam, cm=XYZ_TO_RGBE):
    """The chromaticity whose camera neutral (cm . XYZ(xy)) gives exactly the after-matrix white
    balance in the camera registers (0x64c / 0x650): IDC's as-shot white balance."""
    M = np.asarray(cam["matrix"], dtype=np.float64)
    pre = np.asarray(cam["pregain"], dtype=np.float64)
    target = np.log(np.asarray(cam["wb"], dtype=np.float64)[[0, 2]])

    def f(xy):
        return np.log(post_wb_for_neutral(neutral_for_xy(xy, cm) * pre, M)[[0, 2]]) - target

    xy = np.array(R.D50_XY)
    for _ in range(60):
        r = f(xy)
        if np.abs(r).max() < 1e-12:
            break
        J = np.stack([(f(xy + d) - r) / 1e-7 for d in (np.array([1e-7, 0.0]), np.array([0.0, 1e-7]))], 1)
        step = np.linalg.solve(J, -r)
        xy = xy + np.clip(step, -0.05, 0.05)
    return float(xy[0]), float(xy[1])


def asshot_neutral(cam, cm=XYZ_TO_RGBE):
    """AsShotNeutral for a DNG of this shot (largest = 1)."""
    return neutral_for_xy(asshot_xy(cam, cm), cm)


def asshot_temp_tint(cam, cm=XYZ_TO_RGBE):
    """IDC's as-shot white balance as Lightroom's Temperature (K) and Tint for this profile."""
    return R.xy_to_temp_tint(asshot_xy(cam, cm))


def exposure_offset(cam, xy=None, cm=XYZ_TO_RGBE, white=WHITE_DN):
    """Exposure (EV) that brings one shot to the profile's reference: IDC's digital-gain register,
    the white-balance-dependent scale of its G channel ((M . n)_G, up to +-1.7 %) and the black
    level. A profile cannot hold this (its tables leave neutrals alone), so it goes into the
    DNG's BaselineExposure or the sidecar's Exposure."""
    if xy is None:
        xy = asshot_xy(cam, cm)
    n = neutral_for_xy(xy, cm)
    mng = float((np.asarray(cam["matrix"], np.float64) @ (n * np.asarray(cam["pregain"], np.float64)))[1])
    k = (white - float(np.mean(cam["black"]))) * DSP_INPUT_SCALE
    mng_ref = float((SONY_MATRIX @ neutral_for_xy(XY_REF))[1])
    return float(np.log2(float(cam["gain"]) * mng * k / (mng_ref * K_REF)))


# --------------------------------------------------------------------------------------------
# .dcp files

T_BYTE, T_ASCII, T_SHORT, T_LONG, T_RATIONAL, T_SRATIONAL, T_FLOAT = 1, 2, 3, 4, 5, 10, 11
TAG = dict(UniqueCameraModel=50708, ColorMatrix1=50721, ColorMatrix2=50722, AsShotNeutral=50728,
           BaselineExposure=50730, CalibrationIlluminant1=50778, CalibrationIlluminant2=50779,
           ProfileName=50936, ProfileHueSatMapDims=50937, ProfileHueSatMapData1=50938,
           ProfileHueSatMapData2=50939, ProfileToneCurve=50940, ProfileEmbedPolicy=50941,
           ProfileCopyright=50942, ForwardMatrix1=50964, ForwardMatrix2=50965,
           ProfileLookTableDims=50981, ProfileLookTableData=50982, ProfileHueSatMapEncoding=51107,
           ProfileLookTableEncoding=51108, BaselineExposureOffset=51109, DefaultBlackRender=51110)
TAG_NAME = {v: k for k, v in TAG.items()}
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_TYPE_FMT = {3: "H", 4: "I", 8: "h", 9: "i", 11: "f", 12: "d"}


def _rationals(values, den=10000, signed=True):
    return (T_SRATIONAL if signed else T_RATIONAL, tuple((int(round(float(v) * den)), den) for v in np.ravel(values)))


def _ascii(text):
    return (T_ASCII, str(text).encode("ascii", "replace") + b"\0")


def profile_tags(p):
    """Profile -> {tag: (type, value)}, the value format of the converter's serialise_ifd():
    ASCII as bytes with the NUL, SHORT / LONG / FLOAT as tuples, (S)RATIONAL as (num, den) pairs."""
    t = {TAG["UniqueCameraModel"]: _ascii(p.unique_camera_model),
         TAG["ProfileName"]: _ascii(p.name),
         TAG["ProfileEmbedPolicy"]: (T_LONG, (int(p.embed_policy),)),
         TAG["CalibrationIlluminant1"]: (T_SHORT, (int(p.illuminant1),)),
         TAG["ColorMatrix1"]: _rationals(p.cm1)}
    if p.copyright:
        t[TAG["ProfileCopyright"]] = _ascii(p.copyright)
    if p.cm2 is not None:
        t[TAG["CalibrationIlluminant2"]] = (T_SHORT, (int(p.illuminant2),))
        t[TAG["ColorMatrix2"]] = _rationals(p.cm2)
    if p.fm1 is not None:
        t[TAG["ForwardMatrix1"]] = _rationals(p.fm1)
    if p.fm2 is not None:
        t[TAG["ForwardMatrix2"]] = _rationals(p.fm2)
    if p.hsm1 is not None:
        nv, nh, ns, _ = np.shape(p.hsm1)
        t[TAG["ProfileHueSatMapDims"]] = (T_LONG, (nh, ns, nv))
        t[TAG["ProfileHueSatMapData1"]] = (T_FLOAT, tuple(np.asarray(p.hsm1, np.float32).ravel().tolist()))
        if p.hsm2 is not None:
            t[TAG["ProfileHueSatMapData2"]] = (T_FLOAT, tuple(np.asarray(p.hsm2, np.float32).ravel().tolist()))
        t[TAG["ProfileHueSatMapEncoding"]] = (T_LONG, (int(p.hsm_encoding),))
    if p.look is not None:
        nv, nh, ns, _ = np.shape(p.look)
        t[TAG["ProfileLookTableDims"]] = (T_LONG, (nh, ns, nv))
        t[TAG["ProfileLookTableData"]] = (T_FLOAT, tuple(np.asarray(p.look, np.float32).ravel().tolist()))
        t[TAG["ProfileLookTableEncoding"]] = (T_LONG, (int(p.look_encoding),))
    if p.tone_curve is not None:
        t[TAG["ProfileToneCurve"]] = (T_FLOAT, tuple(np.asarray(p.tone_curve, np.float32).ravel().tolist()))
    t[TAG["BaselineExposureOffset"]] = _rationals([p.baseline_exposure_offset], 100)   # kept to 0.01 EV
    t[TAG["DefaultBlackRender"]] = (T_LONG, (int(p.default_black_render),))
    return t


def _encode(typ, value):
    if typ in (1, 2, 6, 7):
        data = bytes(value)
        return len(data), data
    if typ in (5, 10):
        flat = [int(x) for pair in value for x in pair]
        return len(value), struct.pack("<" + ("i" if typ == 10 else "I") * len(flat), *flat)
    cast = float if typ in (11, 12) else int
    return len(value), struct.pack("<" + _TYPE_FMT[typ] * len(value), *[cast(v) for v in value])


def dcp_bytes(tags):
    """{tag: (type, value)} -> a .dcp file: 'IIRC' (byte order II, magic 0x4352), the offset of
    the one IFD (8), the IFD, then the out-of-line values (word aligned). Offsets count from
    the start of the file, as dng_camera_profile_info::ParseExtended reads them."""
    order = sorted(tags)
    data_start = 8 + 2 + 12 * len(order) + 4
    head, tail = struct.pack("<H", len(order)), b""
    for tag in order:
        typ, value = tags[tag]
        count, data = _encode(typ, value)
        if len(data) <= 4:
            field = data + b"\0" * (4 - len(data))
        else:
            tail += b"\0" * (len(tail) % 2)
            field = struct.pack("<I", data_start + len(tail))
            tail += data
        head += struct.pack("<HHI", tag, typ, count) + field
    return b"IIRC" + struct.pack("<I", 8) + head + struct.pack("<I", 0) + tail


def write_dcp(path, profile):
    blob = dcp_bytes(profile_tags(profile))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)
    return len(blob)


def read_tags(blob):
    """Tags of a .dcp ('IIRC') or of IFD0 of a TIFF / DNG (either byte order).
    -> {tag: (type, count, values)}; ASCII as bytes, (S)RATIONAL as (num, den) pairs."""
    bo = {b"II": "<", b"MM": ">"}.get(bytes(blob[:2]))
    if bo is None:
        raise ValueError("not a TIFF / DCP file")
    magic = struct.unpack(bo + "H", blob[2:4])[0]
    if magic not in (42, 0x4352, 0x4f52, 0x5352):
        raise ValueError("not a TIFF / DCP file (magic %#x)" % magic)
    off = struct.unpack(bo + "I", blob[4:8])[0]
    n = struct.unpack(bo + "H", blob[off:off + 2])[0]
    out = {}
    for i in range(n):
        pos = off + 2 + 12 * i
        tag, typ, cnt = struct.unpack(bo + "HHI", blob[pos:pos + 8])
        if typ not in _TYPE_SIZE:
            continue
        size = _TYPE_SIZE[typ] * cnt
        pos += 8
        if size > 4:
            pos = struct.unpack(bo + "I", blob[pos:pos + 4])[0]
        raw = bytes(blob[pos:pos + size])
        if len(raw) < size:
            raise ValueError("tag %d runs past the end of the file" % tag)
        if typ in (1, 2, 6, 7):
            val = raw
        elif typ in (5, 10):
            flat = struct.unpack(bo + ("i" if typ == 10 else "I") * (2 * cnt), raw)
            val = tuple((flat[2 * k], flat[2 * k + 1]) for k in range(cnt))
        else:
            val = struct.unpack(bo + _TYPE_FMT[typ] * cnt, raw)
        out[tag] = (typ, cnt, val)
    return out


def tags_to_profile(tags):
    """{tag: (type, count, values)} -> dng_ref.Profile."""
    def get(name, default=None):
        return tags[TAG[name]][2] if TAG[name] in tags else default

    def mat(name, rows):
        v = get(name)
        if v is None:
            return None
        a = np.array([num / den for num, den in v], dtype=np.float64)
        return a.reshape(rows, -1)

    def text(name):
        return get(name, b"").split(b"\0")[0].decode("latin-1")

    cm1 = np.array([num / den for num, den in get("ColorMatrix1")], dtype=np.float64)
    planes = cm1.size // 3
    kw = dict(unique_camera_model=text("UniqueCameraModel"), name=text("ProfileName"),
              copyright=text("ProfileCopyright"), embed_policy=get("ProfileEmbedPolicy", (0,))[0],
              illuminant1=get("CalibrationIlluminant1", (0,))[0], illuminant2=get("CalibrationIlluminant2", (0,))[0],
              cm1=cm1.reshape(planes, 3), cm2=mat("ColorMatrix2", planes),
              fm1=mat("ForwardMatrix1", 3), fm2=mat("ForwardMatrix2", 3),
              hsm_encoding=get("ProfileHueSatMapEncoding", (0,))[0],
              look_encoding=get("ProfileLookTableEncoding", (0,))[0],
              default_black_render=get("DefaultBlackRender", (0,))[0])
    for dims_name, datas in (("ProfileHueSatMapDims", (("hsm1", "ProfileHueSatMapData1"), ("hsm2", "ProfileHueSatMapData2"))),
                             ("ProfileLookTableDims", (("look", "ProfileLookTableData"),))):
        d = get(dims_name)
        if d is None:
            continue
        nh, ns, nv = d[0], d[1], (d[2] if len(d) > 2 else 1)
        for key, name in datas:
            if get(name) is not None:
                kw[key] = np.array(get(name), dtype=np.float64).reshape(nv, nh, ns, 3)
    if get("ProfileToneCurve") is not None:
        kw["tone_curve"] = np.array(get("ProfileToneCurve"), dtype=np.float64).reshape(-1, 2)
    beo = get("BaselineExposureOffset")
    kw["baseline_exposure_offset"] = beo[0][0] / beo[0][1] if beo else 0.0
    return R.Profile(**kw)


def read_dcp(path):
    with open(path, "rb") as f:
        return tags_to_profile(read_tags(f.read()))


# --------------------------------------------------------------------------------------------
# the profile tags for the converter's DNG export

_PROFILE_CACHE = {}


def _profile_file_tags(mode, dcp_dir=None):
    path = os.path.join(dcp_dir or HERE, dcp_filename(mode))
    key = (path, os.path.getmtime(path))
    if key not in _PROFILE_CACHE:
        with open(path, "rb") as f:
            tags = read_tags(f.read())
        _PROFILE_CACHE.clear()
        _PROFILE_CACHE[key] = (tags, tags_to_profile(tags))
    return _PROFILE_CACHE[key]


def build_dcp_tags(cam, mode=None, dcp_dir=None, exposure=True, neutral=None):
    """Tags that embed the IDC profile in a DNG's IFD0, plus the shot's exact white balance.

    cam       the camera registers, as read_srf_camera() returns them (sensor["camera"])
    mode      "real" or "standard"; None takes the mode the shot was taken in (cam["mode"])
    dcp_dir   where the .dcp files are (default: next to this module)
    exposure  also return BaselineExposure (exposure_offset(): IDC's digital gain and the
              white-balance-dependent brightness of its G channel)
    neutral   a camera neutral (R, G, B, E) to write instead of IDC's as-shot one, e.g. the
              converter's own white balance (1 / wb_from_temp_tint(temp, tint)); the
              BaselineExposure then follows that white

    Returns {tag: (tiff_type, value)} in the converter's serialise_ifd() format: every profile
    tag of the .dcp except UniqueCameraModel (the DNG names its own camera), AsShotNeutral
    (RATIONAL, largest = 1, from the registers and in the profile's colour-matrix space, so
    Lightroom maps it back to exactly IDC's white) and BaselineExposure (SRATIONAL, in 0.01 EV:
    dng_negative::SetBaselineExposure keeps no more). Apply it last: it replaces the converter's
    ColorMatrix1, CalibrationIlluminant1, ForwardMatrix1, ProfileName, ProfileEmbedPolicy,
    AsShotNeutral, BaselineExposure and, if present, ProfileToneCurve / DefaultBlackRender."""
    if mode is None:
        mode = cam.get("mode") or "real"
    if mode not in PROFILE_NAME:
        raise ValueError("mode must be 'real' or 'standard'")
    tags, prof = _profile_file_tags(mode, dcp_dir)
    out = {tag: (typ, val) for tag, (typ, cnt, val) in tags.items() if tag != TAG["UniqueCameraModel"]}
    spec = R.ColorSpec(prof)
    cm = spec.cm1                                 # the matrix as the SDK reads it (CM1 = CM2)
    if neutral is None:
        xy = asshot_xy(cam, cm)
        n = neutral_for_xy(xy, cm)
    else:
        n = np.asarray(neutral, dtype=np.float64)
        n = n / n.max()
        xy = spec.neutral_to_xy(n)
    out[TAG["AsShotNeutral"]] = _rationals(n, 1000000, signed=False)
    if exposure:                                  # the SDK keeps BaselineExposure to 0.01 EV
        out[TAG["BaselineExposure"]] = _rationals([round(exposure_offset(cam, xy, cm), 2)], 100)
    return out


def retag(path, camera_model=None, name=None, out=None):
    """Rewrite a .dcp with another UniqueCameraModel and / or ProfileName (no refit)."""
    with open(path, "rb") as f:
        tags = read_tags(f.read())
    flat = {tag: (typ, val) for tag, (typ, cnt, val) in tags.items()}
    if camera_model:
        flat[TAG["UniqueCameraModel"]] = _ascii(camera_model)
    if name:
        flat[TAG["ProfileName"]] = _ascii(name)
    blob = dcp_bytes(flat)
    dst = out or path
    with open(dst + ".tmp", "wb") as f:
        f.write(blob)
    os.replace(dst + ".tmp", dst)
    return dst


# --------------------------------------------------------------------------------------------
# PART 2: rebuilding the profiles. Needs the F828 converter's processing code (PART 1 of
# f828_converter.py: SRF reading, the IDC DSP model), rawpy and scipy.

def find_converter():
    cands = glob.glob(os.path.join(HERE, "f828_converter*.py")) + \
        glob.glob(os.path.join(HERE, "..", "f828_converter*.py")) + \
        glob.glob(os.path.join(HERE, "..", "converter", "f828_converter*.py"))
    num = lambda p: int((re.findall(r"(\d+)", os.path.basename(p)) or ["0"])[-1])
    return max(cands, key=num) if cands else None


def load_converter(path):
    """PART 1 (processing) of a converter file, without its Qt interface."""
    import types
    src = open(path, encoding="utf-8").read().split("# PART 2  -  INTERFACE")[0]
    out, skip = [], False
    for ln in src.splitlines():
        if ln.startswith("from PyQt6"):
            skip = ln.rstrip().endswith(",") or ("(" in ln and ")" not in ln)
            continue
        if skip:
            if ")" in ln:
                skip = False
            continue
        out.append(ln)
    m = types.ModuleType("f828_converter")
    m.__file__ = path
    exec(compile("\n".join(out), path, "exec"), m.__dict__)
    for name in ("read_sensor", "read_srf_camera", "render_sony_dsp", "cfa_to_superpixel", "normalise_rgbe",
                 "block_mean", "DSP_CURVE_X", "XYZ_TO_RGBE"):
        if not hasattr(m, name):
            raise ValueError("%s has no %s (not an F828 converter, or too old)" % (path, name))
    if not np.allclose(np.asarray(m.XYZ_TO_RGBE, np.float64), XYZ_TO_RGBE, atol=1e-6):
        raise ValueError("the converter's XYZ_TO_RGBE differs from this module's")
    return m


# chart shots (ColorChecker-like target on a monitor): patch centres in the 816 x 612 preview
CHART_FILES = ("DSC00681", "DSC00682", "DSC00683", "DSC00685", "DSC00686")
CHART_CX = [66.5, 135.5, 203.5, 271.5, 339.5, 407.5, 523, 632, 741.5]
CHART_CY = [187.5, 255.5, 323.5, 391.5]
CHART_RAMP_Y, CHART_RAMP_X0, CHART_RAMP_DX = 484.5, 146.0, 31.0
KIND_PIXEL, KIND_EXTENDED, KIND_SURFACE, KIND_CHART = 0, 1, 2, 3


def chart_patches(sp):
    """Mean camera values of the chart's 36 patches and 21 grey-ramp steps (half-res image)."""
    pat = []
    for y in CHART_CY:
        for x in CHART_CX:
            Y, X = int((4 * y + 4) / 2), int((4 * x + 9) / 2)
            pat.append(sp[Y - 12:Y + 12, X - 12:X + 12].reshape(-1, 4).mean(0))
    for i in range(21):
        Y, X = int((4 * CHART_RAMP_Y + 4) / 2), int((4 * (CHART_RAMP_X0 + i * CHART_RAMP_DX) + 9) / 2)
        pat.append(sp[Y - 10:Y + 10, X - 10:X + 10].reshape(-1, 4).mean(0))
    return np.array(pat)


def surface_samples(n_samples, xy_white, rng, cm=XYZ_TO_RGBE, cmax=100.0):
    """Plausible surface colours (CIELAB, L 3..97, chroma up to cmax, inside ProPhoto) seen under
    a light of chromaticity xy_white, as normalised camera values through the colour matrix."""
    L = rng.uniform(3, 97, n_samples)
    C = cmax * np.sqrt(rng.uniform(0, 1, n_samples))
    H = rng.uniform(0, 2 * np.pi, n_samples)
    fy = (L + 16) / 116
    fx, fz = fy + C * np.cos(H) / 500, fy - C * np.sin(H) / 200
    finv = lambda t: np.where(t > 6 / 29, t ** 3, 3 * (6 / 29) ** 2 * (t - 4 / 29))
    xyz50 = np.stack([finv(fx) * R.PCS_XYZ[0], finv(fy), finv(fz) * R.PCS_XYZ[2]], 1)
    ok = (xyz50 @ R.PCS_TO_PROPHOTO.T).min(1) >= 0
    xyz = xyz50[ok] @ R.bradford_white_map(R.D50_XY, xy_white).T
    camv = xyz @ np.asarray(cm).T
    camv /= (np.asarray(cm) @ R.xy_to_xyz(xy_white)).max()
    camv *= np.exp(rng.uniform(np.log(0.3), 0, (len(camv), 1)))
    return camv[camv.min(1) >= 0]


def collect_samples(conv, path, log=print):
    """Training data from one SRF: 40000 quarter-resolution pixels, 20000 of them pushed to
    more chroma / other brightness, surface colours under the shot's light and, on the chart
    shots, the chart patches."""
    stem = os.path.basename(path)[:8].upper()
    rng = np.random.default_rng(zlib.crc32(stem.encode()))
    s = conv.read_sensor(path)
    cam = s["camera"]
    if cam is None:
        raise ValueError("%s: no camera registers (not an F828 .SRF?)" % path)
    if not np.allclose(cam["matrix"], SONY_MATRIX, atol=1e-6):
        log("  warning: %s has a different IDC matrix; the profile's ForwardMatrix uses the usual one" % stem)
    sp = conv.normalise_rgbe(conv.cfa_to_superpixel(s["cfa"], s["cmap"]), cam["black"], s["white"])
    q = conv.block_mean(sp, 2).reshape(-1, 4)
    ok = (q.max(1) < 0.97) & (q.max(1) > 0.0005)
    qs = q[ok][rng.choice(int(ok.sum()), min(40000, int(ok.sum())), replace=False)].astype(np.float64)
    xy = asshot_xy(cam)
    n = neutral_for_xy(xy)
    b = qs[rng.choice(len(qs), 20000)] / n
    t = b.mean(1, keepdims=True)
    ext = n * (t + rng.uniform(1.0, 2.5, (len(b), 1)) * (b - t)) * np.exp(rng.uniform(np.log(0.3), np.log(3.0), (len(b), 1)))
    ext = ext[(ext.min(1) >= 0) & (ext.max(1) < 0.97)]
    srf = surface_samples(16000, xy, rng)
    parts = [qs, ext, srf]
    wts = [1.0, 0.5, 0.4]
    if stem in CHART_FILES:
        parts.append(chart_patches(sp))
        wts.append(300.0)
    camv = np.concatenate(parts)
    w = np.concatenate([np.full(len(p), wt) for p, wt in zip(parts, wts)])
    kind = np.concatenate([np.full(len(p), k, np.int8) for k, p in enumerate(parts)])
    keep = (camv / n).max(1) <= 0.999                 # leave clipped highlights out
    return dict(stem=stem, path=path, cam=cam, white=float(s["white"]), xy=xy,
                camv=camv[keep], w=w[keep], kind=kind[keep])


def idc_target(conv, camv, cam, mode, xy, chroma=None, wb=None, normalise=True):
    """IDC's sRGB output (encoded, 0..1) for normalised camera values, through the converter's
    model of its DSP. normalise=True takes out the shot's exposure_offset()."""
    cam2 = dict(cam)
    if chroma:
        cam2.update(chroma)
    k = (WHITE_DN - np.asarray(cam["black"], np.float64)) * DSP_INPUT_SCALE * float(cam["gain"]) * \
        np.asarray(cam["pregain"], np.float64)
    if normalise:
        k = k / 2.0 ** exposure_offset(cam, xy)
    lin = conv.render_sony_dsp((camv * k).astype(np.float32), cam2, cam["wb"] if wb is None else wb, mode)
    return R.srgb_encode(lin.astype(np.float64))


CHROMA_KEYS = ("cr_cross", "cb_cross", "cb_gain", "cr_gain")


def chroma_model(files, mode):
    """Chroma registers of `mode` for any white: piecewise linear in mired between the shots of
    that mode, constant outside them."""
    own = sorted((1e6 / R.xy_to_temp_tint(f["xy"])[0], f["cam"]) for f in files if f["cam"]["mode"] == mode)
    if not own:
        return None
    ms = np.array([m for m, _ in own])

    def at(xy):
        mired = 1e6 / R.xy_to_temp_tint(xy)[0]
        return {key: tuple(int(round(np.interp(mired, ms, [float(cm[key][j]) for _, cm in own]))) for j in range(2))
                for key in CHROMA_KEYS}
    return at


def neutral_tone(conv, cam_ref, mode, n=4097):
    """IDC's neutral response on the DNG's linear scale (1 = camera white) at the reference white."""
    cam = dict(cam_ref)
    cam["black"] = np.full(4, BLACK_REF, np.float32)
    cam["gain"] = 1.0
    cam["pregain"] = np.ones(4, np.float32)
    v = np.linspace(0, 1, n)
    nref = neutral_for_xy(XY_REF)
    wb = post_wb_for_neutral(nref).astype(np.float32)
    enc = idc_target(conv, v[:, None] * nref[None, :], cam, mode, XY_REF, wb=wb, normalise=False)
    return v, R.srgb_decode(enc[:, 1])


def tone_points(conv, cam_ref, mode):
    """ProfileToneCurve: IDC's neutral response at the Sony curve's knots (on the DNG scale)."""
    v, y = neutral_tone(conv, cam_ref, mode)
    mng_ref = float((SONY_MATRIX @ neutral_for_xy(XY_REF))[1])
    knots = np.asarray(conv.DSP_CURVE_X, np.float64) / (mng_ref * K_REF)
    xs = np.unique(np.concatenate([[0.0], knots[(knots > 0) & (knots < 1.0)], [1.0]]))
    return np.stack([xs, np.interp(xs, v, y)], 1)


class Tone:
    """The profile's tone curve (the SDK's spline) and its inverse."""

    def __init__(self, pts):
        self.grid = np.linspace(0, 1, 65537)
        self.vals = np.maximum.accumulate(R.Spline(pts)(self.grid))

    def __call__(self, x):
        return np.interp(x, self.grid, self.vals)

    def inv(self, y):
        return np.interp(y, self.vals, self.grid)


def rgb_tone_inverse(z, tone):
    """Inverse of the SDK's hue-preserving RGB tone step for z in [0, 1]^3."""
    mx, mn = z.max(-1), z.min(-1)
    imx, imn = tone.inv(mx), tone.inv(mn)
    span = mx - mn
    frac = np.where(span[..., None] > 0, (z - mn[..., None]) / np.where(span > 0, span, 1.0)[..., None], 0.0)
    out = imn[..., None] + (imx - imn)[..., None] * frac
    return np.where(span[..., None] > 0, out, tone.inv(z))


def _design(u, dims, encoding):
    """Sparse trilinear-interpolation matrix of the SDK's table lookup for inputs u (N, 3)."""
    import scipy.sparse as sps
    h, s, v = R.rgb_to_hsv(u)
    v_enc = R.srgb_encode(np.clip(v, 0, 1)) if (encoding == 1 and dims[0] >= 2) else v
    corners = R.table_weights(h, s, v_enc, dims)
    n = len(u)
    rows = np.concatenate([np.arange(n)] * len(corners))
    cols = np.concatenate([ix for ix, _ in corners])
    vals = np.concatenate([w for _, w in corners])
    return sps.csr_matrix((vals, (rows, cols)), shape=(n, int(np.prod(dims)))), (h, s, v, v_enc)


def _second_differences(dims):
    """Second-difference operators along hue (circular), saturation and value."""
    import scipy.sparse as sps
    nv, nh, ns = dims
    idx = np.arange(nv * nh * ns).reshape(nv, nh, ns)

    def op(mid, lo, hi):
        m = mid.size
        return sps.csr_matrix((np.tile([1.0, -2.0, 1.0], m), (np.repeat(np.arange(m), 3),
                               np.stack([lo.ravel(), mid.ravel(), hi.ravel()], 1).ravel())), shape=(m, idx.size))
    ops = {"h": op(idx, np.roll(idx, 1, axis=1), np.roll(idx, -1, axis=1))}
    if ns > 2:
        ops["s"] = op(idx[:, :, 1:-1], idx[:, :, :-2], idx[:, :, 2:])
    if nv > 2:
        ops["v"] = op(idx[1:-1], idx[:-2], idx[2:])
    return ops


def _solve(B, targets, weights, regs, fix_mask=None, identity=(0.0, 1.0, 1.0)):
    """min sum w (B p - t)^2 + sum lam |D (p - identity)|^2 for each of the three table
    quantities (lam relative to the mean diagonal of B'WB); fix_mask pins the value scale to 1."""
    import scipy.sparse as sps
    import scipy.sparse.linalg as spla
    n = B.shape[1]
    out = np.zeros((n, 3))
    for q in range(3):
        BtW = B.T.multiply(weights[q]).tocsr()
        N = (BtW @ B).tocsr()
        scale = max(N.diagonal().mean(), 1e-12)
        L = sps.csr_matrix((n, n))
        for D, lam in regs:
            L = L + lam * scale * (D.T @ D)
        A = (N + L).tocsc()
        idv = np.full(n, identity[q])
        b = BtW @ (targets[q] - B @ idv)
        if q == 2 and fix_mask is not None:
            free = ~fix_mask
            d = np.zeros(n)
            d[free] = spla.spsolve(A[free][:, free].tocsc(), b[free])
        else:
            d = spla.spsolve(A, b)
        out[:, q] = idv + d
    return out


def _hsv_targets(u_hsv, y, use_enc):
    """Per-sample table targets (hue shift in degrees, saturation scale, value scale) that move
    the table input u to y, and their weights (each as a distance in encoded RGB)."""
    h_u, s_u, v_u, ve_u = u_hsv
    h_y, s_y, v_y = R.rgb_to_hsv(y)
    dh = ((h_y - h_u) * 60.0 + 180.0) % 360.0 - 180.0
    sat = np.where(s_u > 1e-4, s_y / np.maximum(s_u, 1e-4), 1.0)
    ve_y = R.srgb_encode(np.clip(v_y, 0, 1)) if use_enc else v_y
    val = np.where(ve_u > 1e-5, ve_y / np.maximum(ve_u, 1e-5), 1.0)
    e_y = R.srgb_encode(np.clip(v_y, 0, 1))
    w_h = (s_y * e_y * np.pi / 180.0) ** 2
    w_s = (s_u * e_y) ** 2
    w_v = np.clip(ve_u, 0, 1) ** 2 if use_enc else (v_u * e_y / (2.2 * np.maximum(v_y, 1e-6))) ** 2
    return (dh, sat, val), (w_h, w_s, w_v)


def _table_inverse(y, table, encoding, u0, iters=6):
    """u with apply_hue_sat_map(u) = y (damped Newton, finite-difference Jacobian)."""
    u = np.clip(u0.copy(), 1e-6, 1.0)
    for _ in range(iters):
        f0 = R.apply_hue_sat_map(u, table, encoding)
        J = np.empty(u.shape + (3,))
        for j in range(3):
            d = np.zeros(3)
            d[j] = 1e-4
            up = np.clip(u + d, 0, 1)
            J[..., j] = (R.apply_hue_sat_map(up, table, encoding) - f0) / np.maximum(up[..., j] - u[..., j], 1e-9)[..., None]
        try:
            step = np.linalg.solve(J + 1e-9 * np.eye(3), -(f0 - y)[..., None])[..., 0]
        except np.linalg.LinAlgError:
            step = y - f0
        u = np.clip(u + np.clip(step, -0.1, 0.1), 1e-6, 1.0)
    return u


LUT_DIMS = (16, 90, 16)          # values (sRGB-encoded), hues, saturations
HSM_DIMS = (1, 90, 16)           # 2.5-D, linear: the SDK's 2.5-D lookup never encodes values


def make_profile(mode, tone, look=None, hsm1=None, hsm2=None):
    return R.Profile(name=PROFILE_NAME[mode], unique_camera_model=UNIQUE_CAMERA_MODEL, copyright=PROFILE_COPYRIGHT,
                     embed_policy=3, illuminant1=17, illuminant2=21, cm1=XYZ_TO_RGBE, cm2=XYZ_TO_RGBE,
                     fm1=forward_matrix_for(XY_A), fm2=forward_matrix_for(XY_D65),
                     hsm1=hsm1, hsm2=hsm2, hsm_encoding=0, look=look, look_encoding=1,
                     tone_curve=tone, baseline_exposure_offset=0.0, default_black_render=1)


def fit_profile(conv, files, mode, iters=2, log=print):
    """Fit the LookTable and the two HueSatMaps of one mode to IDC's model on the training data.
    Alternates: LookTable (least squares in hue / saturation / value terms, with second-difference
    smoothness), then both HueSatMaps given the LookTable (through its inverse), then the
    LookTable again; the best iteration on the training data is kept."""
    import scipy.sparse as sps
    other = chroma_model(files, mode)
    use = [f for f in files if f["cam"]["mode"] == mode or (mode == "standard" and other is not None)]
    ref = [f for f in files if f["cam"]["mode"] == mode]
    if not ref:
        raise ValueError("no %s-mode shot among the inputs: cannot fit the %s profile" % (mode, mode))
    pts = tone_points(conv, ref[0]["cam"], mode)
    tone = Tone(pts)
    base = make_profile(mode, pts)
    p2s = R.PCS_TO_PROPHOTO @ R.SRGB_TO_PCS
    data = []
    for f in use:
        chroma = None if f["cam"]["mode"] == mode else other(f["xy"])
        t = idc_target(conv, f["camv"], f["cam"], mode, f["xy"], chroma=chroma)
        ren = R.Renderer(base, white_xy=f["xy"])
        y = rgb_tone_inverse(np.clip(np.clip(R.srgb_decode(t), 0, 1) @ p2s.T, 0, 1), tone)
        w = f["w"] / f["w"].sum() * 1e5 * (1.0 if chroma is None else 0.5)
        data.append(dict(f=f, t=t, x=ren.to_prophoto(f["camv"]), y=y, w=w, g=ren.spec.weight(f["xy"])))
    X = np.concatenate([d["x"] for d in data])
    Y = np.concatenate([d["y"] for d in data])
    W = np.concatenate([d["w"] for d in data])
    G = np.concatenate([np.full(len(d["x"]), d["g"]) for d in data])
    nl, nhs = int(np.prod(LUT_DIMS)), int(np.prod(HSM_DIMS))
    hsm1 = np.tile(np.array([0.0, 1.0, 1.0]), HSM_DIMS + (1,))
    hsm2 = hsm1.copy()
    lut_ops, hsm_ops = _second_differences(LUT_DIMS), _second_differences(HSM_DIMS)
    sat0_lut = (np.arange(nl) % LUT_DIMS[2]) == 0
    sat0_hsm = np.tile((np.arange(nhs) % HSM_DIMS[2]) == 0, 2)
    log("  %s: %d samples from %d shots" % (mode, len(X), len(data)))

    def score(look):
        prof = base.copy(look=look, hsm1=hsm1, hsm2=hsm2)
        pix, chart, line = [], [], []
        for d in data:
            e = (R.Renderer(prof, white_xy=d["f"]["xy"])(d["f"]["camv"]).astype(np.float64) - d["t"]) * 255
            kind = d["f"]["kind"]
            r = [float(np.sqrt((e[kind == k] ** 2).mean())) if (kind == k).any() else None for k in range(4)]
            pix.append(r[0])
            if r[3] is not None:
                chart.append(r[3])
            line.append("%s %.2f%s" % (d["f"]["stem"][-3:], r[0], "" if r[3] is None else "/%.2f" % r[3]))
        total = float(np.mean(pix) + (np.mean(chart) if chart else 0.0))
        return total, " ".join(line)

    best = None
    for it in range(iters):
        t0 = time.time()
        U = np.empty_like(X)
        for gv in np.unique(G):
            m = G == gv
            U[m] = R.apply_hue_sat_map(X[m], gv * hsm1 + (1 - gv) * hsm2, 0)
        A, uh = _design(U, LUT_DIMS, 1)
        tg, wq = _hsv_targets(uh, Y, True)
        regs = [(lut_ops[a], 0.05) for a in "hsv"] + [(sps.identity(nl, format="csr"), 1e-4)]
        look = _solve(A, tg, [W * q for q in wq], regs, fix_mask=sat0_lut).reshape(LUT_DIMS + (3,))
        look[..., 1:] = np.clip(look[..., 1:], 0.25, 4.0)
        total, line = score(look)
        log("  iteration %d (%.0fs): RMS /255 pixels[/chart] %s | score %.3f" % (it, time.time() - t0, line, total))
        if best is None or total < best[0]:
            best = (total, look.copy(), hsm1.copy(), hsm2.copy())
        if it == iters - 1:
            break
        ustar = _table_inverse(Y, look, 1, U)
        Bx, xh = _design(X, HSM_DIMS, 0)
        tg2, wq2 = _hsv_targets(xh, ustar, False)
        B = sps.hstack([Bx.multiply(G[:, None]), Bx.multiply(1 - G[:, None])]).tocsr()
        z = lambda D: sps.csr_matrix((D.shape[0], nhs))
        blk = lambda D: sps.vstack([sps.hstack([D, z(D)]), sps.hstack([z(D), D])]).tocsr()
        regs2 = [(blk(hsm_ops["h"]), 0.05), (blk(hsm_ops["s"]), 0.05), (sps.identity(2 * nhs, format="csr"), 0.02),
                 (sps.hstack([sps.identity(nhs), -sps.identity(nhs)]).tocsr(), 0.002)]
        p = _solve(B, tg2, [W * q for q in wq2], regs2, fix_mask=sat0_hsm)
        p[:, 1:] = np.clip(p[:, 1:], 0.25, 4.0)
        hsm1, hsm2 = p[:nhs].reshape(HSM_DIMS + (3,)), p[nhs:].reshape(HSM_DIMS + (3,))
    _, look, hsm1, hsm2 = best
    return make_profile(mode, pts, look.astype(np.float32), hsm1.astype(np.float32), hsm2.astype(np.float32))


def srf_inputs(args):
    paths = []
    for a in args:
        if os.path.isdir(a):
            paths += sorted(glob.glob(os.path.join(a, "*.SRF")) + glob.glob(os.path.join(a, "*.srf")))
        else:
            paths.append(a)
    seen, out = set(), []
    for p in paths:
        stem = os.path.basename(p)[:8].upper()
        if stem not in seen:
            seen.add(stem)
            out.append(p)
    return out


def build(conv, paths, out_dir, modes=MODES, iters=2, log=print):
    files = []
    for p in paths:
        t0 = time.time()
        f = collect_samples(conv, p, log)
        tt = R.xy_to_temp_tint(f["xy"])
        log("%s  %-8s as shot %5.0f K %+5.1f  %6d samples  (%.1fs)" % (f["stem"], f["cam"]["mode"], tt[0], tt[1], len(f["camv"]), time.time() - t0))
        files.append(f)
    written = []
    for mode in modes:
        prof = fit_profile(conv, files, mode, iters=iters, log=log)
        path = os.path.join(out_dir, dcp_filename(mode))
        size = write_dcp(path, prof)
        back = read_dcp(path)
        assert back.name == prof.name and np.allclose(back.look, prof.look) and np.allclose(back.hsm2, prof.hsm2)
        log("wrote %s (%d bytes)" % (path, size))
        written.append(path)
    return written


# --------------------------------------------------------------------------------------------
# command line

def describe(prof):
    lines = ["%s  (camera '%s')" % (prof.name, prof.unique_camera_model),
             "  planes %d, illuminants %d / %d, embed policy %d, default black render %d, baseline exposure offset %+.3f"
             % (prof.cm1.shape[0], prof.illuminant1, prof.illuminant2, prof.embed_policy, prof.default_black_render,
                prof.baseline_exposure_offset)]
    for nm, t, enc in (("HueSatMap1", prof.hsm1, prof.hsm_encoding), ("HueSatMap2", prof.hsm2, prof.hsm_encoding),
                       ("LookTable", prof.look, prof.look_encoding)):
        if t is not None:
            lines.append("  %-10s %d hues x %d sats x %d vals, %s values: hue %+.1f..%+.1f deg, sat x%.2f..%.2f, val x%.2f..%.2f"
                         % (nm, t.shape[1], t.shape[2], t.shape[0], "sRGB" if enc else "linear", t[..., 0].min(), t[..., 0].max(),
                            t[..., 1].min(), t[..., 1].max(), t[..., 2].min(), t[..., 2].max()))
    if prof.tone_curve is not None:
        lines.append("  ToneCurve  %d points, white -> %.4f" % (len(prof.tone_curve), prof.tone_curve[-1, 1]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="rebuild the profiles from F828 .SRF files")
    b.add_argument("inputs", nargs="+", help=".SRF files or folders")
    b.add_argument("--converter", help="f828_converter*.py (default: the newest next to this file or in ../converter)")
    b.add_argument("--out", default=HERE, help="folder for the .dcp files (default: next to this file)")
    b.add_argument("--mode", choices=MODES + ("both",), default="both")
    b.add_argument("--iters", type=int, default=2, help="LookTable / HueSatMap alternations (default 2)")
    i = sub.add_parser("info", help="print a .dcp")
    i.add_argument("dcp", nargs="+")
    r = sub.add_parser("retag", help="change the camera name (UniqueCameraModel) or profile name of .dcp files")
    r.add_argument("dcp", nargs="+")
    r.add_argument("--camera-model", help='e.g. the UniqueCameraModel Adobe DNG Converter writes for your files')
    r.add_argument("--name", help="new ProfileName (one file only)")
    t = sub.add_parser("tags", help="show build_dcp_tags() for raw files")
    t.add_argument("srf", nargs="+")
    t.add_argument("--mode", choices=MODES)
    t.add_argument("--converter")
    a = ap.parse_args(argv)

    if a.cmd == "info":
        for path in a.dcp:
            print(describe(read_dcp(path)))
        return 0
    if a.cmd == "retag":
        if a.name and len(a.dcp) > 1:
            ap.error("--name needs a single .dcp")
        for path in a.dcp:
            print(describe(read_dcp(retag(path, a.camera_model, a.name))))
        return 0
    conv_path = a.converter or find_converter()
    if not conv_path:
        ap.error("cannot find f828_converter*.py; pass --converter")
    if a.cmd == "tags":
        conv = load_converter(conv_path)
        for p in srf_inputs(a.srf):
            cam = conv.read_srf_camera(p)
            tags = build_dcp_tags(cam, a.mode)
            tt = asshot_temp_tint(cam)
            print("%s (%s): as shot %.0f K %+.1f, AsShotNeutral %s, BaselineExposure %+.4f, %d tags"
                  % (os.path.basename(p), a.mode or cam["mode"], tt[0], tt[1],
                     " ".join("%.6f" % (n / d) for n, d in tags[TAG["AsShotNeutral"]][1]),
                     tags[TAG["BaselineExposure"]][1][0][0] / 100.0, len(tags)))
        return 0
    conv = load_converter(conv_path)
    paths = srf_inputs(a.inputs)
    if not paths:
        ap.error("no .SRF files given")
    modes = MODES if a.mode == "both" else (a.mode,)
    t0 = time.time()
    for path in build(conv, paths, a.out, modes, a.iters):
        print(describe(read_dcp(path)))
    print("done in %.0fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
