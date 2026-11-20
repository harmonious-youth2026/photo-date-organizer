@echo off
setlocal
cd /d "%~dp0"
mode con cols=120 lines=60 >nul 2>nul
echo.
echo ============================================================
echo   Photo Date Organizer - Verify with REAL screenshot folder
echo   Source folder is READ-ONLY. Only the _demo copy is touched.
echo   Screenshot any STAGE before pressing Enter.
echo ============================================================
echo.
where py >nul 2>nul
if errorlevel 1 goto USE_PYTHON
py -3 examples\verify_real_folder.py %*
goto DONE
:USE_PYTHON
python examples\verify_real_folder.py %*
:DONE
set "RC=%ERRORLEVEL%"
echo.
echo ============================================================
echo   Verification finished. Exit code: %RC%
echo   0 = all checks passed. See the STAGE 5 summary above.
echo ============================================================
echo.
echo Press any key to close this window.
pause >nul
exit /b %RC%
