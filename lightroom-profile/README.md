# Sony DSC-F828: IDC "Real" and "Standard" camera profiles for Lightroom and Camera Raw

These two DNG camera profiles (`.dcp`) make Adobe Lightroom Classic and Camera Raw (ACR) render
Sony DSC-F828 raw files the way Sony Image Data Converter SR 1.1 (IDC) does. This covers IDC's
colour, its tone curve and its white-balance behaviour. They work on the camera's `.SRF` files
and on the F828 converter's DNG exports. The converter can also embed them in its DNGs.

| File | What it is |
|---|---|
| `Sony DSC-F828 IDC Real.dcp` | Profile for shots taken in Real colour mode |
| `Sony DSC-F828 IDC Standard.dcp` | Profile for shots taken in Standard colour mode |
| `f828_xmp.py` | Writes Lightroom sidecars (`.xmp`) that set each shot's real as-shot white balance and the matching profile. See [White balance](#white-balance-read-this) |
| `f828_dcp.py` | Rebuilds both profiles from `.SRF` files. Also holds `build_dcp_tags()`, which the converter calls to embed the profile in a DNG, and the commands `info`, `tags` and `retag` |
| `dng_ref.py` | Reference renderer: the DNG SDK 1.4 profile pipeline in numpy, used for fitting and checking |
| `dcp_validate.py` | Compares the profiles with IDC's own TIFFs. `--compare` measures your own Lightroom export against IDC |
| `dng_ref_sdk_check.py` | Checks `dng_ref.py` against Adobe's DNG SDK renderer (`dng_validate`) |

## Install on Windows

1. Close Lightroom and Photoshop.
2. In Explorer's address bar, type `%APPDATA%\Adobe\CameraRaw` and press Enter. Create a folder
   named `CameraProfiles` there if none exists. The full path is
   `C:\Users\<you>\AppData\Roaming\Adobe\CameraRaw\CameraProfiles`.
3. Copy both `.dcp` files into that folder, then start Lightroom or Photoshop again.
4. Select a profile:
   - **Lightroom Classic:** Develop module, Basic panel, Profile. Click the grid icon to open the
     Profile Browser. The profiles are listed by name, *Sony DSC-F828 IDC Real* and *… Standard*,
     with the other profiles for this camera. Star them to put them in the Profile drop-down.
   - **Camera Raw:** Edit tab, Profile, Browse.
   - To set the profile and the correct white balance in one step, use the sidecars below.

On macOS the folder is `~/Library/Application Support/Adobe/CameraRaw/CameraProfiles`.

The profiles are listed only for raw files whose camera name is `Sony DSC-F828`. That is
Adobe's usual naming (Make plus Model), and dcraw and LibRaw use the same key.

If they do not appear for your `.SRF` files:
1. Convert one file with Adobe DNG Converter.
2. Run `exiftool -UniqueCameraModel file.dng` to read the camera name it uses.
3. Run `python3 f828_dcp.py retag --camera-model "<that name>" *.dcp`.

## White balance (read this)

**The problem.** Every F828 `.SRF` stores the same white-balance numbers in its readable tags:
404 / 256 / 751 (SRF2 tags 0xd0–0xd2), whatever the light. All 13 test files hold identical
values in tags 0xc0–0xd2. LibRaw reads these as the camera multipliers (with E = G), so in
LibRaw-based programs "As Shot" is the same for every picture. The camera's real white balance
is in the encrypted register block that IDC develops from. It is stored as IDC's gains after its
colour matrix, registers 0x64c and 0x650. Examples:

- DSC00682 (Daylight preset): R 2.207, B 2.105.
- DSC00686 (Incandescent preset): R 1.137, B 4.297.

**What Camera Raw uses as "As Shot" for an `.SRF` is unknown.** Adobe's raw reader is closed and
I had no Camera Raw to test. Two ways to check:
- Open DSC00682 and DSC00686 with White Balance set to *As Shot*. If both show the same
  Temp/Tint, Adobe reads the constant tags, and "As Shot" is wrong for nearly every picture.
