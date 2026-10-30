# LYGO LLM Console — do not break

Working product: Agent Portal with 24 limbs, Wikipedia search, URL prefetch, SOUL.md / MEMORY.md, session save.

## Never

- Do **not** name a JS variable `history`. Use `chatHistory`. Always `window.history.replaceState`.
- Do **not** use `Get-NetTCPConnection` in the BAT (hangs). Use `netstat` + `taskkill`.
- Do **not** scan the whole Documents tree at boot.
- Do **not** bind HTTP only *after* a long scan. Listen first, warmup in a thread.
- Do **not** start kit/ or `docs/lygo-llm-console-public/` copies. Canon is this folder.
- Do **not** ship vaults, `data/.lygo_llm_token`, or `engine/*.exe` to public git.

## Always

- Inline CSS/JS on `GET /` so the browser cannot mix old `app.js` with new HTML.
- BAT: `<EDRIVE>\lygo-protocol-stack\lygo_llm_console\LYGO_LLM_CONSOLE.bat` (Desktop trampoline OK).
- Tests: `python -m unittest discover -s tests -v`
- Loopback bind `127.0.0.1`; `AUTH_REQUIRED` only with `--lan --i-consent`.
- P0 blocks `format c:`, diskpart, OS wipe.
- Access logs must strip `?query` (operator token lives in `?t=`).
- Session JSON is written via a temp file then replace.
- `compose_system` uses `pulse_stamps()` only (no weather HTTP on every chat).
- llama-server watchdog reboots if the runner process dies.
- Public branded bots that template-refuse identity lockers are RESOURCE only — see `docs/whitepapers/LYGO_HARD_STOP_LESSON_v1.md`. Do not spend sessions jailbreaking them.

## Ports

- Portal `9641`
- llama-server `11441`

## Continuity

- `workspace/SOUL.md` identity
- `workspace/MEMORY.md` growing notes
- `save/sessions/current.json` chat
