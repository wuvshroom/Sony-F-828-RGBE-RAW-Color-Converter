# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Part of the Sony F-828 RGBE RAW Color Converter, https://github.com/wuvshroom/Sony-F-828-RGBE-RAW-Color-Converter (see LICENSE there).
"""Reference renderer for DNG camera profiles (DCP), following the Adobe DNG SDK 1.4
(dng_render.cpp, dng_reference.cpp, dng_color_spec.cpp, dng_camera_profile.cpp,
dng_temperature.cpp, dng_spline.cpp) in vectorised numpy.

Pipeline (dng_render_task::ProcessArea), per pixel:
  camera values (0..1, each channel clipped at the camera white)
  -> CameraToPCS (ForwardMatrix interpolated by the white's temperature, or the
     pseudo-inverse of the ColorMatrix when there is no ForwardMatrix)
  -> linear ProPhoto RGB, pinned to [0, 1]
  -> ProfileHueSatMap (interpolated between the two illuminants)
  -> exposure ramp (BaselineExposure + BaselineExposureOffset + user exposure, black 'shadows')
  -> ProfileLookTable
  -> tone curve (ProfileToneCurve, else the SDK's default ACR3 curve), applied with the
     hue-preserving RGBTone method, together with the negative-exposure shoulder
  -> final space (sRGB by default), gamma encoded.

Checked against the SDK's own renderer (dng_validate -16 -tif, SDK 1.4) with the two IDC
profiles embedded in 4-plane LinearRaw DNGs of real F828 data: RMS 0.0015, max 0.016 in
8-bit units (dng_ref_sdk_check.py).

What this does not model, and what Lightroom / Camera Raw may do differently:
  * Process versions. The SDK renders the profile as DNG 1.4 defines it: matrix, HueSatMap,
    exposure, LookTable, ProfileToneCurve. Camera Raw's process versions (2003 / 2010 / 2012
    and the later "Version 4..6") wrap their own tone controls around this: at default
    settings PV 2012+ is meant to leave the profile's look alone, PV 2003 / 2010 add their
    Brightness / Contrast defaults. Not verifiable without Camera Raw (dcp_validate.py
    --compare measures a real Lightroom export against IDC's TIFF).
  * BaselineExposure. For a DNG it is the tag (the converter writes it). For a proprietary
    raw file Camera Raw uses its own per-camera value, which Adobe does not publish: it moves
    the whole image by that many stops. To see it, convert one .SRF with Adobe DNG Converter
    and read the DNG's BaselineExposure tag (exiftool -BaselineExposure file.dng); 0 means
    the profile's brightness is right as it is.
  * Highlights: the SDK clips each camera channel at the white balance's white before the
    profile; Camera Raw has its own highlight recovery. Also gamut mapping on output,
    demosaic, sharpening and noise reduction.
"""
import math
import numpy as np

# --------------------------------------------------------------------------------------
# constants (dng_xy_coord.h, dng_color_space.cpp)

D50_XY = (0.3457, 0.3585)
D55_XY = (0.3324, 0.3474)
D65_XY = (0.3127, 0.3290)


def xy_to_xyz(xy):
    x = min(max(xy[0], 0.000001), 0.999999)
    y = min(max(xy[1], 0.000001), 0.999999)
    if x + y > 0.999999:
        s = 0.999999 / (x + y)
        x, y = x * s, y * s
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def xyz_to_xy(xyz):
    t = float(np.sum(xyz))
    if t > 0:
        return (float(xyz[0]) / t, float(xyz[1]) / t)
    return D50_XY


PCS_XYZ = xy_to_xyz(D50_XY)


def _space(m):
    """dng_color_space::SetMatrixToPCS: scale rows so RGB (1,1,1) maps exactly to the PCS white."""
    m = np.asarray(m, dtype=np.float64)
    to_pcs = np.diag(PCS_XYZ / (m @ np.ones(3))) @ m
    return to_pcs, np.linalg.inv(to_pcs)


PROPHOTO_TO_PCS, PCS_TO_PROPHOTO = _space([[0.7977, 0.1352, 0.0313],
                                           [0.2880, 0.7119, 0.0001],
                                           [0.0000, 0.0000, 0.8249]])
SRGB_TO_PCS, PCS_TO_SRGB = _space([[0.4361, 0.3851, 0.1431],
                                   [0.2225, 0.7169, 0.0606],
                                   [0.0139, 0.0971, 0.7141]])
ADOBE_TO_PCS, PCS_TO_ADOBE = _space([[0.6097, 0.2053, 0.1492],
                                     [0.3111, 0.6257, 0.0632],
                                     [0.0195, 0.0609, 0.7446]])
PROPHOTO_TO_SRGB = PCS_TO_SRGB @ PROPHOTO_TO_PCS


def srgb_encode(x):
    x = np.asarray(x)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(np.maximum(x, 0.0031308), 1.0 / 2.4) - 0.055)


def srgb_decode(y):
    y = np.asarray(y)
    return np.where(y <= 0.0031308 * 12.92, y / 12.92, np.power((np.maximum(y, 0.04) + 0.055) / 1.055, 2.4))


# --------------------------------------------------------------------------------------
# colour temperature (dng_temperature.cpp): Robertson's isotemperature lines, tint scale -3000