- Convert both with Adobe DNG Converter and compare `exiftool -AsShotNeutral`.

The cost of a wrong white balance is large. With the constant tags read the LibRaw way, these
profiles miss IDC by 6–47 RMS (8-bit units). See the "stored WB" column below.

**The fix: `f828_xmp.py`.** It decodes the registers through the converter's `read_srf_camera()`.
It then finds the chromaticity whose camera neutral, through the profile's colour matrix, gives
exactly IDC's gains. It writes that chromaticity as `crs:Temperature` and `crs:Tint`, in the
profile's own scale: the DNG SDK's `dng_temperature` with the profile's ColorMatrix, the scale
Lightroom shows for this profile. Each sidecar sets:
- `crs:WhiteBalance="Custom"`
- `crs:CameraProfile="Sony DSC-F828 IDC Real"`, or `… Standard`, following the shot's mode register
- `crs:Exposure2012`, the shot's exposure offset (see [Brightness](#brightness-process-versions-and-baselineexposure))
- `crs:ProcessVersion="11.0"`, but only in new sidecars

```
python3 f828_xmp.py D:\Photos\F828            # one DSC0xxxx.xmp next to each .SRF
python3 f828_xmp.py --update D:\Photos\F828   # existing sidecars: change only the white balance and profile
```

When the sidecars take effect:
- **Lightroom** reads a sidecar when it imports the raw file. For files already in the catalog,
  use *Metadata > Read Metadata from Files*, which replaces the catalog's settings for them.
- **Camera Raw** reads it when it opens the file.

Rounding Temp to 1 K and Tint to 1 unit moves the white by at most 0.0002 in u′v′ (the neutral by at most 0.2%) on these files.

| Shot | 670 | 671 | 673 | 674 | 675 | 676 | 681 | 682 | 683 | 685 | 686 | 098 | 651 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| As shot (profile scale) | 4398 / +18 | 4387 / +19 | 5458 / +13 | 5468 / +13 | 5260 / +12 | 5265 / +12 | 4947 / +25 | 5303 / +13 | 6742 / +15 | 3954 / +19 | 3163 / +9 | 5508 / +25 | 3894 / +16 |
| Exposure offset (EV) | +0.02 | +0.02 | −0.02 | −0.02 | −0.06 | −0.05 | +0.02 | −0.01 | −0.04 | +0.03 | 0.00 | +0.01 | +0.02 |

The stored tags, read the LibRaw way, would instead give about 4067 K / +84 on every shot.

The converter's DNGs do not need sidecars. `build_dcp_tags()` writes the exact AsShotNeutral.
That neutral equals the converter's own as-shot white balance, `1 / wb_from_temp_tint(dsp_temp_tint(cam))`,
to 6 decimals.

## Accuracy

`dcp_validate.py` checks the profiles against IDC's sRGB TIFFs, using all 11 labelled shots. Each raw file is:
1. demosaiced with the converter's bilinear demosaic, using the camera's black level;
2. rendered with `dng_ref.py`, which is the SDK pipeline;
3. compared on IDC's smooth 16×16 blocks.

A block counts as smooth when its standard deviation is under 2 and its values lie between 3
and 252, as in the converter's `test_export.py`.

The table gives RMS / max over block means, in 8-bit units. The columns are:
- **Adobe matrix + ACR3 curve:** Adobe's colour matrix with the SDK's default tone curve and black. This is what a plain Adobe-style profile gives, at the correct white balance.
- **IDC profile:** these profiles with no exposure change.
- **+ exposure:** with the shot's exposure offset, as in the converter DNG or the sidecar.
- **stored WB:** this profile at the constant tag white balance.
- **IDC model:** the converter's model of IDC's DSP, which is what the profiles were fitted to. Its error against IDC is a floor that no profile fitted to it can beat.

