@echo off
REM ============================================================
REM  Sound Safari launcher - what the kid's desktop icon runs.
REM  Starts the full pipeline: slice new recordings, open the
REM  review app in the browser, then publish when they're done.
REM
REM  The kid never sees a command prompt beyond this friendly
REM  window. They review in the browser; closing it / pressing a
REM  key here finishes the publish step.
REM ============================================================

setlocal
cd /d "%~dp0\.."

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
  echo.
  echo  Sound Safari isn't set up on this PC yet.
  echo  Ask a grown-up to run deploy\setup-kid-pc.ps1
  echo.
  pause
  exit /b 1
)

title Sound Safari
echo.
echo   Starting Sound Safari...
echo   Your browser will open in a moment. Have fun sorting sounds!
echo.
echo   When you're all done, come back to this window
echo   and press any key to finish.
echo.

REM Full run: slices the Drive inbox, opens the review app, publishes on Ctrl+C.
REM Uses the config the doctor wrote at setup so it picks the right device.
"%VENV_PY%" chopshop.py --config "%CD%\chopshop.json"

echo.
echo   All finished! Your sounds are saved and ready in Ableton.
echo.
pause
endlocal