_TINT_SCALE = -3000.0
_RUVT = np.array([
    [0, 0.18006, 0.26352, -0.24341], [10, 0.18066, 0.26589, -0.25479], [20, 0.18133, 0.26846, -0.26876],
    [30, 0.18208, 0.27119, -0.28539], [40, 0.18293, 0.27407, -0.30470], [50, 0.18388, 0.27709, -0.32675],
    [60, 0.18494, 0.28021, -0.35156], [70, 0.18611, 0.28342, -0.37915], [80, 0.18740, 0.28668, -0.40955],
    [90, 0.18880, 0.28997, -0.44278], [100, 0.19032, 0.29326, -0.47888], [125, 0.19462, 0.30141, -0.58204],
    [150, 0.19962, 0.30921, -0.70471], [175, 0.20525, 0.31647, -0.84901], [200, 0.21142, 0.32312, -1.0182],
    [225, 0.21807, 0.32909, -1.2168], [250, 0.22511, 0.33439, -1.4512], [275, 0.23247, 0.33904, -1.7298],
    [300, 0.24010, 0.34308, -2.0637], [325, 0.24702, 0.34655, -2.4681], [350, 0.25591, 0.34951, -2.9641],
    [375, 0.26400, 0.35200, -3.5814], [400, 0.27218, 0.35407, -4.3633], [425, 0.28039, 0.35577, -5.3762],
    [450, 0.28863, 0.35714, -6.7262], [475, 0.29685, 0.35823, -8.5955], [500, 0.30505, 0.35907, -11.324],
    [525, 0.31320, 0.35968, -15.628], [550, 0.32129, 0.36011, -23.325], [575, 0.32931, 0.36038, -40.770],
    [600, 0.33724, 0.36051, -116.45]])


def xy_to_temp_tint(xy):
    """dng_temperature::Set_xy_coord -> (temperature K, tint), as Lightroom / ACR show them."""
    x, y = xy
    u = 2.0 * x / (1.5 - x + 6.0 * y)
    v = 3.0 * y / (1.5 - x + 6.0 * y)
    last_dt = last_dv = last_du = 0.0
    for i in range(1, 31):
        du, dv = 1.0, _RUVT[i, 3]
        ln = math.sqrt(1.0 + dv * dv)
        du, dv = du / ln, dv / ln
        uu, vv = u - _RUVT[i, 1], v - _RUVT[i, 2]
        dt = -uu * dv + vv * du
        if dt <= 0.0 or i == 30:
            if dt > 0.0:
                dt = 0.0
            dt = -dt
            f = 0.0 if i == 1 else dt / (last_dt + dt)
            temp = 1.0e6 / (_RUVT[i - 1, 0] * f + _RUVT[i, 0] * (1.0 - f))
            uu = u - (_RUVT[i - 1, 1] * f + _RUVT[i, 1] * (1.0 - f))
            vv = v - (_RUVT[i - 1, 2] * f + _RUVT[i, 2] * (1.0 - f))
            du = du * (1.0 - f) + last_du * f
            dv = dv * (1.0 - f) + last_dv * f
            ln = math.sqrt(du * du + dv * dv)
            du, dv = du / ln, dv / ln
            return temp, (uu * du + vv * dv) * _TINT_SCALE
        last_dt, last_du, last_dv = dt, du, dv


def temp_tint_to_xy(temp, tint):
    """dng_temperature::Get_xy_coord."""
    r = 1.0e6 / temp
    offset = tint * (1.0 / _TINT_SCALE)
    for i in range(30):
        if r < _RUVT[i + 1, 0] or i == 29:
            f = (_RUVT[i + 1, 0] - r) / (_RUVT[i + 1, 0] - _RUVT[i, 0])
            u = _RUVT[i, 1] * f + _RUVT[i + 1, 1] * (1.0 - f)
            v = _RUVT[i, 2] * f + _RUVT[i + 1, 2] * (1.0 - f)
            uu1, vv1 = 1.0, _RUVT[i, 3]
            uu2, vv2 = 1.0, _RUVT[i + 1, 3]
            l1, l2 = math.sqrt(1.0 + vv1 * vv1), math.sqrt(1.0 + vv2 * vv2)
            uu1, vv1, uu2, vv2 = uu1 / l1, vv1 / l1, uu2 / l2, vv2 / l2
            uu3 = uu1 * f + uu2 * (1.0 - f)
            vv3 = vv1 * f + vv2 * (1.0 - f)
            l3 = math.sqrt(uu3 * uu3 + vv3 * vv3)
            u += uu3 / l3 * offset
            v += vv3 / l3 * offset
            return (1.5 * u / (u - 4.0 * v + 2.0), v / (u - 4.0 * v + 2.0))


# dng_camera_profile::IlluminantToTemperature (EXIF LightSource codes)
_ILLUMINANT_K = {17: 2850.0, 3: 2850.0, 24: 3200.0, 23: 5000.0, 20: 5500.0, 1: 5500.0, 9: 5500.0, 4: 5500.0,
                 18: 5500.0, 21: 6500.0, 19: 6500.0, 10: 6500.0, 22: 7500.0, 11: 7500.0,
                 12: (5700.0 + 7100.0) * 0.5, 13: (4600.0 + 5500.0) * 0.5, 14: (3800.0 + 4500.0) * 0.5,
                 2: (3800.0 + 4500.0) * 0.5, 15: (3250.0 + 3800.0) * 0.5, 16: (2600.0 + 3250.0) * 0.5}


def illuminant_temperature(code):
    return _ILLUMINANT_K.get(int(code), 0.0)


def pinv(a):
    """dng_matrix Invert for a non-square matrix: (A^T A)^-1 A^T."""
    a = np.asarray(a, dtype=np.float64)
    if a.shape[0] == a.shape[1]:
        return np.linalg.inv(a)
    return np.linalg.inv(a.T @ a) @ a.T


def bradford_white_map(white1, white2):
    """dng_color_spec MapWhiteMatrix: linearised Bradford from xy white1 to xy white2."""
    mb = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    w1 = np.maximum(mb @ xy_to_xyz(white1), 0.0)
    w2 = np.maximum(mb @ xy_to_xyz(white2), 0.0)
    a = np.diag([min(max(w2[i] / w1[i] if w1[i] > 0 else 10.0, 0.1), 10.0) for i in range(3)])
    return np.linalg.inv(mb) @ a @ mb

