@echo off
setlocal EnableExtensions
title LYGO LLM Console - public install
cd /d "%~dp0"
rem -------------------------------------------------------------------------
rem  Drive-portable: everything here is relative to this file (%~dp0), so the kit can be
rem  copied anywhere and installed from there. Ports are NOT set here - the console reads
rem  them from config\console.json, overridable with the exported LYGO_*_PORT names that
rem  LYGO_LLM_CONSOLE.bat documents at the top of its own header (LYGO_CONSOLE_PORT,
rem  LYGO_LLAMA_PORT, LYGO_EMBED_PORT, LYGO_COLIBRI_PORT).
rem -------------------------------------------------------------------------
echo.
echo  LYGO LLM Console - public install
echo  This folder: %CD%
echo  Seeds Soul / Identity / Memory for YOU. Does not copy steward vaults.
echo.
where py >nul 2>&1 && (set "LYGO_PYTHON=py -3") || (set "LYGO_PYTHON=python")
%LYGO_PYTHON% -u "%~dp0src\install.py" %*
if errorlevel 1 (
  echo.
  echo  Install needs Python 3. https://www.python.org/downloads/
  pause
  exit /b 1
)
echo.
echo  Optional engine download (ggml-org llama-server CPU zip):
choice /C YN /M "Fetch llama-server into engine now"
if errorlevel 2 goto done
if errorlevel 1 %LYGO_PYTHON% -u "%~dp0src\install.py" --fetch-engine
:done
echo.
echo  Start the console with LYGO_LLM_CONSOLE.bat
pause