| Shot | Mode | As-shot WB | Smooth blocks | Adobe matrix + ACR3 curve | IDC profile | IDC profile + exposure | Profile at the SRF's stored WB | IDC model (floor) |
|---|---|---|---:|---|---|---|---|---|
| DSC00670 | Standard | 4398 K +18 | 758 | 17.73 / 24.2 | 0.92 / 2.0 | **0.28 / 0.9** | 27.08 / 57.2 | 0.22 / 0.7 |
| DSC00671 | Real | 4387 K +19 | 987 | 20.60 / 24.4 | 1.13 / 2.1 | **0.27 / 1.0** | 32.23 / 59.2 | 0.19 / 0.7 |
| DSC00673 | Real | 5458 K +13 | 973 | 5.01 / 26.9 | 0.32 / 1.2 | **0.30 / 1.0** | 6.34 / 78.5 | 0.21 / 0.8 |
| DSC00674 | Standard | 5468 K +13 | 7001 | 4.00 / 27.8 | 0.29 / 1.6 | **0.25 / 0.9** | 11.43 / 77.0 | 0.23 / 0.9 |
| DSC00675 | Real | 5260 K +12 | 10274 | 7.10 / 30.0 | 2.20 / 5.1 | **0.84 / 1.9** | 9.49 / 78.0 | 0.24 / 0.9 |
| DSC00676 | Standard | 5265 K +12 | 10356 | 10.32 / 30.0 | 2.12 / 3.4 | **0.70 / 2.0** | 10.10 / 74.6 | 0.22 / 0.9 |
| DSC00681 | Real | 4947 K +25 | 1502 | 13.86 / 47.5 | 1.00 / 4.1 | **0.44 / 5.1** | 27.16 / 104.2 | 0.24 / 1.0 |
| DSC00682 | Real | 5303 K +13 | 1609 | 16.04 / 71.2 | 0.73 / 7.9 | **0.50 / 7.5** | 33.11 / 125.1 | 0.21 / 0.9 |
| DSC00683 | Real | 6742 K +15 | 1520 | 20.22 / 62.3 | 2.25 / 10.6 | **0.76 / 9.2** | 46.82 / 143.3 | 0.23 / 1.0 |
| DSC00685 | Real | 3954 K +19 | 1430 | 18.88 / 85.7 | 1.60 / 13.5 | **1.07 / 13.3** | 24.79 / 59.6 | 0.22 / 0.8 |
| DSC00686 | Real | 3163 K +9 | 851 | 22.51 / 62.2 | 0.62 / 3.0 | **0.62 / 3.0** | 35.95 / 69.8 | 0.21 / 0.9 |
| **median / worst RMS** | | | | 16.04 / 22.51 | 1.00 / 2.25 | **0.50 / 1.07** | 27.08 / 46.82 | 0.22 / 0.24 |

On the profile + exposure column, the 99th percentile of the block errors is 0.6–3.5. Saturated
blocks (channel range over 60) have RMS 0.5–1.5. They occur only on the five chart shots, 376–707 blocks per shot.

The chart shots (681–686) are a colour chart shown on a monitor: 36 patches and a 21-step ramp.
Per-patch errors use the mean over a 40×40-pixel box. "Saturated" means patches whose IDC
channel range exceeds 60.

| Shot | Adobe: patches RMS / max · saturated RMS | IDC profile + exposure | IDC model | Ramp RMS / max: Adobe · profile + exp · model |
|---|---|---|---|---|
| DSC00681 | 35.20 / 183.9 · 41.4 | **3.06 / 26.1 · 3.64** | 0.83 / 6.7 | 12.0 / 27.6 · **0.59 / 3.1** · 0.22 / 0.7 |
| DSC00682 | 34.17 / 169.4 · 39.9 | **0.95 / 5.2 · 1.12** | 0.77 / 5.4 | 13.6 / 27.7 · **0.33 / 0.8** · 0.17 / 0.4 |
| DSC00683 | 35.95 / 160.8 · 41.6 | **1.97 / 7.5 · 2.10** | 0.79 / 7.4 | 17.0 / 35.4 · **1.22 / 7.2** · 0.24 / 0.7 |
| DSC00685 | 34.38 / 172.7 · 39.7 | **4.50 / 38.7 · 5.25** | 0.36 / 2.3 | 13.5 / 34.2 · **1.06 / 2.6** · 0.16 / 0.6 |
| DSC00686 | 31.75 / 153.0 · 33.5 | **5.27 / 51.2 · 5.54** | 0.30 / 1.4 | 18.9 / 46.4 · **1.79 / 10.1** · 0.22 / 0.8 |