# The SDK's default tone curve (dng_tone_curve_acr3_default), 1025 samples on [0, 1].
ACR3_DEFAULT_CURVE = np.array([
    0.00000, 0.00078, 0.00160, 0.00242, 0.00314, 0.00385, 0.00460, 0.00539, 0.00623, 0.00712,
    0.00806, 0.00906, 0.01012, 0.01122, 0.01238, 0.01359, 0.01485, 0.01616, 0.01751, 0.01890,
    0.02033, 0.02180, 0.02331, 0.02485, 0.02643, 0.02804, 0.02967, 0.03134, 0.03303, 0.03475,
    0.03648, 0.03824, 0.04002, 0.04181, 0.04362, 0.04545, 0.04730, 0.04916, 0.05103, 0.05292,
    0.05483, 0.05675, 0.05868, 0.06063, 0.06259, 0.06457, 0.06655, 0.06856, 0.07057, 0.07259,
    0.07463, 0.07668, 0.07874, 0.08081, 0.08290, 0.08499, 0.08710, 0.08921, 0.09134, 0.09348,
    0.09563, 0.09779, 0.09996, 0.10214, 0.10433, 0.10652, 0.10873, 0.11095, 0.11318, 0.11541,
    0.11766, 0.11991, 0.12218, 0.12445, 0.12673, 0.12902, 0.13132, 0.13363, 0.13595, 0.13827,
    0.14061, 0.14295, 0.14530, 0.14765, 0.15002, 0.15239, 0.15477, 0.15716, 0.15956, 0.16197,
    0.16438, 0.16680, 0.16923, 0.17166, 0.17410, 0.17655, 0.17901, 0.18148, 0.18395, 0.18643,
    0.18891, 0.19141, 0.19391, 0.19641, 0.19893, 0.20145, 0.20398, 0.20651, 0.20905, 0.21160,
    0.21416, 0.21672, 0.21929, 0.22185, 0.22440, 0.22696, 0.22950, 0.23204, 0.23458, 0.23711,
    0.23963, 0.24215, 0.24466, 0.24717, 0.24967, 0.25216, 0.25465, 0.25713, 0.25961, 0.26208,
    0.26454, 0.26700, 0.26945, 0.27189, 0.27433, 0.27676, 0.27918, 0.28160, 0.28401, 0.28641,
    0.28881, 0.29120, 0.29358, 0.29596, 0.29833, 0.30069, 0.30305, 0.30540, 0.30774, 0.31008,
    0.31241, 0.31473, 0.31704, 0.31935, 0.32165, 0.32395, 0.32623, 0.32851, 0.33079, 0.33305,
    0.33531, 0.33756, 0.33981, 0.34205, 0.34428, 0.34650, 0.34872, 0.35093, 0.35313, 0.35532,
    0.35751, 0.35969, 0.36187, 0.36404, 0.36620, 0.36835, 0.37050, 0.37264, 0.37477, 0.37689,
    0.37901, 0.38112, 0.38323, 0.38533, 0.38742, 0.38950, 0.39158, 0.39365, 0.39571, 0.39777,
    0.39982, 0.40186, 0.40389, 0.40592, 0.40794, 0.40996, 0.41197, 0.41397, 0.41596, 0.41795,
    0.41993, 0.42191, 0.42388, 0.42584, 0.42779, 0.42974, 0.43168, 0.43362, 0.43554, 0.43747,
    0.43938, 0.44129, 0.44319, 0.44509, 0.44698, 0.44886, 0.45073, 0.45260, 0.45447, 0.45632,
    0.45817, 0.46002, 0.46186, 0.46369, 0.46551, 0.46733, 0.46914, 0.47095, 0.47275, 0.47454,
    0.47633, 0.47811, 0.47989, 0.48166, 0.48342, 0.48518, 0.48693, 0.48867, 0.49041, 0.49214,
    0.49387, 0.49559, 0.49730, 0.49901, 0.50072, 0.50241, 0.50410, 0.50579, 0.50747, 0.50914,
    0.51081, 0.51247, 0.51413, 0.51578, 0.51742, 0.51906, 0.52069, 0.52232, 0.52394, 0.52556,
    0.52717, 0.52878, 0.53038, 0.53197, 0.53356, 0.53514, 0.53672, 0.53829, 0.53986, 0.54142,
    0.54297, 0.54452, 0.54607, 0.54761, 0.54914, 0.55067, 0.55220, 0.55371, 0.55523, 0.55673,
    0.55824, 0.55973, 0.56123, 0.56271, 0.56420, 0.56567, 0.56715, 0.56861, 0.57007, 0.57153,
    0.57298, 0.57443, 0.57587, 0.57731, 0.57874, 0.58017, 0.58159, 0.58301, 0.58443, 0.58583,
    0.58724, 0.58864, 0.59003, 0.59142, 0.59281, 0.59419, 0.59556, 0.59694, 0.59830, 0.59966,
    0.60102, 0.60238, 0.60373, 0.60507, 0.60641, 0.60775, 0.60908, 0.61040, 0.61173, 0.61305,
    0.61436, 0.61567, 0.61698, 0.61828, 0.61957, 0.62087, 0.62216, 0.62344, 0.62472, 0.62600,
    0.62727, 0.62854, 0.62980, 0.63106, 0.63232, 0.63357, 0.63482, 0.63606, 0.63730, 0.63854,
    0.63977, 0.64100, 0.64222, 0.64344, 0.64466, 0.64587, 0.64708, 0.64829, 0.64949, 0.65069,
    0.65188, 0.65307, 0.65426, 0.65544, 0.65662, 0.65779, 0.65897, 0.66013, 0.66130, 0.66246,
    0.66362, 0.66477, 0.66592, 0.66707, 0.66821, 0.66935, 0.67048, 0.67162, 0.67275, 0.67387,
    0.67499, 0.67611, 0.67723, 0.67834, 0.67945, 0.68055, 0.68165, 0.68275, 0.68385, 0.68494,
    0.68603, 0.68711, 0.68819, 0.68927, 0.69035, 0.69142, 0.69249, 0.69355, 0.69461, 0.69567,
    0.69673, 0.69778, 0.69883, 0.69988, 0.70092, 0.70196, 0.70300, 0.70403, 0.70506, 0.70609,
    0.70711, 0.70813, 0.70915, 0.71017, 0.71118, 0.71219, 0.71319, 0.71420, 0.71520, 0.71620,
    0.71719, 0.71818, 0.71917, 0.72016, 0.72114, 0.72212, 0.72309, 0.72407, 0.72504, 0.72601,
    0.72697, 0.72794, 0.72890, 0.72985, 0.73081, 0.73176, 0.73271, 0.73365, 0.73460, 0.73554,
    0.73647, 0.73741, 0.73834, 0.73927, 0.74020, 0.74112, 0.74204, 0.74296, 0.74388, 0.74479,
    0.74570, 0.74661, 0.74751, 0.74842, 0.74932, 0.75021, 0.75111, 0.75200, 0.75289, 0.75378,
    0.75466, 0.75555, 0.75643, 0.75730, 0.75818, 0.75905, 0.75992, 0.76079, 0.76165, 0.76251,
    0.76337, 0.76423, 0.76508, 0.76594, 0.76679, 0.76763, 0.76848, 0.76932, 0.77016, 0.77100,
    0.77183, 0.77267, 0.77350, 0.77432, 0.77515, 0.77597, 0.77680, 0.77761, 0.77843, 0.77924,
    0.78006, 0.78087, 0.78167, 0.78248, 0.78328, 0.78408, 0.78488, 0.78568, 0.78647, 0.78726,
    0.78805, 0.78884, 0.78962, 0.79040, 0.79118, 0.79196, 0.79274, 0.79351, 0.79428, 0.79505,
    0.79582, 0.79658, 0.79735, 0.79811, 0.79887, 0.79962, 0.80038, 0.80113, 0.80188, 0.80263,
    0.80337, 0.80412, 0.80486, 0.80560, 0.80634, 0.80707, 0.80780, 0.80854, 0.80926, 0.80999,
    0.81072, 0.81144, 0.81216, 0.81288, 0.81360, 0.81431, 0.81503, 0.81574, 0.81645, 0.81715,
    0.81786, 0.81856, 0.81926, 0.81996, 0.82066, 0.82135, 0.82205, 0.82274, 0.82343, 0.82412,
    0.82480, 0.82549, 0.82617, 0.82685, 0.82753, 0.82820, 0.82888, 0.82955, 0.83022, 0.83089,
    0.83155, 0.83222, 0.83288, 0.83354, 0.83420, 0.83486, 0.83552, 0.83617, 0.83682, 0.83747,
    0.83812, 0.83877, 0.83941, 0.84005, 0.84069, 0.84133, 0.84197, 0.84261, 0.84324, 0.84387,
    0.84450, 0.84513, 0.84576, 0.84639, 0.84701, 0.84763, 0.84825, 0.84887, 0.84949, 0.85010,
    0.85071, 0.85132, 0.85193, 0.85254, 0.85315, 0.85375, 0.85436, 0.85496, 0.85556, 0.85615,
    0.85675, 0.85735, 0.85794, 0.85853, 0.85912, 0.85971, 0.86029, 0.86088, 0.86146, 0.86204,
    0.86262, 0.86320, 0.86378, 0.86435, 0.86493, 0.86550, 0.86607, 0.86664, 0.86720, 0.86777,
    0.86833, 0.86889, 0.86945, 0.87001, 0.87057, 0.87113, 0.87168, 0.87223, 0.87278, 0.87333,
    0.87388, 0.87443, 0.87497, 0.87552, 0.87606, 0.87660, 0.87714, 0.87768, 0.87821, 0.87875,
    0.87928, 0.87981, 0.88034, 0.88087, 0.88140, 0.88192, 0.88244, 0.88297, 0.88349, 0.88401,
    0.88453, 0.88504, 0.88556, 0.88607, 0.88658, 0.88709, 0.88760, 0.88811, 0.88862, 0.88912,
    0.88963, 0.89013, 0.89063, 0.89113, 0.89163, 0.89212, 0.89262, 0.89311, 0.89360, 0.89409,
    0.89458, 0.89507, 0.89556, 0.89604, 0.89653, 0.89701, 0.89749, 0.89797, 0.89845, 0.89892,
    0.89940, 0.89987, 0.90035, 0.90082, 0.90129, 0.90176, 0.90222, 0.90269, 0.90316, 0.90362,
    0.90408, 0.90454, 0.90500, 0.90546, 0.90592, 0.90637, 0.90683, 0.90728, 0.90773, 0.90818,
    0.90863, 0.90908, 0.90952, 0.90997, 0.91041, 0.91085, 0.91130, 0.91173, 0.91217, 0.91261,
    0.91305, 0.91348, 0.91392, 0.91435, 0.91478, 0.91521, 0.91564, 0.91606, 0.91649, 0.91691,
    0.91734, 0.91776, 0.91818, 0.91860, 0.91902, 0.91944, 0.91985, 0.92027, 0.92068, 0.92109,
    0.92150, 0.92191, 0.92232, 0.92273, 0.92314, 0.92354, 0.92395, 0.92435, 0.92475, 0.92515,
    0.92555, 0.92595, 0.92634, 0.92674, 0.92713, 0.92753, 0.92792, 0.92831, 0.92870, 0.92909,
    0.92947, 0.92986, 0.93025, 0.93063, 0.93101, 0.93139, 0.93177, 0.93215, 0.93253, 0.93291,
    0.93328, 0.93366, 0.93403, 0.93440, 0.93478, 0.93515, 0.93551, 0.93588, 0.93625, 0.93661,
    0.93698, 0.93734, 0.93770, 0.93807, 0.93843, 0.93878, 0.93914, 0.93950, 0.93986, 0.94021,
    0.94056, 0.94092, 0.94127, 0.94162, 0.94197, 0.94231, 0.94266, 0.94301, 0.94335, 0.94369,
    0.94404, 0.94438, 0.94472, 0.94506, 0.94540, 0.94573, 0.94607, 0.94641, 0.94674, 0.94707,
    0.94740, 0.94774, 0.94807, 0.94839, 0.94872, 0.94905, 0.94937, 0.94970, 0.95002, 0.95035,
    0.95067, 0.95099, 0.95131, 0.95163, 0.95194, 0.95226, 0.95257, 0.95289, 0.95320, 0.95351,
    0.95383, 0.95414, 0.95445, 0.95475, 0.95506, 0.95537, 0.95567, 0.95598, 0.95628, 0.95658,
    0.95688, 0.95718, 0.95748, 0.95778, 0.95808, 0.95838, 0.95867, 0.95897, 0.95926, 0.95955,
    0.95984, 0.96013, 0.96042, 0.96071, 0.96100, 0.96129, 0.96157, 0.96186, 0.96214, 0.96242,
    0.96271, 0.96299, 0.96327, 0.96355, 0.96382, 0.96410, 0.96438, 0.96465, 0.96493, 0.96520,
    0.96547, 0.96574, 0.96602, 0.96629, 0.96655, 0.96682, 0.96709, 0.96735, 0.96762, 0.96788,
    0.96815, 0.96841, 0.96867, 0.96893, 0.96919, 0.96945, 0.96971, 0.96996, 0.97022, 0.97047,
    0.97073, 0.97098, 0.97123, 0.97149, 0.97174, 0.97199, 0.97223, 0.97248, 0.97273, 0.97297,
    0.97322, 0.97346, 0.97371, 0.97395, 0.97419, 0.97443, 0.97467, 0.97491, 0.97515, 0.97539,
    0.97562, 0.97586, 0.97609, 0.97633, 0.97656, 0.97679, 0.97702, 0.97725, 0.97748, 0.97771,
    0.97794, 0.97817, 0.97839, 0.97862, 0.97884, 0.97907, 0.97929, 0.97951, 0.97973, 0.97995,
    0.98017, 0.98039, 0.98061, 0.98082, 0.98104, 0.98125, 0.98147, 0.98168, 0.98189, 0.98211,
    0.98232, 0.98253, 0.98274, 0.98295, 0.98315, 0.98336, 0.98357, 0.98377, 0.98398, 0.98418,
    0.98438, 0.98458, 0.98478, 0.98498, 0.98518, 0.98538, 0.98558, 0.98578, 0.98597, 0.98617,
    0.98636, 0.98656, 0.98675, 0.98694, 0.98714, 0.98733, 0.98752, 0.98771, 0.98789, 0.98808,
    0.98827, 0.98845, 0.98864, 0.98882, 0.98901, 0.98919, 0.98937, 0.98955, 0.98973, 0.98991,
    0.99009, 0.99027, 0.99045, 0.99063, 0.99080, 0.99098, 0.99115, 0.99133, 0.99150, 0.99167,
    0.99184, 0.99201, 0.99218, 0.99235, 0.99252, 0.99269, 0.99285, 0.99302, 0.99319, 0.99335,
    0.99351, 0.99368, 0.99384, 0.99400, 0.99416, 0.99432, 0.99448, 0.99464, 0.99480, 0.99495,
    0.99511, 0.99527, 0.99542, 0.99558, 0.99573, 0.99588, 0.99603, 0.99619, 0.99634, 0.99649,
    0.99664, 0.99678, 0.99693, 0.99708, 0.99722, 0.99737, 0.99751, 0.99766, 0.99780, 0.99794,
    0.99809, 0.99823, 0.99837, 0.99851, 0.99865, 0.99879, 0.99892, 0.99906, 0.99920, 0.99933,
    0.99947, 0.99960, 0.99974, 0.99987, 1.00000,])


