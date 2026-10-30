@echo off
rem ===========================================================================
rem  LYGO LLM Console - STOP. Port-scoped and drive-portable.
rem  Ports are resolved exactly like LYGO_LLM_CONSOLE.bat does it (same names, same order):
rem     LYGO_CONSOLE_PORT   console     config key: port
rem     LYGO_LLAMA_PORT     chat engine config key: llama_port
rem     LYGO_EMBED_PORT     embed       config key: embed_port
rem     LYGO_COLIBRI_PORT   colibri     config key: colibri_port
rem  Precedence: exported LYGO_*  >  config\local.json  >  config\console.json  >  app default.
rem
rem  Nothing is killed by name or by "whatever owns the port". The owner PID is resolved and
rem  its image name checked: python.exe / pythonw.exe / llama-server.exe is ours, anything
rem  else is reported and left running.
rem  NetTCPIP is not required: if Get-NetTCPConnection returns nothing (stripped, restricted
rem  or module-blocked Windows) the same sweep falls back to
rem  netstat -ano | findstr ":<port>" | findstr LISTENING, and tasklist does the owner check.
rem  The ports are VERIFIED free before this script exits; if one is still held it says so and
rem  exits non-zero instead of pretending the stop worked.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
title LYGO LLM Console - STOP
set "ROOT=%~dp0"
if defined LYGO_CONSOLE_ROOT set "ROOT=%LYGO_CONSOLE_ROOT%"
if not "%ROOT:~-1%"=="\" set "ROOT=%ROOT%\"
cd /d "%ROOT%"
set "LYGO_KIT_ROOT=%ROOT%"
rem --- resolve the port set ONCE, through the kit's own reader (tools\resolve_ports.py), so
rem --- the launcher, the server and this sweep can never disagree about which ports this copy
rem --- owns. Precedence: exported LYGO_* > config\local.json > config\console.json > default.
set "LYGO_P_console="
set "LYGO_P_llama="
set "LYGO_P_embed="
set "LYGO_P_colibri="
set "LYGO_P_bind="
set "LYGO_PORTS="
if not defined LYGO_PYTHON (
  if exist "C:\Python313\python.exe" (set "LYGO_PYTHON=C:\Python313\python.exe") else (
    where py >nul 2>&1 && (set "LYGO_PYTHON=py") || (set "LYGO_PYTHON=python")
  )
)
for /f "usebackq tokens=1,* delims==" %%A in (`%LYGO_PYTHON% -u "%ROOT%tools\resolve_ports.py" 2^>nul`) do if not "%%A"=="" set "LYGO_P_%%A=%%B"

rem --- stop the KEEPER first. It is a supervisor whose whole job is to bring the console back, so a
rem     stop that leaves it running is a stop that undoes itself a few sweeps later. It answers whether
rem     it actually went instead of assuming.
"%LYGO_PYTHON%" -u "%ROOT%tools\keeper.py" --stop
if not defined LYGO_P_console echo  [warn] port set could not be resolved - try: "%LYGO_PYTHON%" "%ROOT%tools\resolve_ports.py"
set "LYGO_CONSOLE_PORT=%LYGO_P_console%"
set "LYGO_LLAMA_PORT=%LYGO_P_llama%"
set "LYGO_EMBED_PORT=%LYGO_P_embed%"
set "LYGO_COLIBRI_PORT=%LYGO_P_colibri%"
rem The port list is SPACE separated (the sweep loops over it) and the comma form feeds
rem PowerShell - the old `%LYGO_PORTS: =%` stripped the separators and fused the four ports
rem into one number, so every sweep checked a token that could never match and reported free.
set "LYGO_PORTS=%LYGO_P_console% %LYGO_P_llama% %LYGO_P_embed% %LYGO_P_colibri%"
for /f "tokens=1-8" %%A in ("%LYGO_PORTS%") do set "LYGO_PORTS=%%A %%B %%C %%D"
set "LYGO_PSPORTS=%LYGO_PORTS: =,%"
set "LYGO_PORTSH=console %LYGO_P_console%"
if not defined LYGO_P_console set "LYGO_PORTSH=console n/a"
if defined LYGO_P_llama (set "LYGO_PORTSH=%LYGO_PORTSH% / chat %LYGO_P_llama%") else (set "LYGO_PORTSH=%LYGO_PORTSH% / chat n/a")
if defined LYGO_P_embed (set "LYGO_PORTSH=%LYGO_PORTSH% / embed %LYGO_P_embed%") else (set "LYGO_PORTSH=%LYGO_PORTSH% / embed n/a")
if defined LYGO_P_colibri (set "LYGO_PORTSH=%LYGO_PORTSH% / colibri %LYGO_P_colibri%") else (set "LYGO_PORTSH=%LYGO_PORTSH% / colibri n/a")
set "LYGO_URLHOST=%LYGO_P_bind%"
if not defined LYGO_URLHOST set "LYGO_URLHOST=127.0.0.1"
set "LYGO_PORTARG="
if defined LYGO_P_console set "LYGO_PORTARG=--port %LYGO_P_console%"

