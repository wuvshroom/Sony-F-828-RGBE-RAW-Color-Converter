"""Checks that the compiled (Numba) fast paths give exactly the numpy paths' results.

    python tests/test_fast_paths.py

Needs numba. Uses a synthetic F828-sized frame and register dump, so no .SRF is needed:
  1. the colour stage and the output step on random inputs and random register settings;
  2. whole exports and previews in several settings, rendered with and without Numba,
     compared byte for byte.
Exits with status 1 on any difference.
"""
import importlib.util
import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("f828_converter", os.path.join(HERE, "..", "f828_converter.py"))
c = importlib.util.module_from_spec(_spec)
sys.modules["f828_converter"] = c
_spec.loader.exec_module(c)


def make_dump():
    d = bytearray(c.SRF_REGISTER_BYTES)
    def w16(a, v): d[a] = (v >> 8) & 0xff; d[a+1] = v & 0xff
    for a in (0x424,0x426,0x428,0x42a): w16(a, 492)
    w16(0x42c, 128)
    for a in (0x434,0x436,0x438,0x43a): w16(a, 256)
    M = [[1700,-500,-120,-56],[-260,1350,-150,84],[40,-420,1500,-96]]
    for i in range(3):
        for j in range(4): w16(0x634+2*(4*i+j), M[i][j] & 0x7fff)
    w16(0x64c, 565); w16(0x650, 539); w16(0x652, 0x3f)
    for a,v in {0x625:3,0x626:6,0x627:1,0x66a:1,0x66b:1,0x6a4:160,0x6a5:255,0x6a6:0,0x6a7:0x12,0x6a9:5,
                0x736:0xf2,0x737:0x75,0x679:131,0x677:0xff,0x675:0x40,0x693:0,0x699:4,
                0x688:0x10,0x689:0xf0,0x68a:0x08,0x68b:0xf8,0x68c:150,0x68d:160,0x68e:140,0x68f:150}.items(): d[a]=v
    w16(0x664,19); w16(0x666,38); w16(0x668,7); w16(0x694,1); w16(0x696,0x3c)
    d[0x738:0x74c] = bytes.fromhex("020201040013310000f010000013310000f01000")
    d[0x754]=0x40; d[0x764:0x76c]=bytes.fromhex("0100711780a0ffff")
    return bytes(d)

def make_cam():
    d = make_dump()
    curve = np.array(c._SONY_CURVE_Y["real"], dtype=np.float32)
    r16 = lambda a: (d[a] << 8) | d[a+1]
    s15 = lambda v: (v & 0x7fff) - 0x8000 if (v & 0x7fff) > 0x3fff else v & 0x7fff
    s8 = lambda v: v - 0x100 if v > 0x7f else v
    cam = {"black": np.array([492]*4, np.float32), "gain": 1.0, "pregain": np.ones(4, np.float32),
        "matrix": np.array([s15(r16(0x634+2*i)) for i in range(12)], np.float32).reshape(3,4)/1024.0,
        "wb": np.array([565,256,539], np.float32)/256.0, "luma": np.array([19,38,7], np.float32)/64.0,
        "cr_cross": (s8(d[0x688]), s8(d[0x68a])), "cb_cross": (s8(d[0x689]), s8(d[0x68b])),
        "cb_gain": (d[0x68c], d[0x68e]), "cr_gain": (d[0x68d], d[0x68f]), "cb_offset": 0, "cr_offset": 0,
        "contrast": 131/128.0, "curve": curve, "mode": "real", "regs": d, "detail_curve": curve}
    tags = {0x22: [int(v) for v in curve[1:]], 0x24: [int(v) for v in curve[1:]], 0x26: list(c.IDC_LUT_Y_IDENTITY)}
    cam["stage"] = c.idc_stage_registers(d, tags); cam["post"] = c.idc_output_registers(d)
    return cam