def acr3_default_curve(x):
    return np.interp(np.clip(x, 0.0, 1.0), np.linspace(0.0, 1.0, ACR3_DEFAULT_CURVE.size), ACR3_DEFAULT_CURVE)


# --------------------------------------------------------------------------------------
# splines (dng_spline_solver): C2 cubic through the points, zero second derivative at the ends

class Spline:
    def __init__(self, pts):
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        X, Y = pts[:, 0].copy(), pts[:, 1].copy()
        n = len(X)
        S = np.zeros(n)
        A = X[1] - X[0]
        B = (Y[1] - Y[0]) / A
        S[0] = B
        for j in range(2, n):
            C = X[j] - X[j - 1]
            D = (Y[j] - Y[j - 1]) / C
            S[j - 1] = (B * C + D * A) / (A + C)
            A, B = C, D
        S[n - 1] = 2.0 * B - S[n - 2]
        S[0] = 2.0 * S[0] - S[1]
        if n > 2:
            E, F, G = np.zeros(n), np.zeros(n), np.zeros(n)
            F[0] = 0.5
            E[n - 1] = 0.5
            G[0] = 0.75 * (S[0] + S[1])
            G[n - 1] = 0.75 * (S[n - 2] + S[n - 1])
            for j in range(1, n - 1):
                A = (X[j + 1] - X[j - 1]) * 2.0
                E[j] = (X[j + 1] - X[j]) / A
                F[j] = (X[j] - X[j - 1]) / A
                G[j] = 1.5 * S[j]
            for j in range(1, n):
                A = 1.0 - F[j - 1] * E[j]
                if j != n - 1:
                    F[j] /= A
                G[j] = (G[j] - G[j - 1] * E[j]) / A
            for j in range(n - 2, -1, -1):
                G[j] = G[j] - F[j] * G[j + 1]
            S = G
        self.X, self.Y, self.S = X, Y, S

    def __call__(self, x):
        x = np.asarray(x, dtype=np.float64)
        X, Y, S = self.X, self.Y, self.S
        j = np.clip(np.searchsorted(X, x, side='left'), 1, len(X) - 1)
        x0, x1, y0, y1, s0, s1 = X[j - 1], X[j], Y[j - 1], Y[j], S[j - 1], S[j]
        A = x1 - x0
        Bf = (x - x0) / A
        Cf = (x1 - x) / A
        d = (y0 * (2.0 - Cf + Bf) + s0 * A * Bf) * Cf * Cf + (y1 * (2.0 - Bf + Cf) - s1 * A * Cf) * Bf * Bf
        return np.where(x <= X[0], Y[0], np.where(x >= X[-1], Y[-1], d))


