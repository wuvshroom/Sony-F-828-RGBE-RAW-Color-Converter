# Changelog

## v21.0.0 (2026-11-16)

A new interface. The processing and the output are unchanged from v20.1.

- **The picture fills the window.** Clear glass panels float over it: the library on the left, Develop on the right, the tools along the top. The glass bends the picture at its rim like a drop of water and lays a light smoke tint over the middle, with no border. Tab hides the library, Shift+Tab the Develop panel.
- **Appearance menu** (click "F828 Converter"): accent colour, panel and toolbar glass, card shade, blur, rim strength, the ambient light behind the picture, the filmstrip and motion. A standard Windows title bar is one switch away.
- **Everything moves on springs.** Switches, buttons, section cards, menus, panels and the scope bounce into place, and a slider's knob is a droplet that stretches as you drag it and settles when you let go. The Appearance menu or Windows' own animation setting turns motion off.
- **New starting settings:** Sony Real colour, As shot white balance, the edge-aware demosaic, the camera's black level, and everything else off or neutral (highlight roll-off off). For exports that match IDC's own files exactly, choose the IDC demosaic under Sensor (or save it as your default). The "..." button in Develop saves your current settings as the starting settings for new images, or goes back to the built-in ones. Crop and straighten are never saved.
- **The preview opens at 100 %** with "draft while adjusting" on.
- **A movable scope.** The histogram floats over the picture: drag it anywhere and it springs into the nearest corner. Right-click it (or select it and press Enter) for its size, channels, linear or log scale, resolution and clipping markers, or switch it to a waveform, RGB parade or vectorscope. H shows and hides it.
- **Develop sections** fold open and shut, show a summary when folded, and each has its own on / off switch (to compare without losing the values) and reset.
- **Undo and redo** per image (Ctrl+Z, Ctrl+Y or Ctrl+Shift+Z), and **Before** (\\) shows the starting settings with your crop.
- **Filmstrip:** with the library hidden, thumbnails run along the bottom, with sync and export for ticked images.
- Export and sync open as sheets over the window. Export progress and results appear in a small card at the bottom with Cancel, Open folder and Details.

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
