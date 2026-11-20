@echo off
setlocal
cd /d "%~dp0"
mode con cols=120 lines=60 >nul 2>nul
echo.
echo ============================================================
echo   Photo Date Organizer - Demo and Self-Test
echo   Zero third-party packages. Safe: only touches _demo folder.
echo ============================================================
echo.
where py >nul 2>nul
if errorlevel 1 goto USE_PYTHON
py -3 examples\run_demo.py
goto DONE
:USE_PYTHON
python examples\run_demo.py
:DONE
set "RC=%ERRORLEVEL%"
echo.
echo ============================================================
echo   Demo finished. Exit code: %RC%
echo   Screenshot any STAGE you like before closing.
echo ============================================================
echo.
echo Press any key to close this window.
pause >nul
exit /b %RC%