# --------------------------------------------------------------------------------------
# profiles

class Profile:
    """A camera profile. Tables are numpy arrays shaped (vals, hues, sats, 3) holding
    (hue shift in degrees, saturation scale, value scale), the order the tags store them."""

    def __init__(self, **kw):
        self.name = kw.get('name', '')
        self.unique_camera_model = kw.get('unique_camera_model', '')
        self.copyright = kw.get('copyright', '')
        self.embed_policy = kw.get('embed_policy', 0)
        self.illuminant1 = kw.get('illuminant1', 0)
        self.illuminant2 = kw.get('illuminant2', 0)
        self.cm1 = None if kw.get('cm1') is None else np.asarray(kw['cm1'], dtype=np.float64)
        self.cm2 = None if kw.get('cm2') is None else np.asarray(kw['cm2'], dtype=np.float64)
        self.fm1 = None if kw.get('fm1') is None else np.asarray(kw['fm1'], dtype=np.float64)
        self.fm2 = None if kw.get('fm2') is None else np.asarray(kw['fm2'], dtype=np.float64)
        self.hsm1 = kw.get('hsm1')
        self.hsm2 = kw.get('hsm2')
        self.hsm_encoding = kw.get('hsm_encoding', 0)
        self.look = kw.get('look')
        self.look_encoding = kw.get('look_encoding', 0)
        self.tone_curve = None if kw.get('tone_curve') is None else np.asarray(kw['tone_curve'], dtype=np.float64)
        self.baseline_exposure_offset = float(kw.get('baseline_exposure_offset', 0.0))
        self.default_black_render = int(kw.get('default_black_render', 0))

    def copy(self, **kw):
        d = dict(self.__dict__)
        d.update(kw)
        return Profile(**d)


