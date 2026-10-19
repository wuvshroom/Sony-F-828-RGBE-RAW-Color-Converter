# Changelog

## v20.1.0 (2026-10-19)

Faster, with exactly the same output.

- **About 4x faster full-size exports with Sony colour** (4.8 s to 1.2 s for an F828-sized frame on a 4-core machine). IDC's colour processor, its 16-bit output step and its demosaic are compiled with Numba. The numpy versions stay as the reference: every export and preview variant tested is identical to v20 byte for byte.
- **Faster opening and previews.** IDC's demosaic is about 3x faster, and the preview's downsizing, black subtraction and curve encoding now run on every core.
- The first launch compiles the fast paths in the background (about 15 s) and keeps them on disk. Without Numba (running from source) the app works as before, at v20's speed.

## v20.0.0 (2026-10-18)

First public release. Versions 1 to 19 were private.

- **IDC's own demosaic**, recreated and now the default. In the default rendering it gives about 40 % less colour noise in the shadows than the edge-aware demosaic and no colour fringes on fine lines.
- **Sony colour in IDC's own arithmetic.** Full-size exports and their export preview run IDC's colour processor bit for bit: integer steps, 8-bit luma and chroma, 4:2:2 chroma, the luma noise filter and the 16-bit output step. They match IDC SR's own TIFFs to 0.64/255 RMS over every pixel of 11 test shots and 0.10/255 on flat colour. The live preview keeps a faster floating-point version (0.2/255 on flat colour).
- IDC re-maps four of the colour processor's thresholds on every render; the converter now does too, which gives deeper shadow colour. The output factor is exactly 255.5/257.
- **IDC's white-balance presets** with Sony colour: daylight, cloudy, shade, tungsten, fluorescent and flash.
- **Edge-aware demosaic fixed.** It had fallen back to bilinear on every F828 file. It is also 2.5x faster.
- **Export preview:** shows the picture exactly as the export file will hold it (format, quality or target size, bit depth, colour space, resize, output sharpening), with its file size and what compression changed.
- **New export dialog** with presets, JPEG XL, JPEG 2000, OpenEXR and BMP alongside TIFF, DNG, PNG, JPEG, WebP and AVIF, resizing, output sharpening, file-size targets and metadata choices.
- **Lightroom / Camera Raw camera profiles** fitted to IDC's Real and Standard. A DNG can embed one with the shot's exposure, so Lightroom renders it as IDC does (within about 0.4/255 on smooth areas in Adobe's DNG SDK renderer).
- **Lens and cleanup tools:** lateral chromatic aberration correction, purple and green fringe removal, colour noise reduction, hot and dead pixel removal. The slow tools use every core.
- **License and packaging:** released under PolyForm Noncommercial 1.0.0. The interface moved from PyQt6 (GPL) to PySide6 (LGPL) so it can carry that license. The release .exe leaves out HEIF export, because the HEIF library's binaries are GPL.
