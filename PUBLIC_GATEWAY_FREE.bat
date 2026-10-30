@echo off
echo [SYSTEM] PUBLIC GATEWAY - FREE BRAIN edition: the web page chats on OpenCode's free pool, no key.
rem ===========================================================================
rem  The same launcher as PUBLIC_GATEWAY.bat, with three settings exported first.
rem
rem    backend  openai              talk to an OpenAI-shaped endpoint instead of our own engine
rem    url      127.0.0.1:9655      the console's OWN free-brain adapter: OpenCode's free pool,
rem                                 reached the only way it can be (through the CLI), no account, no key
rem    model    a -free engine id   what the adapter asks for when a visitor names no model
rem
rem  WHY NOT OPENCODE DIRECTLY: the free tier is client-locked - the same endpoint called from
rem  anywhere but the OpenCode client answers 403 FreeTierError - so the console drives the CLI and
rem  serves the OpenAI shape a web page can use. WHY THE ADAPTER AND NOT THE ADMIN UI: the admin port
rem  (9641) is for the operator; this gateway is the surface that carries the CORS headers a page
rem  needs, which is what the portal tells visitors to attach to.
rem
rem  COST, PLAINLY: the free tier is metered and its per-model data terms differ, and every turn
rem  spawns the CLI (seconds per answer). Side and demo work - not private operator context.
rem
rem  Precedence is unchanged: exported LYGO_* > config\local.json > config\console.json > app default.
rem ===========================================================================
setlocal EnableExtensions
set "LYGO_GATEWAY_BACKEND=openai"
if not defined LYGO_PUBLIC_OPENAI_URL set "LYGO_PUBLIC_OPENAI_URL=http://127.0.0.1:9655/v1/chat/completions"
if not defined LYGO_GATEWAY_MODEL set "LYGO_GATEWAY_MODEL=opencode/glm-5.3-flash-free"
if not defined LYGO_GATEWAY_PORT set "LYGO_GATEWAY_PORT=9642"
call "%~dp0PUBLIC_GATEWAY.bat"
