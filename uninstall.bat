@echo off
setlocal
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -m filewizard uninstall
%PY% -m pip uninstall -y filewizard
pause
