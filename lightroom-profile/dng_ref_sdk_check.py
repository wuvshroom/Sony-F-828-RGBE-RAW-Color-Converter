#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Part of the Sony F-828 RGBE RAW Color Converter, https://github.com/wuvshroom/Sony-F-828-RGBE-RAW-Color-Converter (see LICENSE there).
"""Check dng_ref.py against Adobe's own DNG SDK with the real profiles and real raw data.

    python3 dng_ref_sdk_check.py --dng-validate PATH/dng_validate [--converter ...] FILE.SRF ...

For each raw file: a 4-plane LinearRaw DNG of its half-size image (2 x 2 superpixels, no
interpolation) is written with the IDC profile embedded through f828_dcp.build_dcp_tags()
(the shot's AsShotNeutral and BaselineExposure included), rendered to a 16-bit sRGB TIFF by the
SDK's dng_validate (-16 -tif), and rendered again by dng_ref.py from the profile read back out
of that DNG. Prints the difference in 8-bit units. dng_validate comes with the Adobe DNG SDK
(build it with the XMP part stubbed out if you only need rendering).
"""
import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import dng_ref as R           # noqa: E402
import f828_dcp as D          # noqa: E402


def dng_tags(w, h, cam, mode):
    tags = {254: (4, (0,)), 262: (3, (34892,)), 274: (3, (1,)), 271: (2, b"SONY\0"), 272: (2, b"DSC-F828\0"),
            50706: (1, bytes([1, 4, 0, 0])), 50707: (1, bytes([1, 3, 0, 0])),
            50708: (2, D.UNIQUE_CAMERA_MODEL.encode() + b"\0"),
            50714: (5, ((0, 1),) * 4), 50717: (4, (65535,) * 4), 50718: (5, ((1, 1), (1, 1))),
            50719: (4, (0, 0)), 50720: (4, (w, h))}
    tags.update(D.build_dcp_tags(cam, mode))
    return tags


def check(conv, srf, dng_validate, modes, workdir):
    s = conv.read_sensor(srf)
    cam = s["camera"]
    camv = conv.normalise_rgbe(conv.cfa_to_superpixel(s["cfa"], s["cmap"]), cam["black"], s["white"])
    data = np.clip(np.rint(np.clip(camv, 0, 1) * 65535), 0, 65535).astype(np.uint16)
    del s, camv
    h, w = data.shape[:2]
    out = []
    for mode in modes:
        stem = os.path.join(workdir, "%s_%s" % (os.path.basename(srf)[:8], mode))
        conv.write_raw_tiff(stem + ".dng", data, dng_tags(w, h, cam, mode), {})
        r = subprocess.run([dng_validate, "-16", "-tif", stem, stem + ".dng"], capture_output=True, text=True)
        if r.returncode or not os.path.exists(stem + ".tif"):
            raise RuntimeError("dng_validate failed:\n" + r.stdout[-2000:] + r.stderr[-2000:])
        import tifffile
        sdk = tifffile.imread(stem + ".tif").astype(np.float64) / 65535.0
        with open(stem + ".dng", "rb") as f:
            tags = D.read_tags(f.read())
        prof = D.tags_to_profile(tags)
        neutral = np.array([n / d for n, d in tags[D.TAG["AsShotNeutral"]][2]])
        bexp = tags[D.TAG["BaselineExposure"]][2][0]
        ren = R.Renderer(prof, neutral=neutral, baseline_exposure=bexp[0] / bexp[1])
        mine = ren(data.astype(np.float64) / 65535.0).astype(np.float64)
        d = (mine - sdk) * 255.0
        out.append((os.path.basename(srf), mode, prof.name, d))
        print("%-34s %-8s profile '%s': %dx%d px, dng_ref - SDK (8-bit): rms %.4f  p99 %.4f  max %.4f  mean %+.4f"
              % (os.path.basename(srf), mode, prof.name, w, h, np.sqrt((d ** 2).mean()), np.percentile(np.abs(d), 99),
                 np.abs(d).max(), d.mean()), flush=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("srf", nargs="+")
    ap.add_argument("--dng-validate", required=True, help="the DNG SDK's dng_validate program")
    ap.add_argument("--converter", help="f828_converter*.py (default: found as f828_dcp.py does)")
    ap.add_argument("--mode", choices=D.MODES + ("both",), default="both")
    ap.add_argument("--keep", help="folder to keep the DNGs and TIFFs in")
    a = ap.parse_args(argv)
    conv = D.load_converter(a.converter or D.find_converter())
    modes = D.MODES if a.mode == "both" else (a.mode,)
    work = a.keep or tempfile.mkdtemp(prefix="dcpcheck")
    os.makedirs(work, exist_ok=True)
    for srf in a.srf:
        check(conv, srf, a.dng_validate, modes, work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