The Adobe baseline loses mostly on bright saturated colours. It renders the monitor's green
patch as 180/255/181 where IDC gives 10/255/150. On the near-grey patches of DSC00682 it is within 2 on average.

### Where the remaining error comes from

1. **Colours brighter than white in one channel.** This is the largest single cause, and it
   accounts for the chart maxima. The SDK clips each camera channel at the white balance's white
   before the profile is applied. IDC clips later, after its matrix (at 4092 in its 12-bit units),
   and its white-balance gains reach 4.3×.

   Example: the brightest cyan patch on DSC00686 has the blue camera channel at 1.34× white.
   IDC renders it R 20, while the profile gives R 71. The top steps of the ramp on 683 and 686
   behave the same way, at up to 1.27× white. A profile cannot change this, because the clip
   comes before it. Camera Raw's own highlight handling is different again.
2. **White-balance-dependent chroma.** The camera writes its colour-difference coefficients
   (cross terms and gains) per shot, and they change with the WB preset. They are not a smooth
   function of the light: 685 (Fluorescent) is the outlier, and 651, at 3894 K, carries 686's
   Incandescent set. A profile can only blend two HueSatMaps by colour temperature, which leaves
   0.8–1.1 RMS on 683, 685 and 675.
3. **Steep toes.** Saturated cyans and greens with Sony R near 0 sit on a part of IDC's curve
   where R output jumps. On DSC00681, the patch IDC renders 2/255/174 comes out 13/254/174.
4. **Brightness per shot.** Two things shift brightness from shot to shot:
   - IDC's digital-gain register: 0.9688 on DSC00675 and 0.9766 on DSC00676.
   - The brightness of IDC's G channel, which changes with the white balance by ±1.7%.

   A profile cannot hold either, because grey's value scale must be 1 (`dng_hue_sat_map::SetDelta`). They go into
   `exposure_offset()`: the DNG's BaselineExposure or the sidecar's Exposure. Without it the
   error rises to about 2.2 RMS on 675, 676 and 683, as the "IDC profile" column shows.
5. **The IDC model itself.** The converter's floating-point model of IDC's DSP (`render_sony_dsp`)
   misses IDC by 0.19–0.24 on smooth blocks and 0.30–0.83 on patches. It leaves out IDC's 4:2:2
   chroma and its luma filter, which the converter reproduces only in its full-size exports.

## How the profile is built

Both profiles have four planes, R G B E, in the order of the converter's DNGs and of Adobe's
F828 matrix.

| Tag | Content |
|---|---|
| ColorMatrix1 = ColorMatrix2 (illuminants A and D65) | Adobe's XYZ→RGBE matrix for the F828, the converter's `XYZ_TO_RGBE`. Identical matrices make Lightroom's neutral ↔ Temp/Tint mapping exactly the converter's model, for any white. |
| ForwardMatrix1 / 2 | IDC's own camera matrix (registers 0x634.., the same in all 13 files) times its after-matrix white balance, mapped to XYZ D50 with sRGB primaries. Exact at illuminant A and at D65; the SDK interpolates in 1/T between them. |
| ProfileHueSatMap 1 / 2 | 90 hues × 16 saturations, linear. The part of IDC's colour that changes with the white balance (the chroma registers). |
| ProfileLookTable | 90 hues × 16 saturations × 16 values, sRGB-encoded value axis. The rest of IDC's colour, which depends on brightness: per-channel Sony curve, luma / colour-difference processing, chroma suppression in shadows and highlights, clipping. |
| ProfileToneCurve | 96 points: IDC's neutral response at the Sony curve's knots. It includes the Sony 12→10-bit curve, the luma contrast register (131/128) and the 255.5 / 257 (0.9942) white of IDC's YCbCr→RGB step. The spline error is 0.23/255 at most. |
| DefaultBlackRender = 1, BaselineExposureOffset = 0, EmbedPolicy = 3 (no restrictions) | |

