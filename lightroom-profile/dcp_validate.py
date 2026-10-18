#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Part of the Sony F-828 RGBE RAW Color Converter, https://github.com/wuvshroom/Sony-F-828-RGBE-RAW-Color-Converter (see LICENSE there).
"""Check the IDC profiles against Image Data Converter SR's own TIFFs.

    python3 dcp_validate.py [--converter f828_converter19.py] [--json out.json] FOLDER-or-TIFF ...

For every *-IDC-SRGB.TIF with its .SRF next to it, the raw file is demosaiced (the converter's
bilinear demosaic, camera black level), rendered with dng_ref.py (the DNG SDK 1.4 pipeline) and
compared with IDC's TIFF on smooth 16 x 16 blocks (as the converter's test_export.py does),
on the chart patches of the chart shots, and on the chart's grey ramp. Renderings:

  adobe     Adobe's colour matrix only (CalibrationIlluminant D65, no ForwardMatrix, no tables) and
            the SDK's default ACR3 tone curve with its default black (shadows 5): what a plain
            Adobe-style matrix profile gives, at the correct (register) white balance.
  idc       the IDC profile for the shot's mode, register white balance, no exposure change
            (an .SRF opened in Lightroom with the f828_xmp.py sidecar, no exposure line).
  idc+exp   the same plus the shot's exposure_offset() (the converter's DNG with build_dcp_tags(),
            or the sidecar's Exposure).
  idc@tags  the IDC profile, but with the white balance the .SRF's stored WB tags give
            (404 / 256 / 751, the same in every file; LibRaw reads them, E = G): what Lightroom
            shows if Adobe reads the same tags.
  model     the converter's model of IDC's DSP (render_sony_dsp, what the profiles are fitted to)
            on the same pixels: the part of the error that no profile fitted to it can remove.

Errors are in 8-bit units of the sRGB TIFF (0..255).

    python3 dcp_validate.py --compare LR-EXPORT.tif IDC.TIF ...
checks your own Lightroom / Camera Raw export of a shot (sRGB TIFF, full size, no crop) against
IDC's TIFF the same way (the offset between the two framings is found by correlation): the test
of what Lightroom really does with the profile, which this script can only model.
"""
import argparse
import glob
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import dng_ref as R           # noqa: E402
import f828_dcp as D          # noqa: E402

SRF_TAG_WB = np.array([404.0, 256.0, 751.0, 256.0])     # SRF2 0xd0..0xd2 (R G B), E = G as LibRaw does
BLOCK = 16
IDC_OFFSET = (4, 9)           # IDC pixel (y, x) = converter frame (y + 4, x + 9), give or take one


def blocks(a, n=BLOCK):
    h, w = a.shape[0] // n, a.shape[1] // n
    a = a[:h * n, :w * n].reshape(h, n, w, n, -1)
    return a.mean(axis=(1, 3)), a.std(axis=(1, 3))


def align(cam_g, ref_g):
    """(dy, dx) in -1..1 around IDC_OFFSET by high-pass correlation of the camera's G channel
    with IDC's G channel (on every other pixel, as test_export.py does)."""
    hp = lambda z: z[1:-1, 1:-1] - 0.25 * (z[:-2, 1:-1] + z[2:, 1:-1] + z[1:-1, :-2] + z[1:-1, 2:])
    oy, ox = IDC_OFFSET
    best = None
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            a = cam_g[oy + dy:oy + dy + ref_g.shape[0], ox + dx:ox + dx + ref_g.shape[1]]
            h, w = min(a.shape[0], ref_g.shape[0]), min(a.shape[1], ref_g.shape[1])
            g1 = np.sqrt(np.clip(a[:h:2, :w:2], 0, None))
            cc = np.corrcoef(hp(g1).ravel(), hp(ref_g[:h:2, :w:2]).ravel())[0, 1]
            if best is None or cc > best[0]:
                best = (cc, dy, dx, h, w)
    return best


