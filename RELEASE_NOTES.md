First public release of the Sony F-828 RGBE RAW Color Converter: a Windows app that turns Sony DSC-F828 `.SRF` raw files into TIFF, DNG and other formats with Sony's own colour.

## Download

- **f828_converter.exe**: Windows 10 and 11, no install needed. The .exe is unsigned, so SmartScreen may warn: click More info, then Run anyway.
- **Sony DSC-F828 IDC Real.dcp** and **Sony DSC-F828 IDC Standard.dcp**: camera profiles for Lightroom and Camera Raw. Copy them into `%APPDATA%\Adobe\CameraRaw\CameraProfiles`.
- **f828-lightroom-profiles-v20.0.0.zip**: both profiles plus `f828_xmp.py` and its README, for setting each shot's real as-shot white balance in Lightroom.

## Highlights

- Sony 'Real' and 'Standard' colour reproduced bit-exact from Image Data Converter SR: 0.64/255 RMS over every pixel of 11 test shots.
- IDC's own four-colour (RGBE) demosaic as the default.
- Export to TIFF, DNG, PNG, JPEG, WebP, AVIF, JPEG XL, JPEG 2000, OpenEXR and BMP, with an export preview that shows the exact result.
- Lightroom camera profiles fitted to IDC's Real and Standard.

The full list is in [CHANGELOG.md](https://github.com/wuvshroom/Sony-F-828-RGBE-RAW-Color-Converter/blob/main/CHANGELOG.md).

## Known issues

- HEIF export is not in the .exe (the HEIF library's binaries are GPL). It works when running from source with `pillow-heif` installed.
- The profiles are verified in Adobe's DNG SDK renderer, not yet inside Lightroom itself.

## License

Free for noncommercial use under PolyForm Noncommercial 1.0.0. Selling it, a modified version, or paid support is not permitted. Not affiliated with Sony.
