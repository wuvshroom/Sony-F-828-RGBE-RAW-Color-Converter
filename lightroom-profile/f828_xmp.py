#!/usr/bin/env python3
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Part of the Sony F-828 RGBE RAW Color Converter, https://github.com/wuvshroom/Sony-F-828-RGBE-RAW-Color-Converter (see LICENSE there).
"""Lightroom / Camera Raw sidecars (.xmp) that give each Sony DSC-F828 .SRF its real as-shot
white balance and the matching IDC profile.

    python3 f828_xmp.py [options] SRF-or-folder ...

Why: every F828 .SRF stores the same white-balance numbers in its readable tags (404 / 256 / 751,
whatever the light), which is what LibRaw-based programs show as "as shot". The camera's real
white balance is in the encrypted register block that IDC develops from (registers 0x64c / 0x650,
after its colour matrix). This script decodes it with the F828 converter's read_srf_camera(),
turns it into the chromaticity the profile's colour matrix needs for that exact neutral, and
writes that as Temperature / Tint in the scale of the profile (the DNG SDK's dng_temperature, with
the profile's ColorMatrix), with WhiteBalance="Custom" and CameraProfile set to the IDC profile of
the shot's mode. Optionally Exposure2012 carries the shot's exposure offset (IDC's digital gain
and the white-balance-dependent brightness of its G channel).

Lightroom Classic reads a sidecar when it imports the raw file; for files already in the catalog
use Metadata > Read Metadata from Files (it replaces the catalog's settings for those files).
Camera Raw (Photoshop / Bridge) reads it when it opens the file.

An existing sidecar is left alone unless --update (set the white balance and profile, add the
exposure and process version only where the sidecar has none, keep everything else) or
--overwrite (replace the file).
"""
import argparse
import glob
import os
import re
import sys
from xml.sax.saxutils import quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import f828_dcp as D          # noqa: E402

CRS = "http://ns.adobe.com/camera-raw-settings/1.0/"
DEFAULT_PROCESS_VERSION = "11.0"      # Lightroom Classic 8.3+ / Camera Raw 11.3+ ("Version 5")


def settings_for(cam, mode=None, exposure=True, acr_baseline=0.0, process_version=DEFAULT_PROCESS_VERSION):
    """The crs: settings for one shot, as {name: text}."""
    mode = mode or cam.get("mode") or "real"
    xy = D.asshot_xy(cam)
    temp, tint = D.asshot_temp_tint(cam)
    out = {"WhiteBalance": "Custom",
           "Temperature": "%d" % int(round(temp)),
           "Tint": "%+d" % int(round(tint)),
           "CameraProfile": D.PROFILE_NAME[mode],
           "HasSettings": "True"}
    if process_version:
        out["ProcessVersion"] = process_version
    if exposure or acr_baseline:
        ev = round((D.exposure_offset(cam, xy) if exposure else 0.0) - acr_baseline, 2)
        out["Exposure2012"] = "%+.2f" % (ev + 0.0)      # (+ 0.0: no "-0.00")
    return out


def new_sidecar(settings):
    attrs = "".join("\n   crs:%s=%s" % (k, quoteattr(v)) for k, v in settings.items())
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="f828_xmp.py">\n'
            ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            '  <rdf:Description rdf:about=""\n'
            '    xmlns:crs="%s"%s/>\n'
            ' </rdf:RDF>\n'
            '</x:xmpmeta>\n' % (CRS, attrs))


