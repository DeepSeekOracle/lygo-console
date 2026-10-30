---
name: filmcraft
description: Edit, inspect and render video on THIS machine with the `film` limb (FilmCraft, ships inside the console). Use when the operator says cut, clip, edit, timeline, sequence, export, render a frame, or names a .fcproj — and never claim an edit you did not make.
version: 1.0.0
metadata: {"openclaw": {"emoji": "🎬", "homepage": "https://chatagent.ca/lygoskillhub.html"}, "lygo": true, "limb": "film", "module": "lygo.filmctrl"}
---

# FilmCraft — video editing, locally

FilmCraft is an open-source, Premiere-style video editor (MIT OR Apache-2.0) that **ships inside this
console** at `utilities/filmcraft/`. The agent drives it through the **`film` limb**; the operator
drives the same code through the **FilmCraft** and **FilmCraft controls** cards in the console's
strip. Nothing here touches the internet, and no media leaves the machine.

The one rule that matters: **this limb reports what the engine actually wrote.** It does not narrate
an edit. If a call did not export a file, say so and give the caller the engine's own words.

## Start here, every time

```
film  {"action": "status"}
```

It answers with the install, the pinned version, what is running, the command count, and the last
clip written. If it says the payload is absent, the fix is named in the answer
(`scripts/fetch_filmcraft.ps1`) — **never invent a path or claim FilmCraft is installed because a
folder exists**. Fetching is the operator's decision, not something to do mid-turn.

## The actions

| action | what it really does |
|---|---|
| `status` | what is installed, running, and last written (start here) |
| `doctor` | the same install verdict, written for a caller that wants the gaps named |
| `open` / `stop` | start / stop the DESKTOP editor with its control channel on loopback (`stop` refuses a copy it did not start) |
| `web` | serve the offline browser editor (same engine compiled to WebAssembly) and answer with its **URL** |
| `inspect` | a project or its sequence as JSON — `what=sequence|project|both`, `demo=true` uses the built-in demo project |
| `commands` | find one of ~693 commands by words: `{"action":"commands","query":"razor"}` |
| `describe` | one command's parameters: `{"action":"describe","command":"timeline.razor"}` |
| `run` | run commands on a project: `steps=[{"id":"sequence.addEdit","params":{...}}]`, `save=true` to write it back |
| `export` | render the sequence to a file (H.264 by default): `out="my-cut.mp4"`, optional `start`/`end` (frames or seconds) |
| `render` | ONE still frame (PNG) from the program monitor: `seconds=1.0`, `out="frame.png"` |
| `presets` | the export presets this install has, with their format and limits |

## Where files land (and why it matters)

* `project` — a name inside `workspace/film/projects/`, or an absolute `.fcproj` path.
* `out` / the render `out` — a bare name lands in `workspace/film/exports/` (or `.../renders/`).
* **Anything outside `workspace/film` needs `consent=true`, for reading as well as writing.** A
  project of the operator's own is never read or edited by an agent without an explicit yes — the
  limb refuses with `consent_required` and says why.

## The honest workflow

1. `status` — is it even here, and what does it have?
2. `inspect` — read the timeline BEFORE changing it (`what=sequence` is usually what you want).
3. `commands` / `describe` — a command id you can name, with the parameters it really takes. Do not
   guess an id: 693 commands exist and a guessed one is refused by the engine.
4. `run` with `save=true` only when the operator asked for the edit to stick.
5. `export` (or `render` for a still) — and then **report the absolute path and size the answer
   carries**, because the file was read back off disk. If the answer says `ok:false`, quote its `why`.

## The console's buttons (the operator's door)

The **FilmCraft controls** card publishes four buttons: *Boot FilmCraft*, *Browser editor*, *Stop
editor*, *Stop web*. They post to `POST /api/film` with `{"action": "boot"|"web"|"stop"|"web_stop"}` —
the same harness as the limb. A POST answer that carries a `url` is the browser editor's address; the
console opens it. If the operator says "the card says x", read the card (`GET /api/film`) before
answering: the card and the limb cannot disagree, so a disagreement means you are reading it wrong.

## What it will not do (do not promise these)

* It will not put the editor's own editing UI inside the console. The console has a small strip; the
  editing surface is the editor (desktop window or the served browser page).
* It will not edit over a foreign project without consent, and it will not stop an editor the
  operator started themselves.
* It will not download itself. A kit without the payload refuses and names the fetcher.
* It will not claim a render happened because a command exited 0 — the answer carries the file, the
  byte count and the mtime of what is actually on disk.
