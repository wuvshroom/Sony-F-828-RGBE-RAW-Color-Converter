@echo off
rem Builds dist\f828_converter.exe in a clean virtual environment.
rem Run from this folder:  build.bat
setlocal
if not exist .venv (
    py -3 -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
pip install -r requirements-build.txt || goto :error
rem pillow-heif is GPL and must not be bundled; the spec excludes it, but keep it out of the venv too.
pip uninstall -y pillow-heif >nul 2>&1
pyinstaller --noconfirm --clean f828_converter.spec || goto :error
rem Release asset: the Lightroom profiles with the sidecar tool.
powershell -NoProfile -Command "Compress-Archive -Force -Path 'lightroom-profile\*.dcp','lightroom-profile\f828_xmp.py','lightroom-profile\f828_dcp.py','lightroom-profile\dng_ref.py','lightroom-profile\README.md','LICENSE' -DestinationPath 'dist\f828-lightroom-profiles-v20.1.0.zip'" || goto :error
echo.
echo Built dist\f828_converter.exe
echo Built dist\f828-lightroom-profiles-v20.1.0.zip
certutil -hashfile dist\f828_converter.exe SHA256
exit /b 0
:error
echo Build failed.
exit /b 1
