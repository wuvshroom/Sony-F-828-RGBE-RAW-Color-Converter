# Third-party notices

This project's own code is under the PolyForm Noncommercial License 1.0.0 (see [LICENSE](LICENSE)). It uses the libraries below, each under its own license. The release `.exe` bundles the ones marked **bundled**.

| Component | License | In the .exe |
|---|---|---|
| [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/) and Qt 6 | LGPL-3.0 | bundled |
| [NumPy](https://numpy.org) | BSD-3-Clause | bundled |
| [SciPy](https://scipy.org) | BSD-3-Clause | bundled |
| [rawpy](https://github.com/letmaik/rawpy) | MIT | bundled |
| [LibRaw](https://www.libraw.org) (inside rawpy) | LGPL-2.1 or CDDL-1.0 | bundled |
| [tifffile](https://github.com/cgohlke/tifffile) | BSD-3-Clause | bundled |
| [Pillow](https://python-pillow.org) | MIT-CMU (HPND) | bundled |
| [imagecodecs](https://github.com/cgohlke/imagecodecs) | BSD-3-Clause, plus the licenses of its bundled codec libraries | bundled when installed at build time |
| [OpenEXR](https://openexr.com) | BSD-3-Clause | bundled when installed at build time |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) | BSD-3-Clause source; **GPL-2.0 binary wheels** (x265) | **never bundled**; optional when running from source |

## LGPL components (Qt / PySide6, LibRaw)

These libraries are used unmodified. Their source code is available from the links above. Because the complete source of this application and its build script are public, you can rebuild the `.exe` against your own or a modified version of PySide6, Qt or LibRaw: install it into the build environment and run `build.bat`. The license texts come with each package (`pip show -f PySide6-Essentials`, `pip show -f rawpy`).

## Adobe DNG SDK

`lightroom-profile/dng_ref.py` is a numpy re-implementation of the profile rendering steps of Adobe's DNG SDK 1.4, used to fit and check the camera profiles. It includes one table taken from the SDK, the default ACR3 tone curve (`dng_tone_curve_acr3_default`). The DNG SDK is published by Adobe under a license that allows use, modification and redistribution; that table is Copyright © 2006-2012 Adobe Systems Incorporated. "Adobe", "Lightroom" and "Camera Raw" are trademarks of Adobe Inc.

## Sony

This project contains no Sony software. The `.SRF` decoding and colour processing are independent re-implementations written for interoperability with files from the user's own camera. "Sony", "DSC-F828" and "Image Data Converter" are trademarks of Sony Group Corporation.