def _normalize_cm(m):
    """dng_camera_profile::NormalizeColorMatrix (applied when the profile is read)."""
    m = np.array(m, dtype=np.float64)
    mx = (m @ PCS_XYZ).max()
    if mx > 0 and (mx < 0.99 or mx > 1.01):
        m = m / mx
    return np.round(m * 10000.0) / 10000.0


def _normalize_fm(m):
    """Rounded to 4 decimals when read, then scaled so FM (1,..,1) = PCS white."""
    m = np.round(np.asarray(m, dtype=np.float64) * 10000.0) / 10000.0
    return np.diag(PCS_XYZ / (m @ np.ones(m.shape[1]))) @ m


class ColorSpec:
    """dng_color_spec for one profile (AnalogBalance and CameraCalibration = identity)."""

    def __init__(self, p):
        self.p = p
        self.t1 = illuminant_temperature(p.illuminant1)
        self.t2 = illuminant_temperature(p.illuminant2)
        self.cm1 = _normalize_cm(p.cm1)
        self.fm1 = None if p.fm1 is None else _normalize_fm(p.fm1)
        if p.cm2 is None or self.t1 <= 0 or self.t2 <= 0 or self.t1 == self.t2:
            self.t1 = self.t2 = 5000.0
            self.cm2, self.fm2 = self.cm1, self.fm1
        else:
            self.cm2 = _normalize_cm(p.cm2)
            self.fm2 = None if p.fm2 is None else _normalize_fm(p.fm2)
            if self.t1 > self.t2:
                self.t1, self.t2 = self.t2, self.t1
                self.cm1, self.cm2 = self.cm2, self.cm1
                self.fm1, self.fm2 = self.fm2, self.fm1

    def weight(self, xy):
        """Weight g of the first (lower temperature) calibration, linear in 1/T."""
        t = xy_to_temp_tint(xy)[0]
        if t <= self.t1:
            return 1.0
        if t >= self.t2:
            return 0.0
        return (1.0 / t - 1.0 / self.t2) / (1.0 / self.t1 - 1.0 / self.t2)

    def xyz_to_camera(self, xy):
        g = self.weight(xy)
        cm = g * self.cm1 + (1.0 - g) * self.cm2
        if self.fm1 is not None and self.fm2 is not None:
            fm = g * self.fm1 + (1.0 - g) * self.fm2
        else:
            fm = self.fm1 if self.fm1 is not None else self.fm2
        return cm, fm, g

    def neutral_to_xy(self, neutral):
        neutral = np.asarray(neutral, dtype=np.float64)
        last = D50_XY
        for i in range(30):
            cm, _, _ = self.xyz_to_camera(last)
            nxt = xyz_to_xy(pinv(cm) @ neutral)
            if abs(nxt[0] - last[0]) + abs(nxt[1] - last[1]) < 0.0000001:
                return nxt
            if i == 29:
                nxt = ((last[0] + nxt[0]) * 0.5, (last[1] + nxt[1]) * 0.5)
            last = nxt
        return last

    def set_white_xy(self, xy):
        """-> (camera white (n,), camera -> PCS (3, n), weight g)"""
        cm, fm, g = self.xyz_to_camera(xy)
        white = cm @ xy_to_xyz(xy)
        white = np.clip(white / white.max(), 0.001, 1.0)
        pcs_to_cam = cm @ bradford_white_map(D50_XY, xy)
        pcs_to_cam = pcs_to_cam / (pcs_to_cam @ PCS_XYZ).max()
        if fm is not None:
            cam_to_pcs = fm @ np.diag(1.0 / white)
        else:
            cam_to_pcs = pinv(pcs_to_cam)
        return white, cam_to_pcs, g