def make_sensor(h=2464, w=3280, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = 3000 + 2500*np.sin(xx/37.0)*np.cos(yy/53.0) + 1500*((xx//64 + yy//64) % 2)
    cmap = np.zeros((h, w), np.uint8); cmap[0::2,0::2]=0; cmap[0::2,1::2]=1; cmap[1::2,0::2]=3; cmap[1::2,1::2]=2
    gain = np.array([0.6,1.0,0.45,0.95], np.float32)[cmap]
    cfa = np.clip(492 + base*gain + rng.normal(0, 30, (h, w)), 0, 16368).astype(np.float32)
    cam = make_cam()
    return {"path": None, "cfa": cfa, "cmap": cmap, "white": 16368.0, "camera": cam,
            "black": {"camera": cam["black"].copy(), "border": None, "fixed": c.FIXED_BLACK_RGBE.copy(), "libraw": None},
            "camera_wb": None, "tag_wb": None, "asshot": (5200.0, 10.0), "asshot_source": "camera", "quarter_turns": 0}


def fuzz_stage(p, rng):
    p = dict(p)
    p["m"] = [[int(rng.integers(-3000, 3000)) for _ in range(4)] for _ in range(3)]
    p["wb"] = (int(rng.integers(100, 1500)), 256, int(rng.integers(100, 1500)))
    for k, hi in (("clip_en", 2), ("ext_en", 2), ("hsel", 8), ("lsel", 8), ("ymode", 5), ("posterize", 2), ("neg_y", 2),
                  ("ydel", 4), ("cmode", 4), ("sup_hi_sh", 8), ("sup_lo_g", 8), ("ck_hi_m", 4), ("ck_lo_m", 4),
                  ("f2tap", 2), ("f121", 2), ("f323", 2), ("neg_c", 2), ("knee_s", 16)):
        p[k] = int(rng.integers(0, hi))
    p["sup_en"] = int(rng.integers(0, 2))
    p["clip_thr"] = int(rng.integers(200, 1100))
    p["mask0"], p["mask1"], p["mask2"] = (c.IDC_KILL_MASKS[int(rng.integers(0, 8))] for _ in range(3))
    p["pivot"] = int(rng.integers(0, 16)) << 4
    p["ycon"] = int(rng.integers(64, 256)); p["yoff"] = int(rng.integers(-64, 64)); p["knee"] = int(rng.integers(0, 256)) << 2
    p["ymax"] = int(rng.integers(0, 16)) * 16 + 0x30f; p["ymin"] = int(rng.integers(0, 16)) << 4
    p["sup_hi"] = int(rng.integers(0, 512)) << 4; p["sup_lo"] = int(rng.integers(0, 256)) << 4
    p["ck_hi"] = int(rng.integers(0, 128)) << 4; p["ck_lo"] = int(rng.integers(0, 128)) << 4
    for k in ("k688", "k689", "k68a", "k68b"): p[k] = int(rng.integers(-128, 128))
    for k in ("g68c", "g68d", "g68e", "g68f"): p[k] = int(rng.integers(0, 256))
    p["off_cb"], p["off_cr"] = int(rng.integers(-128, 128)), int(rng.integers(-128, 128))
    return p


def fuzz_post(p, rng):
    p = dict(p)
    for k, hi in (("c_neg", 2), ("c_fix", 2), ("c_fix_cb", 256), ("c_fix_cr", 256), ("c_qmask", 16), ("y_post", 2),
                  ("y_neg", 2), ("s_en", 2), ("s_pair_off", 2), ("s_alt", 2), ("s_w0", 32), ("s_w", 32), ("s_fixed", 2),
                  ("s_adapt", 2), ("s_adapt_sh", 4), ("s_pattern", 256)):
        p[k] = int(rng.integers(0, hi))
    if rng.random() < 0.6: p["c_fix"] = 0
    if rng.random() < 0.5: p["s_fixed"] = 0
    p["c_thr_cb"], p["c_thr_cr"] = int(rng.integers(-128, 128)), int(rng.integers(-128, 128))
    p["c_cen_cb"] = tuple(int(v) for v in rng.integers(-128, 128, 4)); p["c_cen_cr"] = tuple(int(v) for v in rng.integers(-128, 128, 4))
    p["c_scale"] = tuple(int(v) for v in rng.integers(0, 256, 4))
    return p


def numpy_mode(on):
    """Run the numpy steps (on=True) or the compiled ones."""
    c.HAVE_NUMBA = not on


def check_stage(rng, trials=200):
    cam = make_cam()
    base = c._idc_stage_params(cam, cam["wb"], "real")
    bad = 0
    for t in range(trials):
        p = base if t == 0 else fuzz_stage(base, rng)
        rows, W = int(rng.integers(1, 6)), int(rng.integers(20, 300))
        kind = t % 3
        if kind == 0:
            xi = rng.integers(-300, 4500, size=(rows, W, 4)).astype(np.int32)
        elif kind == 1:
            xi = rng.integers(-8192, 65535, size=(rows, W, 4)).astype(np.int32)
        else:
            xi = (rng.integers(0, 4000, size=(rows, 1, 4)) + rng.integers(-20, 20, size=(rows, W, 4))).astype(np.int32)
        H = rng.integers(-8192, 65535 if kind == 1 else 5000, size=(rows, W)).astype(np.int32) if t % 2 else None
        K = rng.integers(0, 4, size=(rows, W)).astype(np.int32) if t % 4 < 2 else None
        Y8, C8, P = c.idc_colour_stage(xi[..., 0], xi[..., 1], xi[..., 2], xi[..., 3], p, 1, H, K)
        ref = c._idc_pixels(Y8, C8, P, W)
        y8 = np.empty((rows, W), np.uint8)
        cb, cr = np.empty_like(y8), np.empty_like(y8)
        curve = np.asarray(list(p["curve"]) + [p["curve"][-1]] * 32, dtype=np.int64)
        curve_d = np.asarray(list(p["curve_d"]) + [p["curve_d"][-1]] * 32, dtype=np.int64)
        lut = np.asarray([0] + list(p["lut_y"])[:80], dtype=np.int64)
        dummy = np.zeros((1, 1), np.int32)
        first = c._nb_stage_pixels(xi, dummy if H is None else H, dummy if K is None else K, H is not None,
                                   K is not None, c._idc_stage_vector(p), curve, curve_d, lut,
                                   c.IDC_PREROLL, c.IDC_LUMA_DELAY, y8, cb, cr)
        if not (all(np.array_equal(a, b) for a, b in zip(ref[:3], (y8, cb, cr))) and ref[3] == first):
            bad += 1
    print("colour stage, %d random settings: %s" % (trials, "identical" if not bad else "%d DIFFER" % bad))
    return bad


def check_output16(rng, trials=200):
    base = make_cam()["post"]
    bad = 0
    for t in range(trials):
        p = base if t == 0 else fuzz_post(base, rng)
        rows, W = int(rng.integers(1, 9)), int(rng.integers(2, 200))
        lo, hi = (0, 256) if t % 2 else (100, 104)
        y8 = rng.integers(lo, hi, (rows, W)).astype(np.uint8)
        cb8 = rng.integers(0, 256, (rows, W)).astype(np.uint8)
        cr8 = rng.integers(0, 256, (rows, W)).astype(np.uint8)
        first = int(rng.integers(0, 2))
        a = c.idc_output16(y8, cb8, cr8, p, first)
        b = np.empty_like(a)
        c._nb_output16(y8, cb8, cr8, c._idc_post_vector(p), first, c._IDC_RECIP.astype(np.int64), b)
        bad += not np.array_equal(a, b)
    print("output step, %d random settings: %s" % (trials, "identical" if not bad else "%d DIFFER" % bad))
    return bad


def render_all(s):
    def ed(**kw):
        e = c.Edit(render="sony", wb_preset="asshot")
        for k, v in kw.items():
            setattr(e, k, v)
        return e
    variants = {
        "Sony, 16-bit TIFF": (ed(), dict(fmt="tiff", bits=16)),
        "Sony, exposure +0.7": (ed(exposure=0.7), dict(fmt="tiff", bits=16)),
        "Sony Standard, daylight": (ed(sony_curve="standard", wb_preset="daylight"), dict(fmt="tiff", bits=16)),
        "Sony, highlights, black point": (ed(highlights=True, black_point=50), dict(fmt="tiff", bits=16)),
        "Sony, Adobe RGB JPEG, turned, cropped": (ed(rot_k=1, crop=(0.1, 0.1, 0.8, 0.9)),
                                                  dict(fmt="jpeg", bits=8, space="adobergb")),
        "Sony, edge-aware demosaic": (ed(demosaic="guided"), dict(fmt="tiff", bits=16)),
        "Sony, half size": (ed(), dict(fmt="tiff", bits=16, resolution="half")),
        "default rendering": (ed(render="default"), dict(fmt="tiff", bits=16)),
    }
    out = {}
    for name, (e, o) in variants.items():
        opts = c.ExportOptions()
        opts.copy_metadata = False
        for k, v in o.items():
            setattr(opts, k, v)
        out[name] = c.render_export(s, e, opts)
    e = c.resolve_edit(ed(), s)
    full = c.sensor_rgbe_dn(s, "full", "idc")
    out["IDC demosaic frame"] = full
    for level in ("quarter", "half"):
        out["preview, " + level] = c.develop_preview(c.frame_from_full(full, level), s, e)
    return out


def check_pipeline():
    s = make_sensor(seed=3)
    s["cfa"][:300, :400] = np.minimum(s["cfa"][:300, :400] * 4, 16368)      # some clipped highlights
    numpy_mode(True)
    ref = render_all(s)
    numpy_mode(False)
    new = render_all(s)
    bad = 0
    for k in ref:
        same = ref[k].dtype == new[k].dtype and np.array_equal(ref[k].view(np.uint8), new[k].view(np.uint8))
        bad += not same
        print("  %-40s %s" % (k, "identical" if same else "DIFFERENT"))
    return bad


def main():
    if not c.HAVE_NUMBA:
        sys.exit("numba is not installed (or F828_NO_NUMBA is set): nothing to compare")
    c.warm_up_kernels()
    rng = np.random.default_rng(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    bad = check_stage(rng) + check_output16(rng)
    print("exports and previews, numpy vs compiled:")
    bad += check_pipeline()
    print("ALL IDENTICAL" if not bad else "%d DIFFERENCES" % bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