The tables were fitted to the converter's model of IDC's DSP (`render_sony_dsp`). The training data was:
- 40,000 pixels from each of the 13 SRFs;
- 20,000 of those pushed to more chroma and other brightness;
- 16,000 plausible surface colours (CIELAB, chroma up to 100) under each shot's light;
- the chart patches of 681–686, at weight 300.

This covers as-shot whites from 3163 to 6742 K. The fit is weighted least squares on the SDK's
trilinear table lookups. Second-difference smoothing runs along hue (circular), saturation and
value. The LookTable and the two HueSatMaps are fitted in alternation, and the best iteration is kept.

The Standard profile used all 13 shots: the three Standard shots, and the ten Real shots at half
weight. For the Real shots the Standard chroma registers were interpolated by mired between the
three Standard shots. This makes it less certain than Real.

Fit error against the model on the training data, RMS /255 (from the build log):

| Profile | Pixels | Chart |
|---|---|---|
| Real | 0.19–2.11 | 0.43–0.98 |
| Standard | 0.12–1.64 | 0.34–1.01 |

Smoothness on the LookTable nodes that real pixels reach (95th percentile of the step between
neighbouring nodes):

| Quantity | Step |
|---|---|
| Hue | 2–6.5° |
| Saturation scale | 0.03–0.19 |
| Value scale | 0.01–0.09 |

The largest values sit at the saturated edge of the table, where IDC clips: hue shifts up to
±93°, value ×0.25. Only colours outside sRGB reach them.

### Does Camera Raw take a four-colour profile?

The profile format, Adobe's reference code and Adobe's renderer all accept it. Lightroom
itself was not tested.

- **DNG specification 1.4** (bundled with the SDK):
  - ColorMatrix has Count "ColorPlanes * 3"; ForwardMatrix has Count "3 * ColorPlanes".
  - Chapter 6: "Let n be the dimensionality of the camera color space (usually 3 or 4)", with
    the pseudo-inverse used when n > 3.
- **DNG SDK 1.4** (`dng_camera_profile::IsValid(channels)`): requires an n×3 ColorMatrix and a
  3×n ForwardMatrix. The SDK's own reader (`ParseExtended`) loads both files; `IsValid(4)` = 1
  and `IsValid(3)` = 0, as expected.
- **`dng_validate`, the SDK's renderer:** it renders 4-plane DNGs with the profiles embedded.
  `dng_ref.py` matches it to 0.0015 RMS, 0.016 max, in 8-bit units, on five real shots in both
  modes (`dng_ref_sdk_check.py`).
- **Adobe's own F828 support:** its colour matrix is 4×3, which suggests ACR's internal profile for this camera has 4 planes. That matrix is the one dcraw and LibRaw copy.
- **Lightroom and Camera Raw:** not available here to test. They share the SDK's profile
  code, but that is an inference. If the profile is listed and the colours look right, it works.

## The converter's DNG export (v20)

Converter v20 embeds these profiles itself. In its export dialog choose DNG, then
**Look: Sony 'Real'** or **Sony 'Standard'**, or pick the preset *Lightroom: linear DNG with
the Sony look*. The converter looks for the two `.dcp` files:
- next to the converter;
- in a `profiles` or `lightroom-profile` folder beside it;
- inside the `.exe` when it is built with `f828_converter.spec`;
- in Adobe's `CameraProfiles` folder.

Such a DNG needs no sidecar. It carries:
- the whole profile;
- the white balance set in the converter, as AsShotNeutral;
- the shot's exposure offset, as BaselineExposure.

The tags are identical to `build_dcp_tags(cam, mode, neutral=neutral)`'s (checked on three
shots in both modes). With the IDC demosaic, the DNG also carries IDC's sharp luminance.