echo  LYGO LLM Console - STOP
echo  Kit root   : %ROOT%
echo  Ports      : %LYGO_PORTSH%
echo  Env names  : LYGO_CONSOLE_PORT  LYGO_LLAMA_PORT  LYGO_EMBED_PORT  LYGO_COLIBRI_PORT
if defined LYGO_P_noteconsole echo  [note] %LYGO_P_noteconsole%
if defined LYGO_P_notellama echo  [note] %LYGO_P_notellama%
if defined LYGO_P_noteembed echo  [note] %LYGO_P_noteembed%
if defined LYGO_P_notecolibri echo  [note] %LYGO_P_notecolibri%
echo.
echo  Checking ports %LYGO_PORTS% ...
call :lygo_map
echo  Port probe : %LYGO_PROBE%   (netstat = NetTCPIP was unavailable, batch fallback in use)
if defined LYGO_PORTS (
  for %%P in (%LYGO_PORTS%) do call :lygo_sweep %%P
) else (
  echo  [note] no port was resolved - nothing to stop. Check config\console.json and the LYGO_*_PORT names.
)
ping -n 3 127.0.0.1 >nul
call :lygo_map
if defined LYGO_PORTS (
  for %%P in (%LYGO_PORTS%) do call :lygo_sweep %%P
)
ping -n 2 127.0.0.1 >nul
echo.
echo  Verifying ports %LYGO_PORTS% ...
set "LYGO_HELD="
set "LYGO_FOREIGN="
call :lygo_map
if defined LYGO_PORTS (
  for %%P in (%LYGO_PORTS%) do call :lygo_verify %%P
) else (
  echo  [note] ports not resolved - cannot verify. Set LYGO_CONSOLE_PORT and the other
  echo         LYGO_*_PORT names, or fix config\console.json, then run this again.
  exit /b 5
)
if defined LYGO_HELD (
  echo.
  echo  [FAIL] our own process still holds a port listed above - the console is NOT fully down.
  if defined LYGO_DENIED (
    echo  [denied] it was started from an ELEVATED window, so this normal one is not allowed
    echo           to kill it. Retrying as Administrator will take it down.
    choice /c YN /n /m "  Relaunch this STOP as administrator now? [Y/N] "
    if not errorlevel 2 (
      echo  asking Windows for elevation...
      powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
      exit /b 3
    )
  )
  echo         Retry, or from an Administrator window: taskkill /F /T /PID ^<pid^>
  exit /b 3
)
if defined LYGO_FOREIGN (
  echo.
  echo  [FAIL] a port above is held by a process that is not ours - it was left running.
  echo         Give this copy its own triplet ^(LYGO_*_PORT^) or free the port yourself.
  exit /b 4
)
echo.
echo  STOP: all ports free - nothing of this kit is running.
exit /b 0
rem ================================ helpers ===================================
rem The port:pid map is built ONCE per pass into LYGO_MAP ("<port>:<pid> ...") because a
rem PowerShell pipeline nested inside a for-loop makes cmd abort the shell. Ports are then
rem looked up in that map with string operations only; PowerShell is touched twice per pass
rem at most, and the owner check uses tasklist, which is always present.
exit /b 0

