@echo off
rem FileWizard installer: installs the Python package and adds the right-click menu.
rem No admin rights needed; everything goes into your user account.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup, then run this again.
    pause
    exit /b 1
)

echo Installing FileWizard and its libraries (first time takes a minute)...
%PY% -m pip install --upgrade --quiet . || goto :fail
%PY% -m filewizard install || goto :fail
echo.
%PY% -m filewizard doctor
echo.
echo Done. Right-click a PDF, image, or Office file and look for "FileWizard".
echo Windows 11: it's under "Show more options" (or Shift + right-click).
pause
exit /b 0

:fail
echo.
echo Installation failed - see the messages above.
pause
exit /b 1