Rendering v20's linear DNGs (IDC demosaic, embedded look) with `dng_ref.py` gives 0.39 RMS
median and 0.85 worst against IDC's TIFFs, on the 11 labelled shots' smooth blocks.

`build_dcp_tags()` stays in `f828_dcp.py` for other scripts:

```python
f828_dcp.build_dcp_tags(cam, mode=None, dcp_dir=None, exposure=True, neutral=None)
    -> {tag: (tiff_type, value)}       # the format of the converter's serialise_ifd()
```

- `cam` is `sensor["camera"]` from `read_srf_camera()`.
- `mode` is `"real"` or `"standard"`; the default is the shot's own mode.
- `neutral` is an optional camera neutral, such as the converter's own white balance.

It returns every profile tag except UniqueCameraModel, plus AsShotNeutral and BaselineExposure.
BaselineExposure is stored to 0.01 EV, which is all the SDK keeps.

With UniqueCameraModel `"Sony DSC-F828"` (the converter's switch *Make Lightroom use the embedded
profile* off), Lightroom also offers the installed profiles. With the converter's own camera name
it offers only the embedded one.

`python3 f828_dcp.py tags FILE.SRF` prints what the function returns for a file.

## Brightness, process versions and BaselineExposure

- **Process versions.** The profiles were fitted and checked against the DNG SDK's rendering,
  which is the DNG 1.4 profile pipeline. With the Basic sliders at 0, Camera Raw's process
  versions 2012 and later are meant to leave a profile's look alone. Process versions 2003 and
  2010 add their own Brightness and Contrast defaults, so use 2012 or later. The sidecars write
  `11.0`; pass `--process-version` to choose another. This could not be checked without Camera Raw.
- **BaselineExposure of `.SRF` files.** For a DNG it is a tag, set by the converter. For a
  proprietary raw file, Camera Raw uses its own per-camera value, which Adobe does not publish,
  and the whole picture moves by that many stops.
  - To see it: convert one `.SRF` with Adobe DNG Converter and run `exiftool -BaselineExposure file.dng`.
  - 0 means the profile's brightness is right as it is.
  - If it is, say, +0.35, run `f828_xmp.py --acr-baseline-exposure 0.35`. The sidecar then subtracts it.
- **Checking a real Lightroom result.** Export a chart shot from Lightroom as a full-size,
  uncropped sRGB TIFF, then run `python3 dcp_validate.py --compare EXPORT.tif DSC00682-REAL-WB-DAY-IDC-SRGB.TIF`.
  It aligns the two framings and reports the same block statistics, including the mean error on
  grey blocks. A steady offset there points to BaselineExposure or process-version tone.

## Limits

- **Detail.** Lightroom's demosaic, sharpening and noise reduction are not IDC's; the
  smooth-block figures exclude detail. For an IDC-like start, reduce Lightroom's default sharpening.
- **Highlights.** Colours over white in a channel are handled differently (cause 1 above), and so
  are clipped channels. Lightroom's highlight recovery and the Highlights and Whites sliders
  change them further.
- **Gamut.** IDC clips to sRGB. The profiles reproduce that look through an sRGB export. With
  Adobe RGB or ProPhoto output, colours that IDC would clip keep more saturation.
- **White balance and exposure.** These are right only through the sidecar or the converter's DNG
  (see [White balance](#white-balance-read-this)).
- **Not tested in Lightroom itself.** Everything here is verified against the DNG SDK, which is
  Adobe's reference code. The process-version tone and the `.SRF` BaselineExposure are the
  remaining unknowns.

## Rebuilding the profiles

```
python3 f828_dcp.py build --converter path\to\f828_converter.py  path\to\SRF-folder [more folders]
```

This needs the converter's PART 1 together with rawpy, numpy and scipy. It reads the camera
registers and raw data, fits both modes and writes both `.dcp` files next to `f828_dcp.py`
(or to `--out`). It takes about 2½ minutes and gives the same result each time.

The chart patch positions it uses are those of the five chart shots, DSC00681–DSC00686.
Other files contribute pixels and surface colours only.

`python3 f828_dcp.py info *.dcp` prints a profile's contents.