:lygo_map
set "LYGO_MAP="
set "LYGO_PROBE=nettcp"
for /f "usebackq tokens=*" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $p=@(%LYGO_PSPORTS%); $o=@(); try{ $t=Get-NetTCPConnection -State Listen -EA Stop }catch{ $t=$null }; if($t){ foreach($x in $t){ if($p -contains $x.LocalPort){ $o+=([string]$x.LocalPort + ':' + [string]$x.OwningProcess) } } }; if($o.Count -gt 0){ $o -join ' ' }"`) do set "LYGO_MAP=%%L"
if not defined LYGO_MAP goto lygo_map_netstat
exit /b 0

:lygo_map_netstat
set "LYGO_PROBE=netstat"
for %%P in (%LYGO_PORTS%) do call :lygo_netstat_one %%P
exit /b 0

:lygo_netstat_one
rem %1 = port. Fallback for stripped/restricted Windows or a blocked NetTCPIP module:
rem netstat -ano | findstr ":<port>" | findstr LISTENING, PID is the 5th column.
rem Two-stage findstr on purpose: a single /C:"<port> .*LISTENING" pattern NEVER matches -
rem findstr splits it at the space, treats ".*LISTENING" as a file to read and exits 1, which
rem is how an old console plus its engine survived a sweep and two servers shared one port.
set "LYGO_NSPID="
for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%~1" ^| findstr "LISTENING"') do set "LYGO_NSPID=%%A"
if defined LYGO_NSPID set "LYGO_MAP=%LYGO_MAP% %~1:%LYGO_NSPID%"
exit /b 0

:lygo_pidof
rem %1 = port -> LYGO_PID (empty when the port is free). String lookup, no PowerShell.
set "LYGO_PID="
for %%M in (%LYGO_MAP%) do for /f "tokens=1,2 delims=:" %%X in ("%%M") do if "%%X"=="%~1" set "LYGO_PID=%%Y"
exit /b 0

:lygo_owner
rem %1 = PID -> LYGO_IMGRAW as tasklist reports it, LYGO_OURS=1 only for python.exe /
rem pythonw.exe / llama-server.exe. tasklist is always available, so this works even when
rem PowerShell or NetTCPIP is not.
set "LYGO_IMGRAW="
set "LYGO_OURS="
if "%~1"=="" exit /b 0
for /f "tokens=1,2 delims=," %%I in ('tasklist /FI "PID eq %~1" /FO CSV /NH') do set "LYGO_IMGRAW=%%~I"
rem tasklist answers an unknown PID with "INFO: No tasks are running ...". Match on INFO,
rem NOT on "No": `if /i` is case-insensitive, so a process called node.exe matched the old
rem test, was wiped to empty and got reported as "already gone" while it still held the port.
if /i "%LYGO_IMGRAW:~0,4%"=="INFO" set "LYGO_IMGRAW="
if /i "%LYGO_IMGRAW%"=="python.exe" set "LYGO_OURS=1"
if /i "%LYGO_IMGRAW%"=="pythonw.exe" set "LYGO_OURS=1"
if /i "%LYGO_IMGRAW%"=="llama-server.exe" set "LYGO_OURS=1"
exit /b 0

:lygo_try
rem %1 = port, %2 = PID. Kill ONLY our own process. A stranger holding the port is reported
rem and left running - never force-killed.
call :lygo_owner %2
if not defined LYGO_IMGRAW (
  echo  port %~1: PID %~2 is already gone
  exit /b 0
)
if not defined LYGO_OURS (
  echo.
  echo  [refused] port %~1 belongs to %LYGO_IMGRAW% PID %~2 - not one of ours, NOT killed.
  echo            Give this copy its own port triplet ^(LYGO_*_PORT^) or free the port yourself.
  echo.
  set "LYGO_FOREIGN=1"
  exit /b 0
)
echo  port %~1: stopping our %LYGO_IMGRAW% PID %~2
call :lygo_kill %~2 %~1
exit /b 0