def update_sidecar(text, settings, keep=("ProcessVersion", "Exposure2012")):
    """Change only our crs: settings in an existing sidecar (attribute or element form); add the
    missing ones to the rdf:Description that declares the crs namespace, or add a Description.
    Settings named in `keep` are only added, never changed (the photographer's process version
    and exposure stay). A CameraProfileDigest (it names the old profile) is removed."""
    pending = dict(settings)
    for key in keep:
        if re.search(r'crs:%s\s*=\s*"[^"]*"|<crs:%s>' % (key, key), text):
            pending.pop(key, None)
    if "CameraProfile" in pending:
        text = re.sub(r'\s*crs:CameraProfileDigest\s*=\s*"[^"]*"', "", text)
        text = re.sub(r'\s*<crs:CameraProfileDigest>[^<]*</crs:CameraProfileDigest>', "", text)
    for key in list(pending):
        val = pending[key]
        attr = re.compile(r'(crs:%s\s*=\s*)"[^"]*"' % key)
        elem = re.compile(r'(<crs:%s>)[^<]*(</crs:%s>)' % (key, key))
        if attr.search(text):
            text = attr.sub(lambda m: m.group(1) + quoteattr(val), text, count=1)
            pending.pop(key)
        elif elem.search(text):
            text = elem.sub(lambda m: m.group(1) + val + m.group(2), text, count=1)
            pending.pop(key)
    if pending:
        decl = re.search(r'xmlns:crs\s*=\s*"%s"' % re.escape(CRS), text)
        extra = "".join("\n   crs:%s=%s" % (k, quoteattr(v)) for k, v in pending.items())
        if decl:
            text = text[:decl.end()] + extra + text[decl.end():]
        elif "</rdf:RDF>" in text:
            i = text.index("</rdf:RDF>")
            text = text[:i] + ('  <rdf:Description rdf:about=""\n    xmlns:crs="%s"%s/>\n ' % (CRS, extra)) + text[i:]
        else:
            raise ValueError("not an XMP packet")
    return text


def sidecar_path(srf):
    return os.path.splitext(srf)[0] + ".xmp"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="+", help=".SRF files or folders")
    ap.add_argument("--mode", choices=D.MODES, help="profile to select (default: the mode the shot was taken in)")
    ap.add_argument("--no-exposure", action="store_true", help="leave Exposure2012 out")
    ap.add_argument("--acr-baseline-exposure", type=float, default=0.0, metavar="EV",
                    help="Camera Raw's own BaselineExposure for the F828 if it is not 0 (see README); subtracted from Exposure2012")
    ap.add_argument("--process-version", default=DEFAULT_PROCESS_VERSION,
                    help="crs:ProcessVersion for new sidecars (default %(default)s; '' = leave out)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--update", action="store_true", help="change only these settings in existing sidecars")
    g.add_argument("--overwrite", action="store_true", help="replace existing sidecars")
    ap.add_argument("--dry-run", action="store_true", help="print, do not write")
    ap.add_argument("--converter", help="f828_converter*.py (default: found as f828_dcp.py does)")
    a = ap.parse_args(argv)

    conv_path = a.converter or D.find_converter()
    if not conv_path:
        ap.error("cannot find f828_converter*.py; pass --converter")
    conv = D.load_converter(conv_path)
    paths, seen = [], set()
    for p in a.inputs:
        for f in (sorted(glob.glob(os.path.join(p, "*.SRF")) + glob.glob(os.path.join(p, "*.srf"))) if os.path.isdir(p) else [p]):
            key = os.path.normcase(os.path.abspath(f))
            if key not in seen:                   # (Windows globs ignore case: *.SRF and *.srf match twice)
                seen.add(key)
                paths.append(f)
    status = 0
    for srf in paths:
        try:
            cam = conv.read_srf_camera(srf)
        except Exception as e:
            print("%s: skipped (%s)" % (srf, e))
            status = 1
            continue
        s = settings_for(cam, a.mode, not a.no_exposure, a.acr_baseline_exposure, a.process_version)
        dst = sidecar_path(srf)
        if os.path.exists(dst) and not (a.update or a.overwrite):
            print("%s: sidecar exists, left alone (use --update or --overwrite)" % dst)
            continue
        if os.path.exists(dst) and a.update:
            with open(dst, encoding="utf-8") as f:
                text = update_sidecar(f.read(), s)
        else:
            text = new_sidecar(s)
        print("%s: %s K %s, %s%s" % (os.path.basename(dst), s["Temperature"], s["Tint"], s["CameraProfile"],
                                     (", exposure " + s["Exposure2012"]) if "Exposure2012" in s else ""))
        if a.dry_run:
            print(text)
            continue
        tmp = dst + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, dst)
    return status


if __name__ == "__main__":
    sys.exit(main())