def patch_boxes(stem, r=20):
    """(y0, y1, x0, x1) on IDC's TIFF of the chart patches and the grey-ramp steps."""
    if stem not in D.CHART_FILES:
        return [], []
    pat = [(int(4 * y) - r, int(4 * y) + r, int(4 * x) - r, int(4 * x) + r) for y in D.CHART_CY for x in D.CHART_CX]
    ramp = [(int(4 * D.CHART_RAMP_Y) - 16, int(4 * D.CHART_RAMP_Y) + 16,
             int(4 * (D.CHART_RAMP_X0 + i * D.CHART_RAMP_DX)) - 16, int(4 * (D.CHART_RAMP_X0 + i * D.CHART_RAMP_DX)) + 16)
            for i in range(21)]
    return pat, ramp


def stats(d, sat_mask=None):
    d = np.asarray(d, dtype=np.float64)
    out = dict(n=int(d.shape[0]), rms=float(np.sqrt((d ** 2).mean())), p99=float(np.percentile(np.abs(d), 99)),
               max=float(np.abs(d).max()), mean=float(d.mean()))
    if sat_mask is not None:
        out["sat_n"] = int(sat_mask.sum())
        out["sat_rms"] = float(np.sqrt((d[sat_mask] ** 2).mean())) if sat_mask.any() else float("nan")
    return out