:lygo_kill
rem %1 = PID, %2 = the port it holds. Kill ONLY our own process, and SAY what the kill answered.
rem A blanket ">nul 2>&1" is how a console looked stuck: when the holder had been started from an
rem ELEVATED window, taskkill is refused with "Access is denied", the text was swallowed, and the
rem line above still said "stopping" as though it had worked. So: print the real answer, retry,
rem escalate (Stop-Process, then a WMI terminate), and if nothing can take it, say why and what
rem fixes it instead of looping until the operator gives up.
set "LYGO_DENIED="
call :lygo_kill_once %~1
ping -n 2 127.0.0.1 >nul
call :lygo_pidof %~2
if not defined LYGO_PID exit /b 0
echo    ^| still held - second attempt
call :lygo_kill_once %~1
ping -n 3 127.0.0.1 >nul
call :lygo_pidof %~2
if not defined LYGO_PID exit /b 0
echo    ^| still held - escalating: Stop-Process, then WMI terminate
powershell -NoProfile -ExecutionPolicy Bypass -Command "Stop-Process -Id %~1 -Force -ErrorAction SilentlyContinue" >nul 2>&1
ping -n 3 127.0.0.1 >nul
call :lygo_pidof %~2
if not defined LYGO_PID exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-CimInstance Win32_Process -Filter 'ProcessId=%~1' -ErrorAction SilentlyContinue).Terminate() | Out-Null" >nul 2>&1
ping -n 3 127.0.0.1 >nul
call :lygo_pidof %~2
if not defined LYGO_PID exit /b 0
set "LYGO_DENIED=1"
exit /b 0

:lygo_kill_once
rem %1 = PID. Run taskkill and echo exactly what it said.
set "LYGO_KILLMSG="
for /f "delims=" %%K in ('taskkill /F /T /PID %~1 2^>^&1') do set "LYGO_KILLMSG=%%K"
rem No ( ) block around these two lines: taskkill's own SUCCESS line reads "... with PID 1234
rem (child process of PID 5678) has been terminated." and a ")" inside an expanded value ends a
rem parenthesised block early - this helper killed the whole batch (exit 255) the first time it ran.
if not defined LYGO_KILLMSG goto :lygo_kill_silent
echo    ^| %LYGO_KILLMSG%
exit /b 0
:lygo_kill_silent
echo    ^| taskkill said nothing
exit /b 0

:lygo_sweep
rem %1 = port
call :lygo_pidof %~1
if not defined LYGO_PID (
  echo  port %~1: free
  exit /b 0
)
call :lygo_try %~1 %LYGO_PID%
exit /b 0

:lygo_guard
rem %1 = port. After the sweep: refuse to start when anything still holds it.
call :lygo_pidof %~1
if not defined LYGO_PID (
  echo  port %~1: free
  exit /b 0
)
call :lygo_owner %LYGO_PID%
if not defined LYGO_OURS (
  echo  [warn] port %~1 is held by %LYGO_IMGRAW% PID %LYGO_PID% - not ours, left alone.
  set "LYGO_FOREIGN=1"
  exit /b 0
)
echo  [warn] port %~1 is STILL held by our %LYGO_IMGRAW% PID %LYGO_PID% - refusing to start a second console.
set "LYGO_HELD=1"
exit /b 0

:lygo_verify
rem %1 = port. STOP's proof: never assume a kill worked.
call :lygo_pidof %~1
if not defined LYGO_PID (
  echo  port %~1: free
  exit /b 0
)
call :lygo_owner %LYGO_PID%
if defined LYGO_OURS (
  echo  port %~1: STILL HELD by our %LYGO_IMGRAW% PID %LYGO_PID%
  set "LYGO_HELD=1"
  exit /b 0
)
echo  port %~1: held by %LYGO_IMGRAW% PID %LYGO_PID% - NOT ours, left alone
set "LYGO_FOREIGN=1"
exit /b 0
