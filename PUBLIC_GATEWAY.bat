@echo off
echo [SYSTEM] WEB PORTAL (API ONLY) - one console, three systems: USB LOCAL / PC LOCAL / WEB PORTAL (API ONLY)
echo [SYSTEM] role    : online API-only agent portal - already built, anyone can use it from a web page
rem ===========================================================================
rem  PUBLIC GATEWAY launcher - drive-portable and config-driven. Nothing is pinned here:
rem     port     LYGO_GATEWAY_PORT     config key: gateway_port     app default: src\public_gateway.py
rem     bind     LYGO_GATEWAY_BIND     config key: gateway_bind     app default: 127.0.0.1 (loopback)
rem     backend  LYGO_GATEWAY_BACKEND  config key: gateway_backend  app default: local
rem     model    LYGO_GATEWAY_MODEL    config key: gateway_model    app default: LYGO_PUBLIC_MODEL
rem  Precedence: exported LYGO_*  >  config\local.json  >  config\console.json  >  app default.
rem
rem  NETWORK EXPOSURE IS A DECISION, NOT A SIDE EFFECT. The bind that was resolved is printed
rem  before anything starts, and the launcher says PLAINLY whether that is this PC only or
rem  public. Only a wildcard value (lan / public / any / 0.0.0.0 / *) is passed as
rem  --lan --i-consent, which is what public_gateway.py requires for a non-loopback bind -
rem  its own default is loopback, so an unconfigured gateway can never silently go public.
rem
rem  Backend model: backend local (the default) talks to the kit's OWN engine on LYGO_LLAMA_PORT,
rem  so boot the console's engine first. backend ollama is a legacy opt-in for a machine that
rem  still runs a daemon; nothing in this kit requires, calls, or needs one.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
title LYGO Public Gateway
set "ROOT=%~dp0"
if defined LYGO_CONSOLE_ROOT set "ROOT=%LYGO_CONSOLE_ROOT%"
if not "%ROOT:~-1%"=="\" set "ROOT=%ROOT%\"
cd /d "%ROOT%"
set "LYGO_KIT_ROOT=%ROOT%"
set "LYGO_RESOLVE_GATEWAY=1"
PORTSBLOCK
if not defined LYGO_P_gwbackend set "LYGO_P_gwbackend=local"
echo  Kit root   : %ROOT%
echo  Env names  : LYGO_GATEWAY_PORT  LYGO_GATEWAY_BIND  LYGO_GATEWAY_BACKEND  LYGO_GATEWAY_MODEL
if defined LYGO_P_gwport (echo  Gateway    : port %LYGO_P_gwport%   backend %LYGO_P_gwbackend%   model %LYGO_P_gwmodel%) else (echo  Gateway    : port n/a ^(app default^)   backend %LYGO_P_gwbackend%   model %LYGO_P_gwmodel%)
if defined LYGO_P_gwlan (
  echo  NETWORK    : PUBLIC - bind 0.0.0.0 on every interface, passed as --lan --i-consent.
  echo               Anyone who can reach this machine can chat with the model. Put it behind HTTPS.
) else (
  if defined LYGO_P_gwpublic (
    echo  NETWORK    : PUBLIC - bind %LYGO_P_gwbind% is not loopback, anyone who can reach it can chat.
  ) else (
    echo  NETWORK    : LOCAL ONLY - bind %LYGO_P_gwbind%. Only this PC can reach the gateway.
    echo               For LAN or public use set gateway_bind in config\local.json ^(or LYGO_GATEWAY_BIND=lan^).
  )
)
if defined LYGO_P_gwmissing if "%LYGO_P_gwmissing%"=="1" (
  echo.
  echo  [warn] model "%LYGO_P_gwmodel%" is NOT in this machine's model vault
  echo         available now : %LYGO_P_gwmodels%
  echo         every request fails until it is found by a Scan ^(console header^) or
  echo         gateway_model / LYGO_GATEWAY_MODEL points at one of the models above.
  echo.
  ping -n 6 127.0.0.1 >nul
)
if defined LYGO_P_gwmissing if "%LYGO_P_gwmissing%"=="?" (
  echo  [note] could not reach the kit's own engine on LYGO_LLAMA_PORT to verify the model -
  echo         boot the console first ^(or set gateway_backend=ollama to use a daemon^).
)
if defined LYGO_P_gwtaken (
  echo.
  echo  [stop] port %LYGO_P_gwport% is already LISTENING: PID %LYGO_P_gwtaken% ^(%LYGO_P_gwimg%^)
  echo         Not started - a second gateway would fight for the port. Stop it with
  echo         LYGO_LLM_CONSOLE_STOP.bat, or give this copy its own gateway_port / LYGO_GATEWAY_PORT.
  echo.
  pause
  exit /b 2
)
if exist "C:\Python313\python.exe" (set "LYGO_PYTHON=C:\Python313\python.exe") else (
  where py >nul 2>&1 && (set "LYGO_PYTHON=py") || (set "LYGO_PYTHON=python")
)
set "LYGO_GWARGS=--backend %LYGO_P_gwbackend%"
if defined LYGO_P_gwport set "LYGO_GWARGS=%LYGO_GWARGS% --port %LYGO_P_gwport%"
if defined LYGO_P_gwmodel set "LYGO_GWARGS=%LYGO_GWARGS% --model %LYGO_P_gwmodel%"
if defined LYGO_P_gwlan (set "LYGO_GWARGS=%LYGO_GWARGS% --lan --i-consent") else (if defined LYGO_P_gwbind set "LYGO_GWARGS=%LYGO_GWARGS% --bind %LYGO_P_gwbind%")
echo.
echo  Starting   : "%LYGO_PYTHON%" -u "%ROOT%src\public_gateway.py" %LYGO_GWARGS%
echo.
"%LYGO_PYTHON%" -u "%ROOT%src\public_gateway.py" %LYGO_GWARGS%
echo.
echo  Gateway stopped.
pause