def validate_file(conv, tif, srf, profiles, log=print):
    import tifffile
    t0 = time.time()
    stem = os.path.basename(srf)[:8].upper()
    s = conv.read_sensor(srf)
    cam = s["camera"]
    mode = cam["mode"]
    camv = conv.normalise_rgbe(conv.sensor_rgbe_dn(s, "full", "bilinear"), cam["black"], s["white"])
    del s
    ref = tifffile.imread(tif).astype(np.float32)
    ref *= np.float32(255.0 / (65535.0 if ref.max() > 255.5 else 255.0))
    cc, dy, dx, h, w = align(camv[..., 1], ref[..., 1])
    oy, ox = IDC_OFFSET[0] + dy, IDC_OFFSET[1] + dx
    frame = camv[oy:oy + h, ox:ox + w]
    ref = ref[:h, :w]
    rm, rs = blocks(ref)
    ok = (rs.max(-1) < 2.0) & (rm.min(-1) > 3) & (rm.max(-1) < 252)
    sat = (rm[ok].max(-1) - rm[ok].min(-1)) > 60
    bi, bj = np.nonzero(ok)
    # camera values of the smooth blocks only: (blocks, 256, 4)
    yy = (bi[:, None] * BLOCK + np.arange(BLOCK)[None, :])[:, :, None]
    xx = (bj[:, None] * BLOCK + np.arange(BLOCK)[None, :])[:, None, :]
    bcam = frame[yy, xx].reshape(len(bi), BLOCK * BLOCK, 4)
    pat, ramp = patch_boxes(stem)
    pcam = [frame[y0:y1, x0:x1].reshape(-1, 4) for y0, y1, x0, x1 in pat + ramp]
    pref = np.array([ref[y0:y1, x0:x1].reshape(-1, 3).mean(0) for y0, y1, x0, x1 in pat + ramp]) if pat else None
    del camv, frame

    xy = D.asshot_xy(cam)
    expo = round(D.exposure_offset(cam, xy), 2)       # as a DNG's BaselineExposure keeps it (0.01 EV)
    adobe = R.Profile(name="Adobe matrix", unique_camera_model=D.UNIQUE_CAMERA_MODEL, illuminant1=21, cm1=D.XYZ_TO_RGBE)
    prof = profiles[mode]
    tag_neutral = (1.0 / SRF_TAG_WB) / (1.0 / SRF_TAG_WB).max()
    renders = {"adobe": [R.Renderer(adobe, white_xy=xy)],
               "idc": [R.Renderer(prof, white_xy=xy), R.Renderer(prof, white_xy=xy, baseline_exposure=expo)],
               "idc@tags": [R.Renderer(prof, neutral=tag_neutral)]}
    names = {"adobe": ["adobe"], "idc": ["idc", "idc+exp"], "idc@tags": ["idc@tags"], "model": ["model"]}
    k_dsp = (D.WHITE_DN - np.asarray(cam["black"], np.float64)) * D.DSP_INPUT_SCALE * float(cam["gain"]) * \
        np.asarray(cam["pregain"], np.float64)

    class _Model:
        """The converter's model of IDC's DSP (what the profiles were fitted to), same pixels."""
        def to_prophoto(self, c):
            return c

        def hsm_stage(self, c):
            return c

        def from_hsm(self, c):
            lin = conv.render_sony_dsp((c * k_dsp).astype(np.float32), cam, cam["wb"], mode)
            return R.srgb_encode(lin.astype(np.float64))
    renders["model"] = [_Model()]

    def render(group, cams):
        """{name: (blocks, 3) mean output in 0..255} for a list of (k, 4) arrays or a (b, k, 4) array."""
        rens = renders[group]
        out = {nm: [] for nm in names[group]}
        items = cams if isinstance(cams, list) else [cams]
        for c in items:
            flat = c.reshape(-1, 4).astype(np.float64)
            res = {nm: np.empty((flat.shape[0], 3)) for nm in names[group]}
            for i in range(0, flat.shape[0], 1 << 18):
                hsm = rens[0].hsm_stage(rens[0].to_prophoto(flat[i:i + (1 << 18)]))
                for nm, ren in zip(names[group], rens):
                    res[nm][i:i + (1 << 18)] = ren.from_hsm(hsm)
            for nm in names[group]:
                v = res[nm].reshape(c.shape[:-1] + (3,)) * 255.0
                out[nm].append(v.mean(axis=-2) if v.ndim == 3 else v.mean(axis=0, keepdims=True))
        return {nm: np.concatenate(v) for nm, v in out.items()}

    result = dict(file=os.path.basename(tif), stem=stem, mode=mode, shift=[int(dy), int(dx)], corr=float(cc),
                  temp_tint=list(R.xy_to_temp_tint(xy)), exposure_offset=expo, blocks=int(ok.sum()),
                  saturated_blocks=int(sat.sum()), renders={})
    for group in renders:
        bm = render(group, bcam)
        pm = render(group, pcam) if pat else None
        for nm in names[group]:
            r = dict(blocks=stats(bm[nm] - rm[ok], sat))
            if pat:
                dp = pm[nm][:len(pat)] - pref[:len(pat)]
                sat_p = (pref[:len(pat)].max(-1) - pref[:len(pat)].min(-1)) > 60
                r["chart"] = stats(dp, sat_p)
                r["chart"]["patches"] = np.round(pm[nm][:len(pat)], 2).tolist()
                if nm == "adobe":
                    result["chart_idc_tiff"] = np.round(pref[:len(pat)], 2).tolist()
                r["ramp"] = stats(pm[nm][len(pat):] - pref[len(pat):])
            result["renders"][nm] = r
    b = result["renders"]
    log("%-38s %-8s %5.0fK %+5.1f shift %+d,%+d  blocks %5d  rms adobe %5.2f | idc %4.2f | idc+exp %4.2f | idc@tags %5.2f | model %4.2f%s  (%.0fs)"
        % (result["file"], mode, result["temp_tint"][0], result["temp_tint"][1], dy, dx, ok.sum(),
           b["adobe"]["blocks"]["rms"], b["idc"]["blocks"]["rms"], b["idc+exp"]["blocks"]["rms"], b["idc@tags"]["blocks"]["rms"],
           b["model"]["blocks"]["rms"],
           ("  chart adobe %4.2f idc+exp %4.2f model %4.2f" % (b["adobe"]["chart"]["rms"], b["idc+exp"]["chart"]["rms"],
                                                             b["model"]["chart"]["rms"])) if pat else "",
           time.time() - t0))
    if pat:
        e = np.abs(np.array(b["idc+exp"]["chart"]["patches"]) - pref[:len(pat)]).max(-1)
        for i in np.argsort(-e)[:3]:
            log("    patch r%d c%d: IDC %s  model %s  profile %s" % (i // len(D.CHART_CX), i % len(D.CHART_CX),
                np.round(pref[i]).astype(int).tolist(), np.round(b["model"]["chart"]["patches"][i]).astype(int).tolist(),
                np.round(b["idc+exp"]["chart"]["patches"][i]).astype(int).tolist()))
    return result


