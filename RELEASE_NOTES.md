Version 21 of the Sony F-828 RGBE RAW Color Converter: a new interface, with the same colour and output as v20.1.

## Download

- **f828_converter.exe**: Windows 10 and 11, no install needed. The .exe is unsigned, so SmartScreen may warn: click More info, then Run anyway.
- **Sony DSC-F828 IDC Real.dcp** and **Sony DSC-F828 IDC Standard.dcp**: camera profiles for Lightroom and Camera Raw. Copy them into `%APPDATA%\Adobe\CameraRaw\CameraProfiles`.
- **f828-lightroom-profiles-v21.0.0.zip**: both profiles plus `f828_xmp.py` and its README, for setting each shot's real as-shot white balance in Lightroom.

## What's new

- The picture fills the window, with clear glass panels floating over it. The Appearance menu (click "F828 Converter") sets the accent colour, glass, blur and motion.
- Every control moves on springs, and slider knobs stretch like a droplet as you drag. Motion follows Windows' animation setting and can be turned off.
- New starting settings: Sony Real colour, As shot white balance, edge-aware demosaic, the camera's black level, everything else off. Save your own with the "..." button in Develop.
- The preview opens at 100 % with "draft while adjusting" on.
- A movable scope: drag the histogram to any corner, right-click for its options, or switch it to a waveform, RGB parade or vectorscope.
- Each Develop section has an on / off switch and reset, plus undo / redo per image and a Before view.

The full list is in [CHANGELOG.md](https://github.com/wuvshroom/Sony-F-828-RGBE-RAW-Color-Converter/blob/main/CHANGELOG.md).

## Known issues

- HEIF export is not in the .exe (the HEIF library's binaries are GPL). It works when running from source with `pillow-heif` installed.
- The profiles are verified in Adobe's DNG SDK renderer, not yet inside Lightroom itself.

## License

Free for noncommercial use under PolyForm Noncommercial 1.0.0. Selling it, a modified version, or paid support is not permitted. Not affiliated with Sony.