def hue_sat_map_for_white(p, g_profile):
    """dng_camera_profile::HueSatMapForWhite, given the first-illuminant weight in the
    profile's own tag order."""
    if p.hsm1 is None:
        return None
    if p.hsm2 is None:
        return np.asarray(p.hsm1, dtype=np.float64)
    return g_profile * np.asarray(p.hsm1, dtype=np.float64) + (1.0 - g_profile) * np.asarray(p.hsm2, dtype=np.float64)


# --------------------------------------------------------------------------------------
# per-pixel stages

def rgb_to_hsv(rgb):
    """DNG_RGBtoHSV, hue in [0, 6)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    v = np.maximum(np.maximum(r, g), b)
    gap = v - np.minimum(np.minimum(r, g), b)
    ok = gap > 0
    sg = np.where(ok, gap, 1.0)
    h = np.where(r == v, (g - b) / sg, np.where(g == v, 2.0 + (b - r) / sg, 4.0 + (r - g) / sg))
    h = np.where(h < 0, h + 6.0, h)
    h = np.where(ok, h, 0.0)
    s = np.where(ok, gap / np.where(v > 0, v, 1.0), 0.0)
    return h, s, v


def hsv_to_rgb(h, s, v):
    """DNG_HSVtoRGB."""
    h = np.where(h < 0, h + 6.0, h)
    h = np.where(h >= 6.0, h - 6.0, h)
    i = np.floor(h).astype(np.int32)
    f = h - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    r = np.choose(np.clip(i, 0, 5), [v, q, p, p, t, v])
    g = np.choose(np.clip(i, 0, 5), [t, v, v, q, p, p])
    b = np.choose(np.clip(i, 0, 5), [p, p, t, v, v, q])
    grey = s <= 0
    return np.stack([np.where(grey, v, r), np.where(grey, v, g), np.where(grey, v, b)], -1)


def table_weights(h, s, v_enc, dims):
    """Indices and trilinear weights of RefBaselineHueSatMap. Returns a list of
    (flat index into the (vals, hues, sats) table, weight) for the 8 (or 4) corners."""
    nv, nh, ns = dims
    hs = h * (nh / 6.0) if nh >= 2 else h * 0.0
    ss = s * (ns - 1)
    h0 = hs.astype(np.int64)
    s0 = np.minimum(ss.astype(np.int64), ns - 2)
    h1 = h0 + 1
    wrap = h0 >= nh - 1
    h0 = np.where(wrap, nh - 1, h0)
    h1 = np.where(wrap, 0, h1)
    hf = hs - h0
    sf = ss - s0
    out = []
    if nv < 2:
        for hi, hw in ((h0, 1.0 - hf), (h1, hf)):
            for si, sw in ((s0, 1.0 - sf), (s0 + 1, sf)):
                out.append((hi * ns + si, hw * sw))
        return out
    vs = v_enc * (nv - 1)
    v0 = np.minimum(vs.astype(np.int64), nv - 2)
    vf = vs - v0
    for vi, vw in ((v0, 1.0 - vf), (v0 + 1, vf)):
        for hi, hw in ((h0, 1.0 - hf), (h1, hf)):
            for si, sw in ((s0, 1.0 - sf), (s0 + 1, sf)):
                out.append(((vi * nh + hi) * ns + si, vw * hw * sw))
    return out


def apply_hue_sat_map(rgb, table, encoding=0):
    """RefBaselineHueSatMap. rgb (..., 3) linear ProPhoto; table (vals, hues, sats, 3)."""
    table = np.asarray(table, dtype=np.float64)
    nv, nh, ns, _ = table.shape
    h, s, v = rgb_to_hsv(rgb)
    use_enc = encoding == 1 and nv >= 2           # the SDK only encodes in its 3-D branch
    v_enc = srgb_encode(np.clip(v, 0.0, 1.0)) if use_enc else v
    flat = table.reshape(-1, 3)
    mod = np.zeros(h.shape + (3,))
    for idx, w in table_weights(h, s, v_enc, (nv, nh, ns)):
        mod += w[..., None] * flat[idx]
    h = h + mod[..., 0] * (6.0 / 360.0)
    s = np.minimum(s * mod[..., 1], 1.0)
    v_enc = np.clip(v_enc * mod[..., 2], 0.0, 1.0)
    if encoding == 1:
        v = srgb_decode(v_enc)                    # (also in the 2.5-D branch, as the SDK does)
    else:
        v = v_enc
    return hsv_to_rgb(h, s, v)


def exposure_ramp(x, white, black, min_black):
    """dng_function_exposure_ramp."""
    slope = 1.0 / (white - black)
    radius = min(0.5 * min_black, (1.0 / 16.0) / slope)
    qscale = slope / (4.0 * radius) if radius > 0 else 0.0
    y = np.minimum((x - black) * slope, 1.0)
    if radius > 0:
        q = x - (black - radius)
        y = np.where(x >= black + radius, y, qscale * q * q)
    return np.where(x <= black - radius, 0.0, y)


def exposure_tone(x, exposure):
    """dng_function_exposure_tone: negative exposure as a shoulder that keeps white at white."""
    if exposure >= 0.0:
        return x
    slope = 2.0 ** exposure
    a = 16.0 / 9.0 * (1.0 - slope)
    b = slope - 0.5 * a
    c = 1.0 - a - b
    return np.where(x <= 0.25, x * slope, (a * x + b) * x + c)


def rgb_tone(rgb, curve):
    """RefBaselineRGBTone: the curve on the largest and smallest channel, the middle one
    interpolated so that the HSV hue is kept."""
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    tmx = curve(mx)
    tmn = curve(mn)
    span = mx - mn
    frac = np.where(span[..., None] > 0, (rgb - mn[..., None]) / np.where(span > 0, span, 1.0)[..., None], 0.0)
    out = tmn[..., None] + (tmx - tmn)[..., None] * frac
    # equal channels (g == b etc.) all take the curve directly
    return np.where(span[..., None] > 0, out, curve(rgb))


# --------------------------------------------------------------------------------------
# the whole render

class Renderer:
    """Set up once per (profile, white balance, exposure); then call on camera data.

    profile: Profile.  neutral: AsShotNeutral (n,), or white_xy.  exposure: user EV.
    baseline_exposure: the DNG's BaselineExposure (for a proprietary raw: ACR's own value). The
        SDK keeps it, and BaselineExposureOffset, to 0.01 EV (dng_negative::SetBaselineExposure,
        dng_camera_profile::SetBaselineExposureOffset); pass the rounded value to match it.
    shadows: the SDK's default black (5 -> 0.005 of white), forced to 0 by DefaultBlackRender=1.
    tone: None = the profile's curve, or the ACR3 default when it has none; or a callable."""

    def __init__(self, profile, neutral=None, white_xy=None, exposure=0.0, baseline_exposure=0.0,
                 shadows=5.0, tone=None, final='srgb'):
        self.spec = ColorSpec(profile)
        if white_xy is None:
            white_xy = self.spec.neutral_to_xy(neutral) if neutral is not None else D55_XY
        self.white_xy = white_xy
        self.camera_white, cam_to_pcs, g = self.spec.set_white_xy(white_xy)
        self.cam_to_rgb = PCS_TO_PROPHOTO @ cam_to_pcs
        # profile-order weight for the tables (HueSatMapForWhite)
        t1p, t2p = illuminant_temperature(profile.illuminant1), illuminant_temperature(profile.illuminant2)
        gp = g if t1p <= t2p else 1.0 - g
        self.hsm = hue_sat_map_for_white(profile, gp)
        self.hsm_encoding = profile.hsm_encoding
        self.look = None if profile.look is None else np.asarray(profile.look, dtype=np.float64)
        self.look_encoding = profile.look_encoding
        self.exposure = exposure + baseline_exposure + profile.baseline_exposure_offset
        if profile.default_black_render == 1:
            shadows = 0.0
        self.white = 1.0 / 2.0 ** max(0.0, self.exposure)
        self.black = min(shadows * 0.001, 0.99 * self.white)
        if tone is None:
            tone = Spline(profile.tone_curve) if profile.tone_curve is not None else acr3_default_curve
        self.tone = tone
        e = self.exposure
        self.curve = lambda x: tone(exposure_tone(x, e))
        self.final = {'srgb': PROPHOTO_TO_SRGB, 'prophoto': np.eye(3),
                      'adobergb': PCS_TO_ADOBE @ PROPHOTO_TO_PCS}[final]
        self.encode = srgb_encode if final == 'srgb' else (
            (lambda x: np.power(np.maximum(x, 0), 1 / 1.8)) if final == 'prophoto' else
            (lambda x: np.power(np.maximum(x, 0), 256.0 / 563.0)))

    def to_prophoto(self, cam):
        """Camera values (..., n), 0..1 -> linear ProPhoto after the matrix, pinned to [0, 1]."""
        c = np.minimum(np.clip(cam, 0.0, 1.0), self.camera_white)
        return np.clip(c @ self.cam_to_rgb.T, 0.0, 1.0)

    def hsm_stage(self, rgb):
        """Linear ProPhoto after the matrix -> after the ProfileHueSatMap."""
        if self.hsm is not None:
            rgb = apply_hue_sat_map(rgb, self.hsm, self.hsm_encoding)
        return rgb

    def from_prophoto(self, rgb, stop=None):
        """Linear ProPhoto (after the matrix) -> final encoded values. stop='look' returns
        the linear ProPhoto before the tone curve."""
        return self.from_hsm(self.hsm_stage(rgb), stop)

    def from_hsm(self, rgb, stop=None):
        """The stages after the ProfileHueSatMap (exposure, LookTable, tone, output space).
        Renderers that differ only in exposure can share the matrix and HueSatMap work."""
        rgb = exposure_ramp(rgb, self.white, self.black, self.black)
        if self.look is not None:
            rgb = apply_hue_sat_map(rgb, self.look, self.look_encoding)
        if stop == 'look':
            return rgb
        rgb = rgb_tone(rgb, self.curve)
        out = np.clip(rgb @ self.final.T, 0.0, 1.0)
        return self.encode(out)

    def __call__(self, cam, chunk=1 << 18):
        cam = np.asarray(cam)
        shp = cam.shape[:-1]
        flat = cam.reshape(-1, cam.shape[-1])
        out = np.empty((flat.shape[0], 3), dtype=np.float32)
        for i in range(0, flat.shape[0], chunk):
            out[i:i + chunk] = self.from_prophoto(self.to_prophoto(flat[i:i + chunk].astype(np.float64)))
        return out.reshape(shp + (3,))