def find_pairs(inputs):
    tifs = []
    for a in inputs:
        tifs += sorted(glob.glob(os.path.join(a, "*-IDC-SRGB.TIF"))) if os.path.isdir(a) else [a]
    pairs = []
    for tif in tifs:
        base = tif[:-len("-IDC-SRGB.TIF")]
        for cand in (base + ".SRF", base + "-RAW.SRF"):
            if os.path.exists(cand):
                pairs.append((tif, cand))
                break
    return pairs


def table(results, key="blocks"):
    cols = ["adobe", "idc", "idc+exp", "idc@tags", "model"]
    lines = ["| shot | mode | " + " | ".join("%s rms / p99 / max / sat" % c for c in cols) + " |",
             "|---|---|" + "---|" * len(cols)]
    for r in results:
        cells = []
        for c in cols:
            s = r["renders"][c][key]
            cells.append("%.2f / %.1f / %.1f / %.1f" % (s["rms"], s["p99"], s["max"], s.get("sat_rms", float("nan"))))
        lines.append("| %s | %s | %s |" % (r["stem"], r["mode"], " | ".join(cells)))
    return "\n".join(lines)


def read_rgb8(path):
    """An RGB TIFF / PNG / JPEG as float32 in 8-bit units."""
    if path.lower().endswith((".tif", ".tiff")):
        import tifffile
        a = tifffile.imread(path)
    else:
        from PIL import Image
        a = np.asarray(Image.open(path).convert("RGB"))
    a = np.asarray(a)[..., :3].astype(np.float32)
    return a * np.float32(255.0 / 65535.0) if a.max() > 255.5 else a


def find_offset(a, b, step=4):
    """(dy, dx) with a[y + dy, x + dx] ~ b[y, x]: phase correlation of the green channels on a
    1/step grid, then refined to the pixel by high-pass correlation."""
    def prep(z):
        z = z[::step, ::step, 1].astype(np.float64)
        return z - z.mean()
    za, zb = prep(a), prep(b)
    h, w = max(za.shape[0], zb.shape[0]), max(za.shape[1], zb.shape[1])
    fa, fb = np.fft.rfft2(za, (2 * h, 2 * w)), np.fft.rfft2(zb, (2 * h, 2 * w))
    r = fa * np.conj(fb)
    c = np.fft.irfft2(r / np.maximum(np.abs(r), 1e-9), (2 * h, 2 * w))
    iy, ix = np.unravel_index(np.argmax(c), c.shape)
    dy0 = (iy if iy < h else iy - 2 * h) * step
    dx0 = (ix if ix < w else ix - 2 * w) * step
    hp = lambda z: z[1:-1, 1:-1] - 0.25 * (z[:-2, 1:-1] + z[2:, 1:-1] + z[1:-1, :-2] + z[1:-1, 2:])
    best = None
    for dy in range(dy0 - step, dy0 + step + 1):
        for dx in range(dx0 - step, dx0 + step + 1):
            y0, x0 = max(0, -dy), max(0, -dx)
            y1, x1 = min(b.shape[0], a.shape[0] - dy), min(b.shape[1], a.shape[1] - dx)
            if y1 - y0 < 64 or x1 - x0 < 64:
                continue
            ga = a[y0 + dy:y1 + dy:2, x0 + dx:x1 + dx:2, 1].astype(np.float64)
            gb = b[y0:y1:2, x0:x1:2, 1].astype(np.float64)
            cc = np.corrcoef(hp(ga).ravel(), hp(gb).ravel())[0, 1]
            if best is None or cc > best[0]:
                best = (cc, dy, dx)
    return best


