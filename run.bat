@echo off
setlocal
cd /d "%~dp0"
set "SCRIPT=%~dp0organize_photos.py"

echo ============================================================
echo   Photo Date Organizer  (Python 3.8+ required)
echo   Drag a folder onto this .bat, or copy it into your photo
echo   folder and double-click to organize that folder.
echo ============================================================
echo.

where py >nul 2>nul
if not errorlevel 1 goto RUN_PY
where python >nul 2>nul
if not errorlevel 1 goto RUN_PYTHON

echo [ERROR] Python 3.8+ was not found on this computer.
echo Install it from https://www.python.org/downloads/
echo and remember to tick "Add python.exe to PATH" during setup.
goto DONE

:RUN_PY
if "%~1"=="" (py -3 "%SCRIPT%") else (py -3 "%SCRIPT%" "%~1")
goto DONE

:RUN_PYTHON
if "%~1"=="" (python "%SCRIPT%") else (python "%SCRIPT%" "%~1")
goto DONE

:DONE
echo.
echo Press any key to close this window.
pause >nul
endlocal
