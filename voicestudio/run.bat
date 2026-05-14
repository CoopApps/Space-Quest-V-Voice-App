@echo off
REM ---------------------------------------------------------------------------
REM SQ5 Voice Studio launcher.
REM
REM On first run, installs every Python package listed in requirements.txt
REM (PyQt6, numpy, sounddevice, soundfile, scipy, pydub).  After that, just
REM launches the app.  Re-run to pick up new dependency versions.
REM
REM ffmpeg is needed ONLY for MP3 / M4A / AAC import.  WAV, FLAC, OGG, AIFF,
REM and AU all work out of the box via soundfile.  See README for ffmpeg
REM install instructions if you need MP3 support.
REM ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0"

REM Prefer the py launcher; fall back to python.
where py >nul 2>&1
if %errorlevel%==0 (
    set PYCMD=py -3
) else (
    set PYCMD=python
)

echo Checking dependencies...
%PYCMD% -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install dependencies.  Make sure Python 3.10+ and pip
    echo are installed and on your PATH, then run this script again.
    pause
    exit /b 1
)

echo Launching SQ5 Voice Studio...
cd ..
%PYCMD% -m voicestudio.main
endlocal