def compare_exports(export, idc):
    """Lightroom / Camera Raw export (sRGB, no crop, no resize) against IDC's TIFF of the same
    shot, on IDC's smooth 16 x 16 blocks."""
    a, b = read_rgb8(export), read_rgb8(idc)
    cc, dy, dx = find_offset(a, b)
    y0, x0 = max(0, -dy), max(0, -dx)
    y1, x1 = min(b.shape[0], a.shape[0] - dy), min(b.shape[1], a.shape[1] - dx)
    y0 += (-y0) % BLOCK
    x0 += (-x0) % BLOCK
    ref, out = b[y0:y1, x0:x1], a[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    rm, rs = blocks(ref)
    am, _ = blocks(out)
    ok = (rs.max(-1) < 2.0) & (rm.min(-1) > 3) & (rm.max(-1) < 252)
    sat = (rm[ok].max(-1) - rm[ok].min(-1)) > 60
    d = am[ok] - rm[ok]
    st = stats(d, sat)
    grey = (rm[ok].max(-1) - rm[ok].min(-1)) < 10
    print("%s vs %s: offset %+d,%+d (corr %.3f), %d smooth blocks: rms %.2f p99 %.1f max %.1f, saturated rms %.2f, "
          "mean %+.2f (R %+.2f G %+.2f B %+.2f), on grey blocks %+.2f"
          % (os.path.basename(export), os.path.basename(idc), dy, dx, cc, st["n"], st["rms"], st["p99"], st["max"],
             st.get("sat_rms", float("nan")), st["mean"], *d.mean(0), d[grey].mean() if grey.any() else float("nan")))
    return st


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="+", help="folders with *-IDC-SRGB.TIF + .SRF, or TIFF files; with --compare: "
                                              "pairs EXPORT.tif IDC.TIF")
    ap.add_argument("--converter", help="f828_converter*.py (default: found as f828_dcp.py does)")
    ap.add_argument("--profiles", default=HERE, help="folder with the two .dcp files")
    ap.add_argument("--json", help="write all numbers to this file")
    ap.add_argument("--compare", action="store_true",
                    help="compare your own Lightroom / Camera Raw exports (sRGB, uncropped, full size) with IDC's TIFFs")
    a = ap.parse_args(argv)
    if a.compare:
        if len(a.inputs) % 2:
            ap.error("--compare takes pairs: EXPORT IDC-TIFF ...")
        for i in range(0, len(a.inputs), 2):
            compare_exports(a.inputs[i], a.inputs[i + 1])
        return 0
    conv = D.load_converter(a.converter or D.find_converter())
    profiles = {m: D.read_dcp(os.path.join(a.profiles, D.dcp_filename(m))) for m in D.MODES}
    results = [validate_file(conv, tif, srf, profiles) for tif, srf in find_pairs(a.inputs)]
    print("\nsmooth 16x16 blocks (8-bit units): rms / p99 / max / rms of saturated blocks")
    print(table(results, "blocks"))
    charts = [r for r in results if "chart" in r["renders"]["idc"]]
    if charts:
        print("\nchart patches (36 per shot): rms / p99 / max / rms of saturated patches")
        print(table(charts, "chart"))
        print("\ngrey ramp (21 steps): rms / p99 / max")
        print(table(charts, "ramp"))
    for c in ("adobe", "idc", "idc+exp", "idc@tags", "model"):
        v = [r["renders"][c]["blocks"]["rms"] for r in results]
        print("%-9s block rms: median %.2f, worst %.2f" % (c, float(np.median(v)), float(np.max(v))))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(results, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
