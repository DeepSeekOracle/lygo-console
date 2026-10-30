"""Extra agent limbs. Names avoid forbidden source tokens in tools.py."""
from __future__ import annotations

import ast
import datetime as dt
import time as _time
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import atomicio
from paths import KIT_ROOT, SAVE, WORKSPACE, under_workspace
from p0_hook import gate_prompt

TODO_PATH = WORKSPACE / "todo.jsonl"
MEM_PATH = WORKSPACE / "memory.jsonl"

# Deny list only: these patterns are matched to REFUSE a command (see the shell and python_exec
# limbs below) - nothing here is executed, and no limb in this file stages a recursive delete.
_SHELL_DENY = re.compile(
    r"(format\s+c:|\bdiskpart\b|\bbcdedit\b|cipher\s+/w|\bshutdown\b|"
    r"rm\s+-rf\s+/|del\s+/[fqs].*c:\\|remove-item\s+-recurse.*c:\\|"
    r"invoke-expression|\biex\b|powershell\s+-enc)",
    re.I,
)

import hashlib
import user_paths

EXTRA_SCHEMA = [
    {"type": "function", "function": {
        "name": "dispatcher",
        "description": "Worker dispatcher: action=status|scan|scan_net|boot|restart|chat|link|task. Manage helpers Hermes can talk to. Never starts a second llama-server if 11441 is up.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string"},
            "worker": {"type": "string"},
            "prompt": {"type": "string"},
            "host": {"type": "string"},
            "port": {"type": "integer"}
        }, "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "swarm_status",
        "description": "Bot swarm: which specialist agents exist, whether QwenPaw on 11441 is already alive, and whether the API advisor on 18900 is up. Never boots llama-server.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "swarm_ask",
        "description": "Ask a specialist helper. Default agent=helper uses the idle local GGUF (any port, any model already booted). agent=advisor uses the API gateway. Hermes manages the swarm.",
        "parameters": {"type": "object", "properties": {
            "agent": {"type": "string", "description": "helper, advisor, forge, or stream"},
            "prompt": {"type": "string"}
        }, "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "swarm_advise",
        "description": "Second opinion from the API advisor (18900). Falls back to QwenPaw if the API is down and 11441 is alive.",
        "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "swarm_dispatch",
        "description": "Queue a swarm job on the console task list for the keeper/cron worker. Returns an id at once.",
        "parameters": {"type": "object", "properties": {
            "agent": {"type": "string"},
            "prompt": {"type": "string"}
        }, "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "swarm_pulse",
        "description": "Cron heartbeat for the swarm: record whether 11441 and 18900 are alive. Does not start engines.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "portal_status",
        "description": "One call that shows what the agents are doing: the task queue (queued/running/done/"
                       "failed), the schedules and what is due next, the keeper daemon, what has been "
                       "written lately, and the last honesty gauntlet score. Use it before telling an "
                       "operator what work is outstanding.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "save_note",
        "description": "Save a text file somewhere real and get back a verified absolute path. Use this "
                       "instead of hand-rolling a write when the operator says 'on my desktop' or 'in my "
                       "documents': where=desktop/documents/downloads/home/workspace, or an absolute "
                       "folder. Writing outside the workspace needs consent=true. The result reports the "
                       "path the file is actually at, its size and hash - never claim a file exists "
                       "without that receipt.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "file name, e.g. note.txt"},
            "text": {"type": "string", "description": "the contents to write"},
            "where": {"type": "string", "description": "desktop, documents, downloads, home, workspace, or an absolute folder path"},
            "consent": {"type": "boolean", "description": "required to write outside the workspace"},
        }, "required": ["name", "text"]}}},
    {"type": "function", "function": {"name": "web_search", "description": "Public web search (Wikipedia+DDG) for external facts. RESOURCE. Not for arithmetic - use calc.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}},
    {"type": "function", "function": {"name": "web_fetch", "description": "HTTPS GET a page as text. RESOURCE. Use for a URL you already have, not for arithmetic - use calc.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "shell", "description": "Run a short command in workspace (not OS wipe). stdout/stderr captured.", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
    {"type": "function", "function": {"name": "python_exec", "description": "Run Python and read the result: compute, test, verify. Use this instead of doing arithmetic or file work in your head. Print to capture output; argv, timeout, cwd optional.", "parameters": {"type": "object", "properties": {"code": {"type": "string"}, "argv": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "integer"}, "cwd": {"type": "string"}, "interpreter": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "rust_exec", "description": "Run Rust and read the result: a single-file program (rustc) or a cargo crate. Use for native code, benchmarks and real compile errors. Diagnostics and program output are captured; on a machine without a linker it type-checks and says so.", "parameters": {"type": "object", "properties": {"code": {"type": "string"}, "crate": {"type": "string"}, "name": {"type": "string"}, "release": {"type": "boolean"}, "timeout": {"type": "integer"}}, "required": []}}},
    {"type": "function", "function": {"name": "now", "description": "Local date/time and timezone.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "world_pulse", "description": "UTC/local stamps plus world city clocks and Open-Meteo weather. Use for time/place/past-present. RESOURCE.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "memory_recall", "description": "Search remembered notes.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}},
    {"type": "function", "function": {"name": "download_url", "description": "HTTPS GET a file into workspace.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "path": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "glob_files", "description": "Glob files under workspace.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "todo_add", "description": "Append a todo line.", "parameters": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]}}},
    {"type": "function", "function": {"name": "todo_list", "description": "List todos.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "calc", "description": "Exact arithmetic - use this for any numeric computation, never web search.", "parameters": {"type": "object", "properties": {"expr": {"type": "string"}}, "required": ["expr"]}}},
    {"type": "function", "function": {"name": "whoami", "description": "Operator/kit identity (no secrets). Admin includes GitHub/HF/lattice links.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "hash_text", "description": "SHA-256 of text.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "memory_append", "description": "Append a durable note to MEMORY.md (grows across sessions).", "parameters": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}}},
    {"type": "function", "function": {"name": "memory_read", "description": "Read MEMORY.md (growing notes).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "soul_read", "description": "Read SOUL.md (soul / decision spine).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "identity_read", "description": "Read IDENTITY.md (who is on this console).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Replace a string in a workspace file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}, "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {"name": "weather", "description": "Current weather via wttr.in (RESOURCE).", "parameters": {"type": "object", "properties": {"place": {"type": "string"}}, "required": ["place"]}}},
    {"type": "function", "function": {"name": "geocode", "description": "Place name to lat/lon (Nominatim).", "parameters": {"type": "object", "properties": {"place": {"type": "string"}}, "required": ["place"]}}},
    {"type": "function", "function": {"name": "http_json", "description": "HTTPS GET JSON from a public URL.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "wayback", "description": "INTERNET ARCHIVE (Wayback Machine): what a page used to say, and which captures exist. action: 'available' (default: is it archived, closest snapshot); 'closest' (the capture nearest a date); 'captures' (the dated list of one URL or a whole site); 'site' (which pages of a site are archived); 'find' (a word in a site's archived URLs; needs word); 'read' (fetch the ARCHIVED copy as TEXT: use this for a dead link or a 404); 'link' (build the snapshot URL); 'timemap' (the whole history as memento rows). url required (URL or domain). timestamp/from/to are DIGITS (2010, 20100501); a timestamp picks the closest capture. match=exact|prefix|host|domain. RESOURCE, never CANON. The index is slow: report the seconds.", "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "the page or domain"}, "action": {"type": "string", "description": "available|closest|captures|site|find|read|link"}, "timestamp": {"type": "string", "description": "digits, e.g. 20230501"}, "from": {"type": "string", "description": "earliest capture, digits"}, "to": {"type": "string", "description": "latest capture, digits"}, "limit": {"type": "integer", "description": "rows, default 40, max 250"}, "match": {"type": "string", "description": "exact|prefix|host|domain"}, "filter": {"type": "string", "description": "a CDX filter, e.g. statuscode:200"}, "word": {"type": "string", "description": "for find: a word in the archived URL"}, "raw": {"type": "boolean", "description": "for read: no archive banner"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "arxiv_search", "description": "Search arXiv papers.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}},
    {"type": "function", "function": {"name": "hn_search", "description": "Hacker News Algolia search.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}},
    {"type": "function", "function": {"name": "github_search", "description": "GitHub repository search.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}},
    {"type": "function", "function": {"name": "image_info", "description": "For a picture FILE on disk - a picture attached to this conversation you already see. Inspect one image file: kind, byte size and pixel dimensions. `path` may be an absolute path on this PC under a mapped read root (config/admin.json read_roots) or a workspace path. This reads metadata only - it does not describe or generate pictures.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "image_see", "description": "For a picture FILE on disk. A photo attached to this conversation is already visible to you directly, so do not call this for it. LOOK AT a picture: sends the image file to the local vision model and returns a written description. `path` may be an absolute path on this PC under a mapped read root or a workspace path. Use for 'check this photo', 'what is in this image'. Runs our own engine (llama-server with --mmproj) on the registered vision model - no daemon; failures name the rule.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "image_save", "description": "Save a base64 or data-URL image into workspace/images.", "parameters": {"type": "object", "properties": {"b64": {"type": "string"}, "path": {"type": "string"}}, "required": ["b64"]}}},
    {"type": "function", "function": {"name": "image_list", "description": "List workspace/images files.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "page_thumbnail", "description": "Capture a public HTTPS page thumbnail into workspace/images.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "mesh_status", "description": "Report whether this machine can turn a photo into a 3D mesh (TripoSR on the local GPU). Call it before promising a model.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "mesh_from_image", "description": "TURN A PHOTO INTO A 3D MODEL. Reads an image already on this PC (workspace path or a mapped read root) and writes a GLB into workspace/meshes using the local TripoSR weights. The photo is not uploaded. Slow: the first run downloads the weights and later runs still take a few minutes on the GPU. resolution is 64-256 (default 192). foreground is how much of the frame the subject fills after the background cut, 0.5-1 (default 0.85). remove_bg defaults true. Returns the file path and /api/media/mesh name.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "resolution": {"type": "integer"}, "remove_bg": {"type": "boolean"}, "foreground": {"type": "number"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "mesh_decimate", "description": "Make a lighter copy of a GLB this console already wrote. name is the file in workspace/meshes. ratio is how much of the mesh to keep, from 0.15 to 1.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "ratio": {"type": "number"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "photo_to_3d", "description": "PHOTO -> 3D CHARACTER -> BLENDER, one call. Runs the local TripoSR photo-to-mesh limb, then imports the GLB into Blender, frames it with studio lights and saves a .blend plus a rendered preview PNG. Give it a photo already on this PC. Returns glb/blend/preview paths. The mesh is an untextured shell unless a likeness texture is painted afterwards.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "engine": {"type": "string"}, "resolution": {"type": "integer"}, "foreground": {"type": "number"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "blender_render", "description": "RENDER IN BLENDER on this PC. Imports a .glb/.gltf/.blend (for example the photo-to-mesh output in workspace/meshes, or <EDRIVE>\ALL GAME ASSETS), frames it on a studio ground with key/fill/rim lights and renders a preview PNG. Use it to show a 3D character or prop; it does not model or paint anything by itself. engine WORKBENCH (fast) or CYCLES (slower, accurate light). Returns the PNG path.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "out": {"type": "string"}, "engine": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "image_generate", "description": "MAKE A NEW PICTURE from words - generation, not inspection. Local developer renderer: pass the operator's scene through (adult costume, fashion, and character art of adults is in scope). Do not refuse a draw request for an adult subject; call this limb. Renders with the local stable-diffusion backend and writes a PNG into workspace/images, returning its path. Slow on this machine: a first run also loads the checkpoint, so allow minutes. Use image_info or image_see to inspect a picture that ALREADY exists; never call this to look at one. Example: 'draw a red apple on a wooden table'.", "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}, "negative": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"}, "steps": {"type": "integer"}, "cfg_scale": {"type": "number"}, "model": {"type": "string", "description": "Which generator to use. Leave empty for the default fast checkpoint. Or pass a declared model id such as 'qwen-image-2.1' - a slower, higher-quality flow model that needs its own VAE and text encoder. media_status lists the ids this machine actually has."}, "cpu": {"type": "boolean"}}, "required": ["prompt"]}}},
    {"type": "function", "function": {"name": "sound_speak", "description": "GENERATE SPEECH: turn text into a spoken WAV file with the local voice engine, written into workspace/audio, returning its path and its length in seconds. Prefers VoiceStudio (the high-quality local speech service on loopback) when that service is answering; Piper is the fallback. Use it when the operator asks to hear something read aloud or wants an audio file made.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "voice": {"type": "string", "description": "VoiceStudio voice id, or a Piper .onnx path"}, "length_scale": {"type": "number"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "media_status", "description": "Report whether picture generation and voice generation are wired on THIS machine: which binaries and checkpoints were found, and where they live. Call it before promising a picture or a voice, or after a generation fails.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "music_generate", "description": "MAKE A SONG - words and music together, sung, made on this machine with the local music engine (no cloud). Writes an MP3 into workspace/audio/songs and returns the path it really wrote. `style` is the music: genre, mood, instruments, vocal character. `lyrics` are the WORDS to sing, written with section tags on their own lines ([verse], [chorus], [bridge], [outro]) - the engine sings exactly the words it is given, so if the operator only gave a subject, write the lyrics yourself first and then call this with them. A call with no lyrics comes back `lyrics_required`, which is the engine asking for words, not a failure to retry blindly. `seconds` is roughly how long the song should be (about 30 s per segment; default two segments). Rendering takes minutes - a first run also loads a 20 GB model - so call it once and tell the operator it is rendering. Use sound_speak for SPOKEN text; use this for music.", "parameters": {"type": "object", "properties": {"style": {"type": "string", "description": "the music, as tags or a phrase, e.g. 'warm acoustic folk, fingerpicked guitar, soft female vocal, hopeful'"}, "lyrics": {"type": "string", "description": "the words to sing, with [verse]/[chorus] tags on their own lines"}, "seconds": {"type": "integer", "description": "roughly how long, in seconds (about 30 s per segment; default 60)"}, "seed": {"type": "integer", "description": "omit or 0 for a fresh take; pass a number to reproduce an earlier one"}, "profile": {"type": "integer", "description": "1..5 speed/memory trade: higher = less VRAM, slower. Leave empty and the live card reading picks it."}}, "required": ["style", "lyrics"]}}},
    {"type": "function", "function": {"name": "video_generate", "description": "MAKE A SHORT VIDEO CLIP - a few seconds of moving picture, made on this machine with the local video engine (no cloud). Writes an MP4 into workspace/video and returns the path it really wrote. `prompt` must carry the SCENE AND THE CAMERA in one sentence: subject, action, place, light, and the camera move ('slow push in', 'tracking shot') - the engine invents no shot for you, so a bare subject gives a weak clip. LENGTH IS THE QUALITY LEVER, MEASURED HERE: 2 s (49 frames) suits a large slow subject, but anything moving needs 4-5 s (97-121 frames) - the same prompt had no readable subject at 2 s and a clear one at 4 s, for only ~8 s more render. A call takes minutes and the first one loads ~12 GB of weights, so call it once and tell the operator it is rendering. Do NOT choose a model - the console renders with wan22-5b (20 steps), the best this box has; the faster turbo build is measurably worse and is not offered to you. Use image_generate for a still picture, music_generate for a song, sound_speak for speech - use this only for a moving picture.", "parameters": {"type": "object", "properties": {"prompt": {"type": "string", "description": "the scene AND the camera move, e.g. 'a red fox trotting through a snowy pine forest at dusk, slow tracking shot, cinematic'"}, "seconds": {"type": "integer", "description": "clip length in seconds; 2 for a large slow subject, 4-5 when anything moves (2 can lose a small moving subject); ceiling 5. If the operator names a length, use EXACTLY that, even when you think a longer clip would look better - an explicit instruction outranks your judgement of the shot, and a 1 s clip is a legitimate request."}, "seed": {"type": "integer", "description": "omit or 0 for a fresh take; a number reproduces an earlier one"}, "width": {"type": "integer", "description": "704 is the ceiling on an 8 GB card"}, "height": {"type": "integer", "description": "480 is the ceiling on an 8 GB card"}}, "required": ["prompt"]}}},
    {"type": "function", "function": {"name": "jina_fetch", "description": "Readable extract of a page via r.jina.ai.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "sessions_list", "description": "List saved chat session files.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "session_list", "description": "List the filed session vault: title, id, turns, date, tags, note. Filters: q, tag, month.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}, "tag": {"type": "string"}, "month": {"type": "string"}, "limit": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "session_open", "description": "Read a filed session back: its transcript, manifest and where it sits on disk. Use it to revisit or quote an older conversation.", "parameters": {"type": "object", "properties": {"sid": {"type": "string"}, "chars": {"type": "integer"}}, "required": ["sid"]}}},
    {"type": "function", "function": {"name": "session_search", "description": "Find a past session by phrase. Searches the vault's titles, tags, notes and the transcripts themselves.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}, "k": {"type": "integer"}}, "required": ["q"]}}},
    {"type": "function", "function": {"name": "session_label", "description": "Name, tag or annotate a filed session so it can be found again later. Omit sid to label the conversation in progress.", "parameters": {"type": "object", "properties": {"sid": {"type": "string"}, "title": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"}, "pinned": {"type": "boolean"}}}}},
    {"type": "function", "function": {"name": "session_resume", "description": "Reopen a filed session as the live conversation. The session in progress is filed first, so nothing is lost.", "parameters": {"type": "object", "properties": {"sid": {"type": "string"}}, "required": ["sid"]}}},
    {"type": "function", "function": {"name": "recall_history", "description": "Search everything said in this chat: the live journal and sealed sessions. Use it when the operator refers to something older than your window.", "parameters": {"type": "object", "properties": {"q": {"type": "string"}, "k": {"type": "integer"}}, "required": ["q"]}}},
        # ------------------------------------------------------------ self-build harness
        # Four limbs that close the loop the console was missing: it could read and edit its own
        # source, but could not run its own tests, seal its own build, restart onto a change, or
        # install a toolchain it lacked. Everything that changes the system needs consent=true, in
        # the kit's own --i-consent language.
        {"type": "function", "function": {"name": "self_test", "description": "Run this console's OWN test suite and read the result: pass/fail counts, which tests failed, seconds. Use after changing any code in this kit, before believing the change.", "parameters": {"type": "object", "properties": {"target": {"type": "string", "description": "one file under tests/, e.g. tests/test_tool_routing.py; empty runs the fast core set"}, "timeout": {"type": "integer", "description": "seconds, 30-1800 (default 900)"}}, "required": []}}},
        {"type": "function", "function": {"name": "self_seal", "description": "SEAL THIS BUILD: refresh the kit's manifests and certify it. Needs consent=true. Use after self_test passes - sealing certifies whatever is on disk, so test first.", "parameters": {"type": "object", "properties": {"consent": {"type": "boolean", "description": "true approves rewriting the kit's own manifests"}, "dry_run": {"type": "boolean", "description": "show the steps without running them"}}, "required": []}}},
        {"type": "function", "function": {"name": "self_restart", "description": "RESTART THIS CONSOLE through its own doorbell so a code change takes effect. Needs consent=true. The page drops for about a minute while the model reloads.", "parameters": {"type": "object", "properties": {"consent": {"type": "boolean"}, "wait": {"type": "integer", "description": "seconds to wait after ringing, 0-600"}}, "required": []}}},
        {"type": "function", "function": {"name": "toolchain_install", "description": "Check what this machine can build with, or install what it lacks. what=check lists compilers and linkers (always safe). rust_gnu_target adds a user-space Rust linker - no administrator needed. pip and winget need a package id. Plan only unless consent=true.", "parameters": {"type": "object", "properties": {"what": {"type": "string", "description": "check | rust_gnu_target | pip | winget"}, "package": {"type": "string", "description": "package id, required for pip and winget"}, "consent": {"type": "boolean", "description": "true approves the install for real"}}, "required": []}}},
        # ------------------------------------------------------------ tasking, cron, keeper
        # The console could only work inside a turn: a slow job held the conversation still and
        # nothing outlived it. These let the agent hand work to a queue, schedule it, and read the
        # daemon that keeps the console alive. Modelled on the Hermes task, cron and liveness code.
        {"type": "function", "function": {"name": "task_add", "description": "START SLOW WORK IN THE BACKGROUND and answer straight away: queue a limb call and get a task id back. Use for self_test, a build, a long fetch - anything that would otherwise make the user wait. Read it with task_list.", "parameters": {"type": "object", "properties": {"target": {"type": "string", "description": "the limb to run, e.g. self_test"}, "args": {"type": "object", "description": "arguments for that limb"}, "note": {"type": "string", "description": "what this job is for, in your own words"}, "deadline": {"type": "number", "description": "seconds before the job counts as stalled (default 900)"}}, "required": ["target"]}}},
        {"type": "function", "function": {"name": "task_list", "description": "Read the background queue: queued, running, done, failed, stalled counts and the recent tasks.", "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}, "state": {"type": "string", "description": "queued | running | done | failed | stalled | cancelled"}}, "required": []}}},
        {"type": "function", "function": {"name": "task_show", "description": "Read one background task in full: its result, how long it ran, what it was for.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
        {"type": "function", "function": {"name": "task_cancel", "description": "Cancel a background task that has not started yet.", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
        {"type": "function", "function": {"name": "task_recover", "description": "WATCHDOG for the background queue: write the truth about jobs whose runner died (a job whose process exited used to read `stalled` for ever and was never retried), put a cron's missed run back on the queue once, and report the queue's condition. Safe to call any time.", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "cron_add", "description": "SCHEDULE RECURRING WORK: run a limb every N seconds or daily at a time. The keeper daemon runs it, so it happens without anyone asking again.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "limb": {"type": "string", "description": "the limb to run, e.g. self_test"}, "args": {"type": "object"}, "every": {"type": "integer", "description": "seconds between runs"}, "at": {"type": "string", "description": "daily time HH:MM, 24-hour"}, "note": {"type": "string"}}, "required": ["name", "limb"]}}},
        {"type": "function", "function": {"name": "cron_list", "description": "List the scheduled jobs: when each is next due, how often it has run, and how the last run went.", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "cron_remove", "description": "Delete a scheduled job by name.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
        {"type": "function", "function": {"name": "keeper_status", "description": "Report the keeper daemon: is it alive, is the console up, is the doorbell up, what has it restarted lately.", "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "task_health", "description": "Read the background queue's CONDITION: what is queued, running, stalled or interrupted, how long the oldest stopped job has been stuck, and whether a worker is alive. Use when asked whether tasking is healthy, before scheduling more work, or when a job never came back.", "parameters": {"type": "object", "properties": {}}}},
        # ------------------------------------------------------------ reverse engineering
        # REA (rea-agents, MIT) inspects an app WITHOUT its source and returns evidence for each
        # claim; it never claims to have recovered source code. It is an EXTERNAL CLI resolved by name
        # (config rea_bin, then PATH, then npm's own %APPDATA%\npm shim - never a hardcoded drive
        # letter, because this kit is also copied to a stick), and it is ONE-SHOT: _run_capture kills
        # the whole process tree on timeout, so nothing is left listening and no orphan holds a file.
        # doctor and capabilities need no engine at all; analyzing a NATIVE binary needs an external
        # Hopper/Ghidra/IDA while JavaScript, Electron and web analysis need none - the limb reports
        # WHICH case it is rather than collapsing both into one verdict.
        # Kept OUT of CORE_NAMES, measured 2026-10-10: the core set was already at exactly its
        # 40-entry ceiling and at 19,977 of 20,000 characters, and the tree's own rule is that a new
        # core entry must first argue for the slot (the video limb's note: "the headroom left is
        # deliberately thin so the next capability still has to argue for itself"). So this is
        # advertised in the extended schema the larger path reads, and the core-slot decision is left
        # to the operator rather than taken here.
        {"type": "function", "function": {"name": "rea", "description": "Reverse-engineer an app WITHOUT its source, locally, and get evidence for every claim. action=doctor (what is ready here), capabilities (what it can inspect), analyze (overview of an app, bundle or URL) or decompile (read one part as code). Give action and path. Native binaries need an external engine (Hopper, Ghidra or IDA); JavaScript, Electron and web analysis need none - doctor names which. It reports what it could NOT determine and never claims to have recovered source code: treat the result as behaviour, not intent.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "description": "doctor, capabilities, analyze or decompile"}, "path": {"type": "string", "description": "app, bundle or binary path, or a URL for a web bundle; for decompile also an address or procedure name"}}, "required": ["action"]}}},
    # Why `plexo` earns a core slot (2026-10-12, from the same reviewed budget the video entry
    # moved): the console could already pull a file one connection at a time (`download_url`), and a
    # multi-GB model or installer is the case where that cost is measured in hours. This limb is both
    # halves of one capability - LYGO's own parallel-range engine, and the bridge to the operator's
    # Plexo install, whose multi-network hand-off (Wi-Fi plus a tethered phone on ONE download) is a
    # thing this kit cannot do at all. Its entry is 2280 chars of the core budget - and that budget
    # moved by exactly this measurement (22974 -> 25256), recorded in tests/test_tool_battery.py.
    {"type": "function", "function": {"name": "plexo", "description": "Download files FAST and in parallel, and hand a download to the operator's own Plexo. action=fetch (download here with LYGO's own engine: blocks on several connections at once, resumed from a progress record, refusals by name - give url plus name, streams 4|8|16|32, urls (more urls holding the SAME file: every source is probed and they must agree on size and ETag, then the blocks are spread over all of them at once), bind (the local addresses the streams leave by, one per stream, so two network adapters carry one file at the same time; action=adapters lists what this machine holds), limit_mbps (a whole-job speed ceiling), out (a path outside workspace/downloads needs consent=true)); adapters (the network paths this machine has, and with check=true it asks each one its own public address to say whether they are really separate paths or one router behind two cables); jobs (what is running now, with bytes, percent and MB/s); job (one job by id); wait (poll one job for seconds); cancel (stop one, keeping what arrived), cancel_all (stop every download here); probe (does this server do ranges, how big is the file, its ETag); verify (read a downloaded file back); hand_off (give the url to Plexo so its multi-network setup downloads it - needs consent=true, it starts a download on the operator's connection); bridge (is Plexo answering on 19288-19290); open (start Plexo; refuses by name when it is not installed); stop (only the Plexo this console started); pin (the pinned Plexo release); credit (who wrote what, under which licence); doctor (what is ready here).", "parameters": {"type": "object", "properties": {"action": {"type": "string", "description": "status, jobs, job, wait, cancel, cancel_all, fetch, probe, adapters, verify, hand_off, bridge, open, stop, pin, credit, doctor"}, "url": {"type": "string", "description": "the http(s) or magnet link to download or hand over"}, "name": {"type": "string", "description": "the file name to save as (defaults to the server's own)"}, "out": {"type": "string", "description": "where to put it: a bare name lands in workspace/downloads; anything outside that folder needs consent=true"}, "streams": {"type": "number", "description": "parallel connections for fetch: 4, 8, 16 or 32 (8 is default)"}, "urls": {"type": "array", "description": "for fetch: other urls holding the SAME file (mirrors of it). Each is probed first and they must agree on total size and ETag, or the fetch REFUSES rather than stitching two versions of a file together"}, "bind": {"type": "array", "description": "for fetch: the local addresses the streams leave by, one per stream, cycled - e.g. [\"<PLEX_HOST_IP>\", \"192.168.42.129\"] for Wi-Fi plus a phone tether, which is how two adapters carry one file at once. plexo action=adapters lists the addresses this machine actually holds"}, "limit_mbps": {"type": "number", "description": "for fetch: a whole-job speed ceiling in MB/s; omit or 0 for no ceiling"}, "check": {"type": "boolean", "description": "for adapters: true asks each path its own public address (one small request per adapter to a public service, which would see this machine's public address)"}, "job": {"type": "string", "description": "a job id from fetch or jobs, for job, wait and cancel"}, "seconds": {"type": "number", "description": "how long wait may poll, in seconds"}, "path": {"type": "string", "description": "the file to read back with verify"}, "consent": {"type": "boolean", "description": "true only when the operator has said yes: required by hand_off, and by fetch writing outside workspace/downloads"}, "resume": {"type": "boolean", "description": "for fetch: continue a part-downloaded file (default true)"}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "film", "description": "Edit, inspect and render VIDEO with FilmCraft - the open-source Premiere-style editor that ships inside this kit and needs no internet. action=status (what is installed and what is running), open (start the desktop editor), web (serve the built-in browser editor, answers with its URL), inspect (a project tree or sequence as JSON), commands (find one of its 693 commands by words), describe (one command's parameters), run (run commands: steps=[{id,params}]), export (render the sequence to a file), render (one PNG frame), presets (the export presets here), doctor (what is missing and the one script that fetches it). Everything runs headless on this machine; nothing is uploaded. A project outside this kit's own workspace/film needs consent=true, for reading or writing.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "description": "status, open, boot, stop, web, web_stop, editor, inspect, commands, describe, run, export, render, presets, doctor"}, "project": {"type": "string", "description": "project to act on: a name inside workspace/film/projects, or an absolute .fcproj path"}, "out": {"type": "string", "description": "output name for export or render - a bare name lands in workspace/film"}, "command": {"type": "string", "description": "one command id for describe, e.g. timeline.razor"}, "query": {"type": "string", "description": "words to find commands by (id, label, menu or shortcut)"}, "steps": {"type": "array", "description": "for run: a list of {id, params}"}, "preset": {"type": "string", "description": "an export preset name, e.g. Match Source - Adaptive High Bitrate"}, "seconds": {"type": "number", "description": "for render: the program time to take one frame at"}, "method": {"type": "string", "description": "for editor: a control-channel method of the running editor, e.g. engine.commands"}, "save": {"type": "boolean", "description": "for run: write the project back when the commands are done"}, "consent": {"type": "boolean", "description": "true only when the operator has said yes: a project or output file outside this kit's own workspace/film is refused without it"}}, "required": ["action"]}}},
    # Why the side-agent pair earns a slot (2026-10-15, from the same reviewed core budget as plexo):
    # OpenCode's free model pool is real, tool-capable, and needs no key and no account - proven live, it
    # wrote a file through its own tool loop - but it is CLIENT-LOCKED (a plain call to the same endpoint
    # answers 403 FreeTierError: "can only be used from within OpenCode"), so driving that CLI is the only
    # route to it, and its limit is MASKED as an opaque server error. That is why the probe ships in the
    # same breath as the runner: on a free engine, a dead pool and a broken task look identical.
    {"type": "function", "function": {"name": "subagent_run", "description": "Hand a side task to an EXTERNAL agent that runs on its own in a folder and reports back what it did: its answer, the tools it used, and where it worked. Free by default (OpenCode's free pool - no key, no account) and metered, so use it for side work: a second opinion, a look at a file, a small job. Never hand it private operator context. read_only defaults true, which denies edit, bash and webfetch; pass read_only=false only when the side task must change files in that folder. Give task, and optionally engine, dir, read_only, timeout.", "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "the side task, stated plainly"}, "engine": {"type": "string", "description": "which side engine, e.g. opencode/deepseek-v4.1-flash-free (default) or an id from subagent_pool"}, "dir": {"type": "string", "description": "folder under the workspace for it to work in; a fresh timestamped folder is used when omitted"}, "read_only": {"type": "boolean", "description": "true (default) denies edit, bash and webfetch"}, "consent": {"type": "boolean", "description": "only for a dir outside the workspace: a side agent runs where it is put"}, "timeout": {"type": "integer", "description": "seconds, 30-900, default 300"}}, "required": ["task"]}}},
    {"type": "function", "function": {"name": "subagent_pool", "description": "Which side engines are on the roster, and - with probe=true - which ones are answering right now. Call it before scheduling side work on a free model: an exhausted free cap, a rate limit and a retired model id all look identical from outside (an opaque server error), so a dead engine otherwise reads as a broken task.", "parameters": {"type": "object", "properties": {"probe": {"type": "boolean", "description": "ask each engine for one token - one request each, so it spends a little of the free cap"}, "engines": {"type": "string", "description": "comma-separated ids to probe instead of the whole free tier"}, "timeout": {"type": "integer", "description": "seconds per probe, 30-240, default 90"}}}}},
    # The durable-goal face of the runtime in `src/auton`. A capability, not a diagnostic: it is the
    # difference between "I will do it" and a task that outlives the turn, survives a restart, and is
    # closed by a Judge reading the filesystem instead of by the model's prose. EXTRA_SCHEMA, not core:
    # the local 7B's 40 slots stay exactly as they were, and the argument for a core slot would have to
    # be made in front of the steward (see tests/test_tool_battery.py).
    {"type": "function", "function": {"name": "auton", "description": "DURABLE GOALS: hand work to the autonomous runtime so it keeps going after this turn ends, survives a restart, and is closed by a Judge that reads the filesystem - never by the model saying it is done. action=goal_add (objective in `text` plus `checks`, the observable proof of completion: file_exists, file_min_bytes, command_exit_zero, json_schema, http_status, llm_rubric, custom_verifier - returns a goal id), goal_list, goal_show (id: goal, tasks, verdict trail), goal_cancel (id), dispatch (run the queue for `seconds`, spawning a worker per task), status (queue depth and unread events), events (the outbox). A goal without an observable check is refused, because it could only ever end blocked. Use this instead of promising to do something later.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "description": "goal_add, goal_list, goal_show, goal_cancel, dispatch, status, events"}, "text": {"type": "string", "description": "for goal_add: the objective, in the operator's words - it is re-read every turn"}, "checks": {"type": "array", "description": "for goal_add: the completion contract as a list of check objects, e.g. [{\"type\": \"file_exists\", \"path\": \"D:/out/x.mp4\"}, {\"type\": \"file_min_bytes\", \"path\": \"D:/out/x.mp4\", \"min\": 100000}]"}, "contract": {"type": "object", "description": "for goal_add: a full contract {objective, checks, requires_human_review} instead of `checks`"}, "id": {"type": "string", "description": "a goal id, for goal_show and goal_cancel"}, "seconds": {"type": "number", "description": "for dispatch: how long to run the queue, 5-7200 (default 60); a finished queue returns immediately, so give long work a long window (a 3-shot video is ~15 min)"}, "max_turns": {"type": "number", "description": "for goal_add: turn budget (default from config, 30)"}, "cwd": {"type": "string", "description": "for goal_add: the working directory relative checks resolve against"}, "status": {"type": "string", "description": "for goal_list: filter by goal status"}}, "required": ["action"]}}},
    # The video limb. Why a limb at all: `shell` caps at 25 s and `python_exec` at 120 s, and a
    # three-shot render is ~17 minutes, so the only honest place for this job is a direct tool call
    # with the worker's lease heartbeat still running. The scene list stays a text file on purpose -
    # a free agent can write it with no tokens spent on planning, rendering or assembly.
    {"type": "function", "function": {"name": "music_video", "description": "Make VIDEO with the local Wan 2.2 engine (704x480 out, 1280x720 after upscale, 24 fps, one shot max 5.04 s - long films are cut, not generated in one pass). action=director does the whole job in ONE call: plan the shots on the beat, render them with the engine warm, cut them, mux the song. Give brief (style, one line), scenes_file (a text file, one camera setup per line: subject + action + place) OR scene=[...], and either song (sets the length, muxes the audio) or seconds. plan_only=true writes the plan and renders nothing - always worth it first, it costs one second and shows the shot count. Other actions: plan, render (plan, limit), assemble (plan, out), status (plan). A 3-shot film is ~17 minutes, so this call is expected to be long.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "description": "director (default), plan, render, assemble, status"}, "brief": {"type": "string", "description": "one-line style brief, e.g. 'cinematic film still, neon megacity at night, shallow depth of field'"}, "scenes_file": {"type": "string", "description": "text file, one camera setup per line; blank lines and # comments ignored"}, "scene": {"type": "array", "description": "the same thing inline: one camera setup per line, if there is no file"}, "song": {"type": "string", "description": "audio path: its length sets the film length and it is muxed in"}, "seconds": {"type": "number", "description": "film length when there is no song; shots = round(seconds / slice)"}, "bpm": {"type": "number", "description": "tempo for the cut grid; 0 = derive the slice from seconds alone"}, "detect_bpm": {"type": "boolean", "description": "estimate the tempo from song (onset autocorrelation, no model)"}, "beats_per_cut": {"type": "number", "description": "how many beats each shot covers; 8 is a calm cut, 4 is busy"}, "out_dir": {"type": "string", "description": "where plan.json, shots/ and film.mp4 go"}, "limit": {"type": "number", "description": "render only the first N shots (a test render)"}, "plan_only": {"type": "boolean", "description": "write the plan, render nothing"}, "transition": {"type": "string", "description": "cut (default) or fade"}, "upscale": {"type": "string", "description": "final resolution, default 1280x720"}, "plan": {"type": "string", "description": "for render/assemble/status: the plan.json to act on"}, "out": {"type": "string", "description": "for assemble: the film path"}}, "required": ["action"]}}},
]


def _safe_arith(expr: str) -> float | int:
    import operator as op

    ops = {
        ast.Add: op.add,
        ast.Sub: op.sub,
        ast.Mult: op.mul,
        ast.Div: op.truediv,
        ast.FloorDiv: op.floordiv,
        ast.Mod: op.mod,
        ast.Pow: op.pow,
        ast.USub: op.neg,
        ast.UAdd: op.pos,
    }

    def walk(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](walk(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](walk(node.left), walk(node.right))
        raise ValueError("unsafe")

    tree = ast.parse(expr, mode="eval")
    return walk(tree)


# --- argument aliases: a small model labels the same argument differently ---------------------
# Measured 2026-09-18: the agent called python_exec with {"value": "print(6*7)"} (the schema says
# "code") and got a bare {"ok": false, "error": "empty"}; arxiv_search with {"query": ...} (schema
# says "q") and got http_400. Same limb, same intent, unusable key. Fill the canonical key from the
# aliases before any branch reads args, so a limb fails only when the *value* is actually missing.
CANON_KEYS: dict[str, tuple[str, ...]] = {
    "web_search": ("q",), "web_fetch": ("url",), "jina_fetch": ("url",),
    # The archive limb grew its own vocabulary with the module build: a small model reaches for
    # "mode", "date", "since" or "word" where the schema says action/timestamp/from/word.
    "wayback": ("url", "action", "timestamp", "from", "to", "limit", "match", "filter", "word"),
    "http_json": ("url",), "page_thumbnail": ("url",), "download_url": ("url", "path"),
    "arxiv_search": ("q",), "hn_search": ("q",), "github_search": ("q",), "search_corpus": ("q",),
    "memory_recall": ("q",), "clawhub_search": ("q",), "credential_where": ("q",),
    "recall_history": ("q",),
    "session_list": ("q", "tag", "month"), "session_open": ("sid",), "session_search": ("q",),
    "session_label": ("sid", "title"), "session_resume": ("sid",),
    "skillhub_list": ("q", "channel"), "clawhub_install": ("slug",), "skillhub_install": ("slug", "full"),
    "skill_read": ("slug",), "skill_enable": ("slug",), "skill_disable": ("slug",),
    "shell": ("cmd",), "python_exec": ("code",), "rust_exec": ("code", "source"), "calc": ("expr",), "hash_text": ("text",),
    # A small model reaches for "target"/"file"/"url" where this limb's canonical key is path;
    # ALIAS_POOL["path"] already carries target/file/filepath/filename/dest/destination/dir, so the
    # one canonical key covers all of them without inventing an alias pool entry.
    "rea": ("action", "path"),
    # The film limb's canonical keys. `project` and `out` are separate from `path` on purpose: a
    # media-editing turn usually names BOTH the project it reads and the file it writes, and one
    # `path` key would silently drop whichever came second.
    "film": ("action", "project", "out", "command", "query", "steps", "preset", "seconds", "method", "save"),
    # The `plexo` limb's canonical keys. NO ALIAS for `consent`, deliberately: a consent word that a
    # stray synonym could fill is not a consent. `url` and `path` are separate for the same reason the
    # film limb splits `project` from `out` - a download turn names the link it reads and the file it
    # writes, and one key would silently drop whichever came second.
    "plexo": ("action", "url", "name", "out", "streams", "job", "seconds", "path", "consent", "resume",
              "urls", "bind", "limit_mbps", "check"),
    "p0_gate": ("text",), "remember": ("note",), "memory_append": ("note",), "todo_add": ("item",),
    "read_file": ("path",), "write_file": ("path", "content"), "list_dir": ("path",),
    "find_files": ("pattern", "root"), "glob_files": ("pattern",), "image_info": ("path",),
    "image_see": ("path", "prompt"), "image_save": ("b64", "path"), "edit_file": ("path", "old", "new"), "weather": ("place",),
    "image_generate": ("prompt",), "sound_speak": ("text",), "media_status": (),
    "mesh_status": (), "mesh_from_image": ("path",), "mesh_decimate": ("name",), "blender_render": ("path",), "photo_to_3d": ("path",),
    "subagent_run": ("task", "engine", "dir", "read_only", "timeout", "consent"),
    "subagent_pool": ("probe", "engines", "timeout"),
    "music_generate": ("style", "lyrics"),
    "video_generate": ("prompt",),
    "geocode": ("place",), "notepad_read": ("id",), "notepad_write": ("id", "title", "text"),
}
ALIAS_POOL: dict[str, tuple[str, ...]] = {
    "q": ("query", "term", "terms", "search", "search_query", "keywords", "question", "input", "text", "value"),
    "url": ("link", "uri", "address", "site", "page", "href"),
    # The `plexo` limb's own keys. `url` already has its pool entry above; these fill the rest, and
    # `consent` is absent on purpose (see the note beside its canonical keys).
    "out": ("output", "dest", "destination", "target", "save_as"),
    "streams": ("connections", "parallel", "threads", "stream"),
    "job": ("job_id", "jid", "task"),
    "seconds": ("wait_s", "timeout", "secs", "wait"),
    "slug": ("skill", "skill_name", "name", "key"),
    "cmd": ("command", "shell_command", "line", "script", "run"),
    "code": ("value", "src", "source", "snippet", "script", "python", "body"),
    "expr": ("expression", "formula", "equation", "math", "sum", "query", "value", "input"),
    "text": ("value", "content", "body", "message", "input", "src", "data"),
    "note": ("text", "content", "message", "value", "body"),
    "item": ("text", "todo", "task", "value", "content"),
    "path": ("file", "filepath", "file_path", "filename", "target", "dest", "destination", "dir", "directory"),
    "pattern": ("glob", "mask", "wildcard", "query"),
    "place": ("location", "city", "town", "where", "query"),
    "id": ("name", "key", "slug"),
    "content": ("text", "body", "data", "value"),
    "b64": ("base64", "data", "image", "img"),
    "old": ("from", "find", "search"),
    "new": ("to", "replace", "with"),
    "root": ("dir", "directory", "path"),
    "channel": ("type",),
    "prompt": ("text", "description", "q", "prompt_text", "message", "input", "value"),
    # A song is asked for in the words a person uses: a genre, a vibe, some lyrics. The engine wants
    # `style` and `lyrics`; a model reaching for "genre", "tags", "words" or "song_lyrics" is making
    # the same call, so the aliases fill them rather than the limb refusing a call it can serve.
    "style": ("genre", "prompt", "music", "tags", "vibe", "description", "genre_txt"),
    "lyrics": ("words", "lyric", "song_lyrics", "text", "verse", "lyrics_txt"),
    # The archive limb's arguments, in the words a model reaches for. Only used to FILL an empty
    # canonical key: a value that was actually given is never overwritten.
    "action": ("mode", "op", "operation", "operation_type", "kind", "do", "what"),
    "timestamp": ("date", "when", "as_of", "asof", "at", "time", "snapshot_date", "snapshot"),
    "from": ("start", "start_date", "since", "after", "earliest"),
    "to": ("end", "end_date", "until", "before", "latest"),
    "limit": ("max", "count", "n", "rows", "page_size", "how_many"),
    "match": ("matchtype", "match_type", "scope", "how_much", "cover"),
    "filter": ("filters", "only", "where", "include"),
    "word": ("phrase", "keyword", "keywords", "term", "contains", "search_word"),
    # The film limb's own argument names, in the words a model reaches for. `project`/`out` are the
    # pair that matters: a small model says "file" or "path" for either of them, and the limb would
    # otherwise write its render over the project it was asked to read.
    "project": ("project_path", "project_file", "fcproj", "timeline", "sequence_file"),
    "out": ("output", "outfile", "output_file", "dest", "destination", "render_to", "save_as"),
    "command": ("command_id", "cmd", "cmd_id", "which_command", "operation"),
    "steps": ("commands", "script", "batch", "sequence_of_commands"),
    # `q` is the canonical key for the search limbs, so a film turn that says "q" would never reach
    # the film limb's `query`. Only used to FILL an empty canonical key, so nothing a caller gave is
    # ever overwritten.
    "query": ("q", "search", "filter", "term", "keywords", "words", "text", "about"),
}


_ESCAPE_PAYLOAD_KEYS = ("code", "source", "cmd", "lyrics", "style")
_ESCAPE_PAIRS = (("\\r\\n", "\n"), ("\\n", "\n"), ("\\r", "\r"), ("\\t", "\t"), ('\\"', '"'))
_NEWLINE_ESCAPES = ("\\n", "\\r", "\\t")


_PATHLIKE = re.compile(
    r"^[A-Za-z0-9_ .\-/\\:]+\.(?:py|pyw|txt|json|md|bat|cmd|ps1|sh|js|ts|go|rs|java|c|cc|cpp|h)$"
)


def repair_escapes(value: str) -> str:
    """Undo a small model's double-escaped payload: literal backslash-n where a newline belongs.

    Measured (gauntlet T8, 2026-09-22, qwen2.5-coder:7b): the model wrote the call inside its own
    JSON string as {"code": "def fibonacci(n):\\n    ..."} - the escape survived json.loads as a
    two-character sequence, so python_exec received a one-line program and Python refused it with
    "unexpected character after line continuation character". The program was right; only the
    escaping was wrong, and the operator was shown a syntax error instead of 144.

    Only executable payloads are repaired, and only when the value carries no real newline: in code,
    a literal backslash-n is never what the model meant. Text destined for a file is left alone,
    because there the sequence can be the content itself.
    """
    if not isinstance(value, str) or not value or "\n" in value:
        return value
    if _PATHLIKE.match(value.strip()):
        # A path is not an escaped program. On Windows "\notes" contains a literal backslash-n that
        # means "notes" and not a line break; repairing one inserted a newline into a filename
        # (measured while fixing T9's call shape).
        return value
    if not any(esc in value for esc in _NEWLINE_ESCAPES):
        return value
    sentinel = "\x00"
    out = value.replace("\\\\", sentinel)
    for esc, real in _ESCAPE_PAIRS:
        out = out.replace(esc, real)
    return out.replace(sentinel, "\\")


#: A bare host (optionally with a path) - a value, not prose. Used only for the archive limb,
#: whose documented target may be a domain with no scheme.
_HOSTLIKE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*\.[A-Za-z]{2,}(?:[/?#]\S*)?$")


def canonicalize(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Fill a limb's canonical argument names from the aliases a small model reaches for."""
    keys = CANON_KEYS.get(name)
    if not keys or not isinstance(args, dict):
        return name, args
    out = dict(args)
    # taken = keys already CONSUMED as an alias source. Pre-seeding it with every present key
    # skipped the alias itself ({"value": ...} never filled "code"), which is how this helper first
    # shipped broken. Canonical keys that already carry a value are skipped by the guard above.
    taken: set[str] = set()
    for key in keys:
        if str(out.get(key) or "").strip():
            continue
        for alt in ALIAS_POOL.get(key, ()):
            if alt in taken:
                continue
            val = out.get(alt)
            if isinstance(val, str) and val.strip():
                if key == "url" and not val.strip().lower().startswith("http"):
                    # The http rule exists to stop PROSE being read as an address (a model
                    # filling "url" with a sentence). The archive is the one door whose
                    # documented target is also a bare host - the harness synthesises the
                    # https:// itself, and "site": "chatagent.ca" is an address, not prose.
                    if name != "wayback" or not _HOSTLIKE_RE.match(val.strip()):
                        continue
                out[key] = val.strip()
                taken.add(alt)
                break
            if val not in (None, "", [], {}):
                out[key] = val
                taken.add(alt)
                break
    if name == "edit_file" and not str(out.get("old") or "").strip():
        # Measured 2026-09-18: asked to replace "old-value" with "agent-value", the model emitted
        # those words AS KEY NAMES. Two loose string args beside "path" are the replacement pair,
        # in the order the model wrote them - a mechanical fill, not an invention.
        loose = [k for k in out if k not in keys and str(out.get(k) or "").strip()]
        if loose:
            out["old"] = out[loose[0]]
            if len(loose) > 1:
                out["new"] = out[loose[1]]
    for pk in _ESCAPE_PAYLOAD_KEYS:
        if isinstance(out.get(pk), str):
            out[pk] = repair_escapes(out[pk])
    return name, out


def _tool_env() -> dict[str, str]:
    """The full environment, for compilers and interpreters.

    `_win_env()` narrows the environment on purpose - that is right for `shell`, which must not
    inherit whatever a parent happened to export. It is wrong for a toolchain: measured here, the very
    same `rustc -O` command that builds successfully with the inherited environment fails under the
    narrowed one with "linker `link.exe` not found", because the linker is found through a variable the
    narrow list drops. So compilers get the real environment; only shell gets the scrubbed one.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _win_env() -> dict[str, str]:
    keys = ("PATH", "SystemRoot", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERNAME", "USERPROFILE", "WINDIR")
    env = {k: os.environ[k] for k in keys if k in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _ws(p: str | None) -> Path:
    # under_workspace also accepts the redundant leading "workspace/" that models add by habit
    # (workspace/gauntlet_edit.txt used to resolve to workspace/workspace/gauntlet_edit.txt).
    return under_workspace(p or ".")


def _kill_tree(pid: int) -> None:
    """Kill a pid and everything it spawned.

    subprocess's own timeout handling kills only the direct child: `cmd.exe /c <exe>` leaves the
    real program (browser, installer) alive as an orphan, still holding files on the stick.
    taskkill /T walks the tree first, so a grandchild dies with its parent.
    """
    if not pid:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))], capture_output=True, timeout=20)
            return
        except Exception:
            pass
    try:
        os.kill(int(pid), signal.SIGTERM)
    except Exception:
        pass


def _run_capture(argv: list[str], *, timeout: int, cwd: str, env: dict[str, str]) -> tuple[int | None, str, str, bool]:
    """Run argv, capturing text output; on timeout kill the whole process TREE.

    Returns (returncode, stdout, stderr, timed_out). returncode is None when it timed out.
    """
    # errors="replace": a compiler may emit bytes the console's locale codec cannot decode, and a
    # UnicodeDecodeError here would kill the limb instead of reporting the program (seen live).
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            errors="replace", env=env)
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.communicate(timeout=10)
        except Exception:
            pass
        return None, "", "", True
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None and not stream.closed:
                    stream.close()
            except Exception:
                pass


RUST_DIR = WORKSPACE / "rust"


def _find_rust() -> tuple[str | None, str | None]:
    """(rustc, cargo) - PATH first, then the usual rustup install. (None, None) when absent."""
    import shutil  # local: this module must import with nothing but the stdlib

    def pick(name: str) -> str | None:
        got = shutil.which(name)
        if got:
            return got
        guess = Path.home() / ".cargo" / "bin" / (name + ".exe")
        return str(guess) if guess.is_file() else None

    return pick("rustc"), pick("cargo")


def _looks_like_a_linker_failure(text: str) -> bool:
    """Did the build fail at LINK time rather than in the code? Then a type-check is still useful."""
    low = (text or "").lower()
    return any(k in low for k in ("linker `link.exe` not found", "linking with `link.exe` failed",
                                  "link.exe", "extra operand", "linker", "link --help"))


_RUST_PROBE: dict[str, Any] = {}


def _is_real_linker(path: str) -> bool:
    """Is this `link` the Microsoft linker, or the coreutils hardlink tool git-bash ships?"""
    low = re.sub(r"/+", "/", str(path or "").replace("\\", "/").lower())
    if not low:
        return False
    return not ("/usr/bin/" in low or "git/usr" in low or "/msys" in low or "mingw64/usr" in low)


def _rust_probe(force: bool = False) -> dict[str, Any]:
    """Ask the toolchain whether it can really build AND run, instead of guessing from the PATH.

    Name-based guessing lies in both directions. git-bash ships a `link.exe` that is coreutils, so a
    machine looks equipped when it is not; and a machine with no `link.exe` on PATH at all can still
    link, because rustc ships its own linker (`rust-lld`), measured working here. So the probe builds a
    trivial program and runs it. Cached per process: that is what "can this machine build Rust" means
    once it has been answered.
    """
    import shutil as _sh
    import tempfile as _tf

    if _RUST_PROBE and not force:
        return _RUST_PROBE
    out: dict[str, Any] = {"can_run": False, "via": "", "detail": "", "stdout": ""}
    rustc = _sh.which("rustc")
    if not rustc:
        out["detail"] = "rustc is not on PATH"
        _RUST_PROBE.update(out)
        return out

    host = ""
    try:
        vv = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, errors="replace", timeout=60).stdout or ""
        m = re.search(r"host:\s*(\S+)", vv)
        host = m.group(1) if m else ""
    except Exception:
        pass
    lld = _sh.which("rust-lld") or _find_rust_lld(rustc, host)

    attempts = [("plain", [])]
    if lld:
        attempts.append(("rust-lld", ["-C", f"linker={lld}"]))
    with _tf.TemporaryDirectory(prefix="lygo-rust-probe-") as d:
        work = Path(d)
        (work / "probe.rs").write_text('fn main() { println!("{}", 6 * 7); }\n', encoding="utf-8")
        exe = work / "probe.exe"
        first_error = ""
        for label, extra in attempts:
            try:
                b = subprocess.run([rustc, "-O", *extra, "probe.rs", "-o", str(exe)], cwd=str(d),
                                   capture_output=True, text=True, timeout=300)
            except Exception as exc:
                first_error = first_error or f"{type(exc).__name__}"
                continue
            if b.returncode == 0 and exe.is_file():
                try:
                    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
                except Exception as exc:
                    first_error = first_error or f"run: {type(exc).__name__}"
                    continue
                out.update({"can_run": run.returncode == 0, "via": label,
                            "stdout": (run.stdout or "").strip()[:40],
                            "detail": f"built and ran a probe program ({label})"})
                _RUST_PROBE.update(out)
                return out
            first_error = first_error or " ".join((b.stderr or b.stdout or "").strip().splitlines()[-1:])[:200]
        try:
            c = subprocess.run([rustc, "--emit=metadata", "probe.rs", "-o", str(work / "probe.rmeta")],
                               cwd=str(d), capture_output=True, text=True, timeout=300)
            out["checked"] = c.returncode == 0
        except Exception:
            out["checked"] = False
        out["detail"] = (first_error or "no linker worked") + (" - the program type-checks" if out.get("checked") else "")
    _RUST_PROBE.update(out)
    return out


def _find_rust_lld(rustc: str = "", host: str = "") -> str:
    """Find the linker Rust ships. `~/.cargo/bin/rustc` is a rustup shim, so its neighbours are not
    the toolchain: ask rustup, then look in the real rustlib. Measured: rust-lld alone can link a
    Windows program here, which is what makes the Rust limb work on a machine with no MSVC linker."""
    import shutil as _sh

    hit = _sh.which("rust-lld")
    if hit:
        return hit
    real = ""
    try:
        got = subprocess.run(["rustup", "which", "rustc"], capture_output=True, text=True, errors="replace", timeout=60)
        if got.returncode == 0:
            real = (got.stdout or "").strip().splitlines()[0] if got.stdout.strip() else ""
    except Exception:
        real = ""
    roots = []
    if real:
        roots.append(Path(real).parent.parent / "lib" / "rustlib")
    if rustc:
        roots.append(Path(rustc).parent.parent / "lib" / "rustlib")
    roots.append(Path.home() / ".rustup" / "toolchains")
    for root in roots:
        for pat in ([f"{host}/bin/rust-lld.exe"] if host else []) + ["*/bin/rust-lld.exe", "*/lib/rustlib/*/bin/rust-lld.exe"]:
            for cand in root.glob(pat):
                if cand.is_file():
                    return str(cand)
    return ""


def _find_real_linker() -> str:
    """The Microsoft linker by path, ignoring anything git-bash put on PATH under the same name.

    Measured: in a clean PATH `rustc` links fine here; in a git-bash PATH it is handed coreutils'
    `link.exe` and fails. `rust-lld` alone is not enough (no MSVC import libraries), so the fix is to
    name the real linker explicitly.
    """
    import shutil as _sh

    for name in ("link", "lld-link"):
        got = _sh.which(name) or ""
        if got and _is_real_linker(got):
            return got
    for root in (Path(r"C:\Program Files\Microsoft Visual Studio"),
                 Path(r"C:\Program Files (x86)\Microsoft Visual Studio")):
        if root.is_dir():
            for cand in root.glob("*/*/VC/Tools/MSVC/*/bin/Host*/x64/link.exe"):
                if cand.is_file():
                    return str(cand)
    return ""


def _rust_link_args(srcf: Path, binp: Path, rustc: str) -> list[str]:
    """How to build a single-file program here, decided by measurement rather than by name.

    plain rustc if that links; otherwise the linker Rust ships; otherwise a metadata-only check. The
    old version asked `_rust_linker_ok()` and, when it said no, never tried the linker that works.
    """
    probe = _rust_probe()
    if probe.get("can_run") and probe.get("via") == "rust-lld":
        lld = _find_rust_lld(rustc, "")
        if lld:
            return [rustc, "-O", "-C", f"linker={lld}", str(srcf), "-o", str(binp)]
    if probe.get("can_run"):
        return [rustc, "-O", str(srcf), "-o", str(binp)]
    real = _find_real_linker()
    if real:
        return [rustc, "-O", "-C", f"linker={real}", str(srcf), "-o", str(binp)]
    lld = _find_rust_lld(rustc, _rust_host(rustc))
    if lld:
        return [rustc, "-O", "-C", f"linker={lld}", str(srcf), "-o", str(binp)]
    return [rustc, "--emit=metadata", "--crate-type=bin", str(srcf), "-o", str(srcf.with_suffix(".rmeta"))]


def _rust_host(rustc: str = "") -> str:
    """The host triple, for finding the right rustlib directory."""
    try:
        vv = subprocess.run([rustc or "rustc", "-vV"], capture_output=True, text=True, errors="replace", timeout=60).stdout or ""
        m = re.search(r"host:\s*(\S+)", vv)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _rust_linker_ok() -> bool:
    """Can rustc actually link here? Measured, not guessed."""
    return bool(_rust_probe().get("can_run"))


# --- the Internet Archive limb (Δ9Φ963-LYGO-WAYBACK-HARNESS-v1) ---------------------------------
# THE VOCABULARY IS A MODEL'S, THE RULES ARE THE HARNESS'S. A small model asks for "snapshots",
# "history", "the old page" or "is it archived" long before it asks for an action name, and a limb
# that refuses a call it can serve teaches nothing. Every alias below lands on one of the doors in
# src/wayback.py, and the harness keeps the honesty rules: which timestamp format is accepted, what
# each door costs, what "closest" is allowed to mean, and which answers are conclusions.
_WAYBACK_ACTIONS: dict[str, str] = {
    "available": "available", "availability": "available", "is": "available", "archived": "available",
    "check": "available", "exists": "available", "have": "available", "any": "available",
    "closest": "closest", "nearest": "closest", "as_of": "closest", "asof": "closest",
    "captures": "captures", "list": "captures", "snapshots": "captures", "history": "captures",
    "dates": "captures", "all": "captures", "when": "captures",
    "site": "site", "urls": "site", "inventory": "site", "map": "site", "everything": "site",
    "find": "find", "search": "find", "grep": "find", "matching": "find",
    "timemap": "timemap", "memento": "timemap", "links": "timemap", "timeline": "timemap",
    "read": "read", "fetch": "read", "text": "read", "see": "read", "open": "read", "old": "read",
    "quote": "read", "snapshot": "read", "get": "read",
    "link": "link", "url": "link", "build": "link",
    "pages": "pages", "count": "pages",
    "live": "live", "alive": "live", "dead": "live", "rot": "live",
}
_WAYBACK_ACTIONS_HELP = ("available (is it archived and which capture is closest), closest (the "
                         "capture nearest a date), captures (the dated list for one URL or a whole "
                         "site), site (which pages of a site are archived), find (a word inside a "
                         "site's archived URLs), timemap (the whole history as memento rows), read "
                         "(the archived copy as TEXT - for a dead link or a 404), link (build the "
                         "snapshot URL), pages (how many pages of index rows exist), live (is the "
                         "URL answering on the live web right now)")
#: What a small model may read in one call. The full rows stay in the harness answer; this is the
#: shaping, and it SAYS it shaped, because a silent cut reads as "that is all there was".
_WAYBACK_MODEL_ROWS = 12
_WAYBACK_MODEL_CHARS = 6000


def _wayback_for_model(out: dict[str, Any]) -> dict[str, Any]:
    """Cap rows and text for the local brain, and report the cap instead of hiding it."""
    if not isinstance(out, dict):
        return {"ok": False, "error": "wayback_bad_answer"}
    shaped = dict(out)
    rows = shaped.get("rows")
    if isinstance(rows, list) and len(rows) > _WAYBACK_MODEL_ROWS:
        shaped["rows"] = rows[:_WAYBACK_MODEL_ROWS]
        shaped["rows_shown"] = _WAYBACK_MODEL_ROWS
        shaped["rows_note"] = (f"showing the first {_WAYBACK_MODEL_ROWS} of {len(rows)} rows; "
                               f"narrow with from/to, match or a filter, or read it in the panel")
    text = shaped.get("text")
    if isinstance(text, str) and len(text) > _WAYBACK_MODEL_CHARS:
        shaped["text"] = text[:_WAYBACK_MODEL_CHARS]
        shaped["text_note"] = f"cut at {_WAYBACK_MODEL_CHARS} of {len(text)} characters"
    return shaped


def _wayback_limb(wb: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Intent -> the door in src/wayback.py, then a shape the local brain can read."""
    action = str(args.get("action") or args.get("mode") or "available").strip().lower()
    action = action.replace("-", "_").replace(" ", "_")
    action = _WAYBACK_ACTIONS.get(action, action)
    url = str(args.get("url") or "").strip()
    ts = str(args.get("timestamp") or args.get("date") or "").strip()
    frm = str(args.get("from") or args.get("from_") or "").strip()
    to = str(args.get("to") or "").strip()
    limit = args.get("limit") or 40
    match = str(args.get("match") or "").strip()
    filt = args.get("filter") or ""
    word = str(args.get("word") or args.get("phrase") or "").strip()
    raw = bool(args.get("raw"))
    try:
        chars = int(args.get("chars") or _WAYBACK_MODEL_CHARS)
    except (TypeError, ValueError):
        chars = _WAYBACK_MODEL_CHARS
    try:
        if action == "available":
            out = wb.available(url, ts)
        elif action == "closest":
            out = wb.closest(url, ts)
        elif action == "captures":
            out = wb.captures(url, limit=limit, match=match, filter=filt, from_=frm, to=to)
        elif action == "site":
            out = wb.inventory(url, limit=limit, filter=filt)
        elif action == "find":
            out = wb.find(url, word, limit=limit)
        elif action == "timemap":
            out = wb.timemap(url, limit=limit, from_=frm, to=to)
        elif action == "read":
            out = wb.read(url, ts, raw=raw, chars=max(500, min(chars, _WAYBACK_MODEL_CHARS)))
        elif action == "link":
            out = wb.link(ts or "2", url, raw=raw)
        elif action == "pages":
            out = wb.pages(url, match=match)
        elif action == "live":
            out = wb.live_status(url)
        else:
            return {"ok": False, "error": "unknown_action", "action": action,
                    "class": "RESOURCE",
                    "why": f"{action!r} is not one of the archive actions",
                    "actions": list(dict.fromkeys(_WAYBACK_ACTIONS.values())),
                    "hint": _WAYBACK_ACTIONS_HELP}
    except Exception as exc:  # noqa: BLE001 - a limb that raises takes the turn down with it
        return {"ok": False, "error": "wayback_client_error", "class": "RESOURCE",
                "why": f"{type(exc).__name__}: {str(exc)[:160]}",
                "hint": "the archive harness refused the call before it reached the network"}
    return _wayback_for_model(out)


def _auton_limb(args: dict[str, Any]) -> dict[str, Any]:
    """The durable-goal face of `auton` for the console agent.

    Thin on purpose: it opens the same store the CLI opens and calls the same contract module, so a
    goal created here is the identical row a `python tools/auton.py dispatch` run would claim. The
    runtime itself is a package (`src/auton`), not this function - the console must not become the
    only place a goal can live.
    """
    from auton import contract as contract_mod
    from auton.config import RuntimeConfig

    action = str(args.get("action") or "").strip()
    db = str(args.get("db") or (WORKSPACE / "auton" / "runtime.db"))
    # The operator's runtime config, if one exists, is what a dispatch from the console obeys: the
    # stale-progress window and lease have to be able to cover a long tool call (a three-shot video
    # render is ~17 minutes, well past the 15-minute default).
    cfg_path = Path(str(args.get("config") or (WORKSPACE / "auton" / "runtime.json")))
    cfg = RuntimeConfig.load(cfg_path if cfg_path.is_file() else None).with_db(db)
    # A dispatch from inside the console must be able to reach a model: the spawned worker cannot
    # inherit this process's callable, so it shells out to the kit's own turn adapter. Without this
    # the queue would claim the task and fail it for want of a turn command.
    if not cfg.turn_cmd:
        cfg.turn_cmd = f'"{sys.executable}" "{KIT_ROOT / "tools" / "auton_turn.py"}"'
    from auton.store import Store

    store = Store(cfg.sqlite_path)
    try:
        if action == "goal_add":
            text = str(args.get("text") or args.get("objective") or "").strip()
            if not text:
                return {"ok": False, "error": "text_required",
                        "why": "give the objective in `text` - it is what the worker reads every turn"}
            checks = args.get("checks")
            if isinstance(checks, str):
                try:
                    checks = json.loads(checks)
                except ValueError:
                    return {"ok": False, "error": "checks_not_json", "why": checks[:200]}
            if checks:
                raw = {"objective": text, "checks": checks}
            elif args.get("contract"):
                raw = args["contract"] if isinstance(args["contract"], dict) else json.loads(args["contract"])
            else:
                return {"ok": False, "error": "checks_required",
                        "why": "a goal without an observable check cannot be judged, so it could only "
                               "ever be 'blocked'. Give checks, e.g. [{\"type\": \"file_exists\", "
                               "\"path\": \"...\"}]"}
            errors = contract_mod.validate_contract(raw)
            if errors:
                return {"ok": False, "error": "contract_invalid", "errors": errors}
            budget = cfg.budgets.as_task_budget()
            if args.get("max_turns"):
                budget["max_turns"] = int(args["max_turns"])
            parent = {"cwd": str(Path(str(args["cwd"])).resolve())} if args.get("cwd") else {}
            goal_id, task_id = contract_mod.create_goal(
                store, text, raw, budget=budget, payload=parent,
                defaults=cfg.budgets.as_task_budget(),
            )
            return {"ok": True, "action": action, "goal": goal_id, "task": task_id,
                    "checks": len(raw.get("checks") or []),
                    "next": "run action=dispatch to start it; the Judge decides when it is done"}
        if action == "goal_list":
            goals = store.list_goals(status=str(args.get("status") or "") or None)
            return {"ok": True, "action": action, "n": len(goals),
                    "goals": [{"id": g.id, "status": g.status, "turns": g.used_turns,
                               "text": g.text[:160]} for g in goals[:40]]}
        if action in {"goal_show", "goal_status"}:
            gid = str(args.get("id") or args.get("goal") or "").strip()
            goal = store.get_goal(gid) if gid else None
            if goal is None:
                return {"ok": False, "error": "no_such_goal", "why": gid or "(no id given)"}
            tasks = store.list_tasks(goal_id=goal.id)
            return {"ok": True, "action": action, "goal": {
                "id": goal.id, "text": goal.text, "status": goal.status,
                "used_turns": goal.used_turns, "budget": goal.budget,
                "contract": goal.contract},
                "tasks": [{"id": t.id, "status": t.status, "attempt": t.attempt_count,
                           "note": t.progress_note} for t in tasks],
                "steps": [{"seq": s.seq, "kind": s.kind} for s in store.list_steps(tasks[0].id)][-12:] if tasks else []}
        if action == "goal_cancel":
            gid = str(args.get("id") or args.get("goal") or "").strip()
            goal = store.get_goal(gid) if gid else None
            if goal is None:
                return {"ok": False, "error": "no_such_goal", "why": gid or "(no id given)"}
            stopped = []
            for task in store.list_tasks(goal_id=goal.id):
                if task.status not in {"done", "failed", "blocked", "cancelled"}:
                    store.transition(task.id, task.status, "cancelled", result={"reason": "operator"})
                    stopped.append(task.id)
            return {"ok": True, "action": action, "goal": goal.id, "cancelled_tasks": stopped}
        if action == "dispatch":
            from auton.dispatcher import dispatcher_loop

            # 900 s was too short for real work: a three-shot video is ~15 min and a six-shot film is
            # over half an hour, so every dispatch died mid-task and the worker had to be reaped and
            # resumed. The window is now the operator's call up to two hours; a finished queue returns
            # immediately, so a long window costs nothing when the work is short.
            seconds = max(5.0, min(7200.0, float(args.get("seconds") or 60)))
            stats = dispatcher_loop(cfg, store=store, duration_s=seconds)
            return {"ok": True, "action": action, "seconds": seconds,
                    "stats": stats.__dict__ if hasattr(stats, "__dict__") else str(stats)}
        if action in {"status", "events"}:
            goals = store.list_goals()
            counts: dict[str, int] = {}
            for g in goals:
                counts[g.status] = counts.get(g.status, 0) + 1
            events = store.read_unconsumed_events(limit=50)
            return {"ok": True, "action": action, "db": str(store.path),
                    "goals": counts,
                    "tasks": {s: len(store.list_tasks(status=s)) for s in
                              ("queued", "claimed", "running", "blocked", "done", "failed")},
                    "unread_events": [{"kind": e.kind, "task": e.task_id} for e in events]}
        return {"ok": False, "error": "unknown_action", "action": action,
                "why": "use goal_add, goal_list, goal_show, goal_cancel, dispatch, status or events"}
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001 - a close that fails must not mask the real result
            pass


def _music_video_limb(args: dict[str, Any]) -> dict[str, Any]:
    """`tools/music_video.py` as a limb, so a long render can run inside ONE tool call.

    This is why it is a limb and not `shell`: the shell limb caps at 25 s and `python_exec` at 120 s,
    while a three-shot render is ~17 minutes. A limb call is executed directly by the worker with the
    lease heartbeat still running, which is the one place a long job can live.
    """
    import importlib.util

    path = KIT_ROOT / "tools" / "music_video.py"
    spec = importlib.util.spec_from_file_location("kit_music_video", path)
    if spec is None or spec.loader is None:
        return {"ok": False, "error": "music_video_unavailable", "why": str(path)}
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    action = str(args.get("action") or "director").strip()
    if action == "director":
        out_dir = Path(str(args.get("out_dir") or (WORKSPACE / "video" / "untitled")))
        out_dir.mkdir(parents=True, exist_ok=True)
        scenes = list(args.get("scene") or [])
        if args.get("scenes_file"):
            scenes = mod.scenes_from_file(Path(str(args["scenes_file"]))) or scenes
        if not scenes:
            return {"ok": False, "error": "scenes_required",
                    "why": "give scenes_file (one camera setup per line) or scene=[...]"}
        seconds = float(args.get("seconds") or 0.0)
        if not seconds and args.get("song"):
            seconds = mod.audio_duration(str(args["song"]))
        if not seconds:
            seconds = 210.0
        bpm = float(args.get("bpm") or 0.0)
        if not bpm and args.get("detect_bpm") and args.get("song"):
            bpm = mod.bpm_from_audio(str(args["song"]))
        plan = mod.make_plan(str(args.get("brief") or ""), scenes, seconds, bpm,
                             int(args.get("beats_per_cut") or 8), int(args.get("length") or 0), 0)
        plan_path = out_dir / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        summary = {"ok": True, "action": action, "plan": str(plan_path),
                   "shots": len(plan["shots"]), "shot_s": plan["shot_s"],
                   "seconds_of_film": round(len(plan["shots"]) * plan["shot_s"], 1), "bpm": bpm}
        if args.get("plan_only"):
            return summary
        res = mod.render_plan(plan_path, limit=int(args.get("limit") or 0))
        summary["rendered"] = res.get("rendered", res.get("shots_ok"))
        if not res.get("ok"):
            summary.update({"ok": False, "error": "render_failed", "detail": res})
            return summary
        if args.get("limit"):
            return summary
        film = out_dir / "film.mp4"
        mod.assemble(plan_path, film, transition=str(args.get("transition") or "cut"),
                     fade_s=float(args.get("fade") or 0.5), audio=str(args.get("song") or ""),
                     upscale=str(args.get("upscale") or "1280x720"))
        probe = None
        try:
            probe = round(mod.ffprobe_duration(film), 2)
        except Exception:  # noqa: BLE001
            probe = None
        summary.update({"film": str(film), "bytes": film.stat().st_size if film.is_file() else 0,
                        "duration_s": probe})
        return summary
    if action == "plan":
        scenes = list(args.get("scene") or [])
        if args.get("scenes_file"):
            scenes = mod.scenes_from_file(Path(str(args["scenes_file"]))) or scenes
        seconds = float(args.get("seconds") or 0.0) or 210.0
        plan = mod.make_plan(str(args.get("brief") or ""), scenes or [str(args.get("brief") or "")],
                             seconds, float(args.get("bpm") or 0.0),
                             int(args.get("beats_per_cut") or 8), int(args.get("length") or 0), 0)
        out = Path(str(args.get("plan") or (WORKSPACE / "video" / "plan.json")))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "action": action, "plan": str(out), "shots": len(plan["shots"]),
                "shot_s": plan["shot_s"]}
    if action == "render":
        plan_path = Path(str(args.get("plan") or ""))
        if not plan_path.is_file():
            return {"ok": False, "error": "no_such_plan", "why": str(plan_path)}
        return {"ok": True, "action": action, "result": mod.render_plan(
            plan_path, limit=int(args.get("limit") or 0))}
    if action == "assemble":
        plan_path = Path(str(args.get("plan") or ""))
        out = Path(str(args.get("out") or (plan_path.parent / "film.mp4")))
        if not plan_path.is_file():
            return {"ok": False, "error": "no_such_plan", "why": str(plan_path)}
        mod.assemble(plan_path, out, transition=str(args.get("transition") or "cut"),
                     fade_s=float(args.get("fade") or 0.5), audio=str(args.get("song") or ""),
                     upscale=str(args.get("upscale") or "1280x720"))
        return {"ok": True, "action": action, "film": str(out),
                "bytes": out.stat().st_size if out.is_file() else 0}
    if action == "status":
        plan_path = Path(str(args.get("plan") or ""))
        if not plan_path.is_file():
            return {"ok": False, "error": "no_such_plan", "why": str(plan_path)}
        return {"ok": True, "action": action, "result": mod.status(plan_path)}
    return {"ok": False, "error": "unknown_action", "action": action,
            "why": "use director, plan, render, assemble or status"}


def extra(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    name, args = canonicalize(name, args or {})
    if name == "dispatcher":
        import dispatcher

        return dispatcher.handle(str(args.get("action") or "status"), args)
    if name in {"swarm_status", "swarm_ask", "swarm_advise", "swarm_dispatch", "swarm_pulse"}:
        import swarm

        action = name.replace("swarm_", "") if name != "swarm_status" else "status"
        return swarm.handle(action, args)
    if name == "auton":
        return _auton_limb(args)
    if name == "music_video":
        return _music_video_limb(args)
    if name == "web_search":
        from web_tools import web_search

        return web_search(str(args.get("q") or args.get("query") or ""))
    if name == "web_fetch":
        from web_tools import web_fetch

        return web_fetch(str(args.get("url") or ""))
    if name == "shell":
        cmd = str(args.get("cmd") or args.get("command") or "")
        if not cmd.strip():
            return {"ok": False, "error": "empty"}
        if _SHELL_DENY.search(cmd) or gate_prompt(cmd).get("verdict") == "QUARANTINE":
            return {"ok": False, "error": "p0_blocked"}
        if re.search(r"[|&><`$]", cmd):
            return {"ok": False, "error": "metachar"}
        try:
            code, out, err, timed_out = _run_capture(
                ["cmd.exe", "/c", cmd], timeout=25, cwd=str(WORKSPACE), env=_win_env()
            )
        except OSError as exc:
            return {"ok": False, "error": f"spawn_failed:{type(exc).__name__}"}
        if timed_out:
            return {"ok": False, "error": "timeout"}
        return {"ok": code == 0, "code": code, "stdout": out[-8000:], "stderr": err[-4000:]}
    if name == "rust_exec":
        code = str(args.get("code") or args.get("source") or "")
        crate = str(args.get("crate") or "").strip()
        rustc, cargo = _find_rust()
        if not rustc and not cargo:
            # fail soft and say where it looked: an absent toolchain is not a bug in the turn
            return {"ok": False, "error": "rust_missing",
                    "hint": "no rustc or cargo on PATH or in %USERPROFILE%\\.cargo\\bin - install from rustup.rs",
                    "checked": ["PATH", str(Path.home() / ".cargo" / "bin")]}
        if code.strip() and (_SHELL_DENY.search(code) or gate_prompt(code).get("verdict") == "QUARANTINE"):
            return {"ok": False, "error": "p0_blocked"}
        seconds = max(1, min(300, int(args.get("timeout") or 90)))
        RUST_DIR.mkdir(parents=True, exist_ok=True)
        if crate:
            proj = _ws("rust/" + crate)
            if not (proj / "Cargo.toml").is_file():
                if not code.strip():
                    return {"ok": False, "error": "crate_missing", "path": str(proj),
                            "hint": "no Cargo.toml there: pass code to create the crate, or the name of one that exists"}
                (proj / "src").mkdir(parents=True, exist_ok=True)
                pkg = re.sub(r"[^a-z0-9_]", "_", crate.lower())[:40] or "crate"
                (proj / "Cargo.toml").write_text(
                    f'[package]\nname = "{pkg}"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8")
                (proj / "src" / "main.rs").write_text(code, encoding="utf-8")
            if not cargo:
                return {"ok": False, "error": "cargo_missing", "hint": "cargo is needed for a crate; pass code for a single file"}
            # without a linker, `cargo check` is the honest thing to run: full type-check, real
            # diagnostics, and it never pretends to have executed the program
            argv = [cargo, "run", "--quiet"] + (["--release"] if args.get("release") else [])
            cwd, binp, srcf = proj, None, proj / "src" / "main.rs"
        else:
            if not code.strip():
                return {"ok": False, "error": "empty",
                        "hint": "rust_exec needs 'code' (a single-file program) or 'crate' (a project folder)"}
            if not rustc:
                return {"ok": False, "error": "rustc_missing", "hint": "cargo is present but rustc is not"}
            slug = re.sub(r"[^a-z0-9_]", "_", str(args.get("name") or "main").lower())[:40] or "main"
            cwd = _ws("rust/" + slug)
            cwd.mkdir(parents=True, exist_ok=True)
            srcf = cwd / "main.rs"
            srcf.write_text(code, encoding="utf-8")
            binp = cwd / (slug + ".exe")
            argv = _rust_link_args(srcf, binp, rustc)
            metadata_only = "--emit=metadata" in " ".join(argv)
        try:
            rc, out, err, timed_out = _run_capture(argv, timeout=seconds, cwd=str(cwd), env=_tool_env())
        except OSError as exc:
            return {"ok": False, "error": f"spawn_failed:{type(exc).__name__}"}
        if timed_out:
            return {"ok": False, "error": "timeout", "timed_out": True, "stderr": err[-2000:]}
        if crate and rc != 0 and _looks_like_a_linker_failure(err):
            try:
                rc4, _o4, err4, _t4 = _run_capture([cargo, "check", "--quiet"],
                                                 timeout=seconds, cwd=str(cwd), env=_tool_env())
            except OSError as exc:
                return {"ok": False, "error": f"spawn_failed:{type(exc).__name__}"}
            if rc4 == 0:
                return {"ok": True, "code": 0, "ran": False, "checked": True, "stdout": "",
                        "stderr": err4[-2000:], "timed_out": False, "via": "cargo", "crate": crate,
                        "project": str(cwd),
                        "note": "the crate type-checks, but this machine has no usable linker, so "
                                "it was not built or run. VS Build Tools (C++) or the "
                                "x86_64-pc-windows-gnu target is what enables that.",
                        "linker": err[-1200:]}
            err = err4 or err
        if crate:
            return {"ok": rc == 0, "code": rc, "stdout": out[-8000:], "stderr": err[-4000:],
                    "timed_out": False, "via": "cargo", "project": str(cwd), "crate": crate,
                    "checked": True, "ran": True, "built": bool(_rust_probe().get("can_run"))}
        if rc != 0 and _looks_like_a_linker_failure(err) and "-C" not in argv:
            real = _find_real_linker()
            if real:
                retry = [rustc, "-O", "-C", f"linker={real}", str(srcf), "-o", str(binp)]
                rc_r, out_r, err_r, _to_r = _run_capture(retry, timeout=seconds, cwd=str(cwd), env=_tool_env())
                if rc_r == 0:
                    argv, rc, out, err, metadata_only = retry, rc_r, out_r, err_r, False
        if rc != 0 and _looks_like_a_linker_failure(err):
            # A machine can have rustc and still no usable linker. Do not call that a broken
            # program: type-check it and say precisely what is missing.
            try:
                rc3, _o3, err3, _t3 = _run_capture(
                    [rustc, "--emit=metadata", "--crate-type=bin", str(srcf), "-o", str(cwd / "main.rmeta")],
                    timeout=seconds, cwd=str(cwd), env=_tool_env())
            except OSError as exc:
                return {"ok": False, "error": f"spawn_failed:{type(exc).__name__}"}
            if rc3 == 0:
                return {"ok": True, "code": 0, "ran": False, "checked": True, "stdout": "",
                        "stderr": err3[-2000:], "timed_out": False, "via": "rustc", "file": str(srcf),
                        "note": "the program type-checks, but this machine has no usable linker "
                                "(link.exe missing, or present but not MSVC's), so it was neither "
                                "built nor run. Install VS Build Tools with the C++ workload, or "
                                "`rustup target add x86_64-pc-windows-gnu`, to run Rust programs.",
                        "linker": err[-1200:]}
            err = err3 or err
        if metadata_only and rc == 0:
            return {"ok": True, "code": 0, "ran": False, "checked": True, "stdout": "", "stderr": err[-4000:],
                    "timed_out": False, "via": "rustc", "file": str(srcf),
                    "note": "no linker on this machine (link.exe/gcc absent), so the program was "
                            "type-checked but not built or run. Install VS Build Tools with the C++ "
                            "option, or `rustup target add x86_64-pc-windows-gnu`, to run it.",
                    "compiler": (out + err)[-1500:]}
        if rc != 0:
            # the compiler's own diagnostics, verbatim: that is what makes a Rust limb useful
            return {"ok": False, "error": "compile_failed", "code": rc, "stderr": err[-6000:],
                    "stdout": out[-2000:], "file": str(srcf)}
        try:
            rc2, out2, err2, to2 = _run_capture([str(binp)], timeout=seconds, cwd=str(cwd), env=_tool_env())
        except OSError as exc:
            return {"ok": False, "error": f"spawn_failed:{type(exc).__name__}"}
        if to2:
            return {"ok": False, "error": "timeout", "timed_out": True, "stderr": err2[-2000:]}
        return {"ok": rc2 == 0, "code": rc2, "stdout": out2[-8000:], "stderr": err2[-4000:],
                "timed_out": False, "via": "rustc", "file": str(srcf), "binary": str(binp),
                "ran": True, "checked": True,
                "compiler": (out + err)[-1500:]}
    if name == "save_note":
        fname = re.sub(r'[\\/:*?"<>|]', "_", str(args.get("name") or "").strip())[:120]
        if not fname:
            return {"ok": False, "error": "no_name", "hint": "give the file a name, e.g. note.txt"}
        body = str(args.get("text") or "")
        where = str(args.get("where") or "workspace").strip()
        if where.lower() in ("workspace", "ws", ""):
            target = _ws("notes")
        else:
            target = user_paths.resolve(where)
        if target is None:
            return {"ok": False, "error": "unknown_place", "asked_for": where,
                    "known": user_paths.folder_map(),
                    "hint": "say desktop/documents/downloads/home/workspace, or an absolute folder that exists"}
        inside = str(target).lower().startswith(str(WORKSPACE).lower())
        # Measured (gauntlet T7): the operator said "create it on my desktop", the model called this limb
        # with where=desktop, and the limb refused for want of a consent flag - so a task that was asked for
        # in plain words failed on our own gate. The four operator folders ARE the operator's own space;
        # any other path outside the workspace still needs consent.
        # the four folders the operator names by hand - NOT their whole profile, and not an arbitrary
        # subfolder of it: a path they did not name still needs the flag.
        named = {str(user_paths.known_folder(k)).lower()
                 for k in ("desktop", "documents", "downloads", "pictures") if user_paths.known_folder(k)}
        ours = str(target).lower() in named
        if not inside and not ours and not args.get("consent"):
            return {"ok": False, "error": "consent_required", "path": str(target),
                    "hint": "writing outside the workspace needs consent=true"}
        dest = target / fname
        try:
            target.mkdir(parents=True, exist_ok=True)
            data = body.encode("utf-8")
            dest.write_bytes(data)
            # the receipt is earned, not asserted: read it back and report what is really on disk
            back = dest.read_bytes()
            digest = hashlib.sha256(back).hexdigest()[:12]
        except OSError as exc:
            return {"ok": False, "error": f"write_failed:{type(exc).__name__}", "path": str(dest),
                    "detail": str(exc)[:200]}
        return {"ok": True, "path": str(dest.resolve()), "folder": str(target.resolve()), "name": fname,
                "bytes": len(back), "sha256_12": digest, "verified": back == data and dest.is_file(),
                "receipt": f"wrote {len(back)} bytes to {dest.resolve()}"}

    if name == "portal_status":
        import json as _json

        out: dict[str, Any] = {"ok": True, "at": _time.strftime("%Y-%m-%d %H:%M:%S"), "gaps": []}
        # every number is asked of its owner; nothing is recomputed here (the meminfo rule)
        for key, limb, args in (("tasks", "task_list", {}), ("crons", "cron_list", {}),
                                ("keeper", "keeper_status", {})):
            try:
                got = extra(limb, dict(args))
                out[key] = got if isinstance(got, dict) else {"raw": str(got)[:200]}
            except Exception as exc:  # a gap is NAMED, never folded into green
                out["gaps"].append(f"{key}: {type(exc).__name__}")
                out[key] = None
        tasks = out.get("tasks") or {}
        items = tasks.get("tasks") if isinstance(tasks, dict) else None
        if isinstance(items, list):
            counts: dict[str, int] = {}
            for t in items:
                state = str((t or {}).get("state") or "unknown")
                counts[state] = counts.get(state, 0) + 1
            out["counts"] = counts
            out["recent"] = [{k: (t or {}).get(k) for k in ("id", "state", "target", "seconds", "note")}
                             for t in items[-5:]]
        report = WORKSPACE.parent / "docs" / ("GAUNTLET_" + _time.strftime("%Y-%m-%d") + ".md")
        out["gauntlet"] = str(report) if report.is_file() else "not run today"
        try:
            blob = _json.loads((WORKSPACE.parent / "save" / "gauntlet"
                                / (_time.strftime("%Y-%m-%d") + ".json")).read_text(encoding="utf-8"))
            out["gauntlet_score"] = blob.get("verdict", {}).get("passed"), blob.get("verdict", {}).get("tasks")
        except Exception:
            out["gauntlet_score"] = None
        try:
            arts = sorted((WORKSPACE / "notes").glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)[:5]
            out["written_lately"] = [f"{f.name} ({f.stat().st_size}B)" for f in arts if f.is_file()]
        except OSError as exc:
            out["gaps"].append(f"written_lately: {type(exc).__name__}")
        out["summary"] = (f"{sum((out.get('counts') or {}).values())} tasks · "
                          f"done {(out.get('counts') or {}).get('done', 0)} · "
                          f"failed {(out.get('counts') or {}).get('failed', 0)} · "
                          + ("keeper alive" if (out.get("keeper") or {}).get("keeper_alive") else "keeper unknown")
                          + (f" · gauntlet {out['gauntlet_score'][0]}/{out['gauntlet_score'][1]}"
                             if out.get("gauntlet_score") and out["gauntlet_score"][1] else ""))
        return out

    if name == "python_exec":
        code = str(args.get("code") or "")
        _interp = str(args.get("interpreter") or sys.executable or "python")
        _secs = max(1, min(120, int(args.get("timeout") or 20)))
        _where = _ws(str(args.get("cwd"))) if args.get("cwd") else WORKSPACE
        if not _where.is_dir():
            # Measured (gauntlet T9, 2026-09-22): the model passed cwd=<workspace>/scripts, which does
            # not exist, and the spawn died as spawn_failed:FileNotFoundError - the operator was told
            # "the specified directory does not exist" instead of getting an answer. A wrong folder is
            # not a reason to lose the turn; run it in the workspace and the receipt shows where.
            _where = WORKSPACE
        if not code.strip():
            # name the missing argument: a bare "empty" told the operator nothing and the model
            # then answered from its own arithmetic (measured: {"value": "print(6*7)"} -> 42).
            return {"ok": False, "error": "empty", "hint": "python_exec needs 'code' - a python snippet that prints its result", "got_keys": sorted(args)}
        _run_file: Path | None = None
        if "\n" not in code and re.fullmatch(r"[A-Za-z0-9_ .\-/\\:]+\.py", code.strip()):
            # Measured (gauntlet T9, 2026-09-22): the model put the *name* of the program it meant to
            # write into 'code' ({"code": "sum_of_numbers.py"}) and the filename went to python as
            # source. Run the file when it exists; when it does not, say what to pass rather than
            # executing a name and reporting the result of nothing.
            _cand = _ws(code.strip())
            if _cand.is_file():
                _run_file = _cand
            else:
                return {"ok": False, "error": "filename_not_code",
                        "hint": "python_exec takes the program TEXT in 'code' (or the path of a file "
                                f"that already exists). Nothing was found at {code.strip()!r} - write "
                                "the program first, then run it.",
                        "got": code[:120], "cwd": str(_where)}
        _argv = ([_interp, str(_run_file)] if _run_file else [_interp, "-c", code]) + [
            str(a) for a in (args.get("argv") or [])
        ]
        if _SHELL_DENY.search(code) or gate_prompt(code).get("verdict") == "QUARANTINE":
            return {"ok": False, "error": "p0_blocked"}
        # Measured 2026-09-21 (gauntlet T12): the agent saved mod_a.py with save_note and then the
        # interpreter could not import it, because the folders it writes code into were not on sys.path.
        # Code the agent saves must be importable when the agent runs code.
        _penv = _tool_env()
        _penv["PYTHONPATH"] = os.pathsep.join(
            [str(WORKSPACE), str(WORKSPACE / "notes"), str(WORKSPACE / "python"), str(_where)]
        )
        try:
            code, out, err, timed_out = _run_capture(
                _argv, timeout=_secs, cwd=str(_where), env=_penv
            )
        except OSError as exc:
            return {"ok": False, "error": f"spawn_failed:{type(exc).__name__}"}
        if timed_out:
            return {"ok": False, "error": "timeout"}
        return {"ok": code == 0, "code": code, "stdout": out[-8000:], "stderr": err[-4000:],
                "timed_out": False, "interpreter": Path(_interp).name, "cwd": str(_where),
                "ran": str(_run_file) if _run_file else "code"}
    if name == "now":
        n = dt.datetime.now().astimezone()
        u = dt.datetime.now(dt.timezone.utc)
        return {"ok": True, "iso": n.isoformat(), "utc": u.isoformat(), "unix": int(n.timestamp()), "tz": str(n.tzinfo), "weekday": n.strftime("%A")}
    if name == "world_pulse":
        from world_clock import pulse

        return pulse()
    if name == "memory_recall":
        q = str(args.get("q") or "").lower()
        hits = []
        if MEM_PATH.is_file():
            for line in MEM_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]:
                if q in line.lower():
                    hits.append(line[:500])
                if len(hits) >= 20:
                    break
        return {"ok": True, "hits": hits}
    if name == "download_url":
        from web_tools import _blocked, _get

        url = str(args.get("url") or "")
        why = _blocked(url)
        if why:
            return {"ok": False, "error": why}
        dest = _ws(args.get("path") or Path(url).name or "download.bin")
        try:
            dest.relative_to(WORKSPACE.resolve())
        except ValueError:
            dest = WORKSPACE / dest.name
        code, raw, _ = _get(url)
        if code != 200:
            return {"ok": False, "error": f"http_{code}"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        return {"ok": True, "path": str(dest), "bytes": len(raw)}
    if name == "glob_files":
        from admin_map import is_admin
        from tools import dispatch as _dispatch

        pat = str(args.get("pattern") or "*")
        if is_admin() and (":" in pat or any(ch in pat for ch in "\\/")):
            return _dispatch("find_files", {"pattern": Path(pat).name, "root": str(Path(pat).parent)})
        if is_admin():
            return _dispatch("find_files", {"pattern": pat})
        hits = [str(p.relative_to(WORKSPACE)) for p in WORKSPACE.glob(pat) if p.is_file()][:80]
        return {"ok": True, "hits": hits}
    if name == "todo_add":
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        with TODO_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"item": str(args.get("item") or "")[:500]}) + "\n")
        return {"ok": True}
    if name == "todo_list":
        items = []
        if TODO_PATH.is_file():
            for line in TODO_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-80:]:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    items.append({"item": line})
        return {"ok": True, "items": items}
    if name == "calc":
        expr = str(args.get("expr") or "")
        try:
            val = _safe_arith(expr)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "value": val}
    if name == "whoami":
        from admin_map import brief, is_admin

        from runtime_facts import facts as _facts

        b = brief()
        f = _facts()
        return {
            "ok": True,
            "mark": "LYGO",
            "role": b.get("role"),
            "seat": "LYRA-Δ9 architect seat on this console",
            "steward": b.get("steward"),
            "kit": str(KIT_ROOT),
            "workspace": str(WORKSPACE),
            "portal": "https://chatagent.ca/lygo-llm-console.html",
            "model": f.get("model"),
            "engine": f.get("engine"),
            "engine_port": f.get("engine_port"),
            "brain": f.get("brain"),
            "brain_label": f.get("brain_label"),
            "build": f.get("build"),
            "n_limbs": f.get("n_limbs"),
            "github": b.get("github_org"),
            "huggingface": b.get("hf_org"),
            "links": {
                "github_org": b.get("github_org"),
                "hf_org": b.get("hf_org"),
                "github_repos": b.get("github_repos"),
                "sites": b.get("sites"),
                "lattice": b.get("lattice"),
            }
            if is_admin()
            else {},
        }
    if name == "hash_text":
        t = str(args.get("text") or "").encode("utf-8")
        return {"ok": True, "sha256": hashlib.sha256(t).hexdigest(), "n": len(t)}
    if name == "memory_append":
        from continuity import append_memory

        return append_memory(str(args.get("note") or args.get("text") or ""))
    if name == "memory_read":
        from continuity import memory_path, ensure_identity

        ensure_identity()
        p = memory_path()
        return {"ok": True, "path": str(p), "text": p.read_text(encoding="utf-8", errors="replace")[-8000:] if p.is_file() else ""}
    if name == "soul_read":
        from continuity import soul_path, ensure_identity

        ensure_identity()
        p = soul_path()
        return {"ok": True, "path": str(p), "text": p.read_text(encoding="utf-8", errors="replace")[:8000] if p.is_file() else ""}
    if name == "identity_read":
        from continuity import identity_path, ensure_identity

        ensure_identity()
        p = identity_path()
        return {"ok": True, "path": str(p), "text": p.read_text(encoding="utf-8", errors="replace")[:8000] if p.is_file() else ""}
    if name == "edit_file":
        p = _ws(str(args.get("path") or ""))
        try:
            p.resolve().relative_to(WORKSPACE.resolve())
        except ValueError:
            return {"ok": False, "error": "denied"}
        if not p.is_file():
            return {"ok": False, "error": "missing"}
        old, new = str(args.get("old") or ""), str(args.get("new") or "")
        if not old:
            # "" is a substring of every string, so the old_not_found guard below could never fire:
            # a call that named neither "old" nor "new" replaced nothing and still returned ok:True.
            return {"ok": False, "error": "empty",
                    "hint": "edit_file needs 'old' (the exact text to replace) and 'new'",
                    "got_keys": sorted(args)}
        t = p.read_text(encoding="utf-8", errors="replace")
        if old not in t:
            return {"ok": False, "error": "old_not_found",
                    "hint": "'old' does not appear in this file - read the file first",
                    "head": t[:200]}
        atomicio.atomic_write_text(p, t.replace(old, new, 1))
        return {"ok": True, "path": str(p), "replaced": 1, "occurrences": t.count(old),
                "bytes": len(t) - len(old) + len(new)}
    if name == "weather":
        from web_tools import _get, _json_get

        place = str(args.get("place") or "Earth")
        # wttr.in answers a browser-like agent with its HTML page even when Accept says text/plain,
        # so read the JSON form (format=j1) and render one line ourselves.
        data = _json_get("https://wttr.in/" + urllib.parse.quote(place) + "?format=j1")
        cur = {}
        if isinstance(data, dict):
            rows = data.get("current_condition") or []
            if rows and isinstance(rows[0], dict):
                cur = rows[0]
        if cur:
            desc = ""
            wd = cur.get("weatherDesc") or []
            if wd and isinstance(wd[0], dict):
                desc = str(wd[0].get("value") or "").strip()
            bits = [
                "{}C".format(cur.get("temp_C")),
                desc,
                "feels {}C".format(cur.get("FeelsLikeC")),
                "wind {}km/h".format(cur.get("windspeedKmph")),
                "humidity {}%".format(cur.get("humidity")),
            ]
            text = place + ": " + ", ".join([b for b in bits if b and "None" not in b])
            return {"ok": True, "text": text, "class": "RESOURCE"}
        code, raw, _ = _get(
            "https://wttr.in/" + urllib.parse.quote(place) + "?format=3",
            headers={"Accept": "text/plain", "User-Agent": "curl/8.4.0"},
        )
        if code != 200:
            return {"ok": False, "error": f"http_{code}"}
        text = raw.decode("utf-8", errors="replace").strip()
        if "<html" in text[:400].lower():
            return {"ok": False, "error": "html_page_not_weather"}
        return {"ok": True, "text": text, "class": "RESOURCE"}
    if name == "geocode":
        from web_tools import _json_get

        qs = urllib.parse.urlencode({"q": str(args.get("place") or ""), "format": "json", "limit": "3"})
        data = _json_get("https://nominatim.openstreetmap.org/search?" + qs)
        if not data:
            return {"ok": False, "error": "none"}
        rows = [{"name": x.get("display_name"), "lat": x.get("lat"), "lon": x.get("lon")} for x in data[:3]]
        return {"ok": True, "results": rows, "class": "RESOURCE"}
    if name == "http_json":
        from web_tools import _blocked, _json_get

        url = str(args.get("url") or "")
        why = _blocked(url)
        if why:
            return {"ok": False, "error": why}
        data = _json_get(url)
        if data is None:
            return {"ok": False, "error": "not_json"}
        blob = json.dumps(data, default=str)
        return {"ok": True, "data": blob[:12000]}
    if name == "wayback":
        # Δ9Φ963-LYGO-WAYBACK-HARNESS-v1 - the Internet Archive. The harness (src/wayback.py) owns
        # the rules: which doors exist, what each costs, what is refused, and which door is allowed
        # to answer "which capture is closest to this date". This branch maps an INTENT onto a door,
        # so a small model never has to know an endpoint or a CDX parameter.
        import wayback as wb

        return _wayback_limb(wb, args)
    if name == "arxiv_search":
        from web_tools import _get
        import xml.etree.ElementTree as ET

        q = str(args.get("q") or "").strip()
        # Date-sorted results are precise only if the phrase is exact; an unquoted multi-word q
        # matches any paper sharing one word, so quote it before it goes to arXiv.
        if " " in q and '"' not in q:
            q = '"' + q + '"'
        qs = urllib.parse.quote(q)
        # arXiv's default order is relevance, which surfaced 2003-2016 papers as "newest"; ask for
        # submission-date order explicitly.
        url = (
            "https://export.arxiv.org/api/query?search_query=all:"
            + qs
            + "&start=0&max_results=5&sortBy=submittedDate&sortOrder=descending"
        )
        code, raw, _ = _get(url)
        if code != 200:
            return {"ok": False, "error": f"http_{code}"}
        papers = []
        try:
            root = ET.fromstring(raw)
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for ent in root.findall("a:entry", ns)[:5]:
                title = (ent.findtext("a:title", default="", namespaces=ns) or "").strip()
                link = ""
                for l in ent.findall("a:link", ns):
                    if l.get("type") == "text/html":
                        link = l.get("href") or ""
                papers.append({"title": title, "url": link})
        except ET.ParseError:
            return {"ok": False, "error": "xml"}
        return {"ok": True, "papers": papers, "class": "RESOURCE"}
    if name == "hn_search":
        from web_tools import hn_search

        return {"ok": True, "hits": hn_search(str(args.get("q") or "")), "class": "RESOURCE"}
    if name == "github_search":
        from admin_map import is_admin
        from web_tools import github_search

        q = str(args.get("q") or "")
        if is_admin() and "user:" not in q.lower() and "org:" not in q.lower():
            scoped = github_search("user:DeepSeekOracle " + q)
            if scoped:
                return {"ok": True, "hits": scoped, "query": "user:DeepSeekOracle " + q, "scope": "steward", "class": "RESOURCE"}
            # An empty answer is worse than a broad one: the steward-scoped search matched nothing,
            # so search GitHub generally and say plainly that the scope was widened.
            return {"ok": True, "hits": github_search(q), "query": q, "scope": "global_fallback", "class": "RESOURCE"}
        return {"ok": True, "hits": github_search(q), "query": q, "scope": "global", "class": "RESOURCE"}
    if name == "image_info":
        from image_tools import image_info

        return image_info(str(args.get("path") or ""))
    if name == "image_see":
        from image_tools import image_see

        return image_see(str(args.get("path") or ""), args.get("prompt"))
    if name == "image_save":
        from image_tools import image_save

        return image_save(str(args.get("b64") or args.get("data") or ""), args.get("path"))
    if name == "image_list":
        from image_tools import image_list

        return image_list()
    if name == "page_thumbnail":
        from image_tools import page_thumbnail

        return page_thumbnail(str(args.get("url") or ""))
    if name == "subagent_run":
        from delegate_tools import subagent_run

        return subagent_run(
            task=str(args.get("task") or args.get("prompt") or ""),
            engine=str(args.get("engine") or ""),
            dir=str(args.get("dir") or ""),
            timeout=int(args.get("timeout") or 300),
            read_only=bool(args.get("read_only", True)),
            consent=bool(args.get("consent") or False),
        )
    if name == "subagent_pool":
        from delegate_tools import subagent_pool

        return subagent_pool(
            probe=bool(args.get("probe") or False),
            engines=str(args.get("engines") or ""),
            timeout=int(args.get("timeout") or 90),
        )
    if name == "mesh_status":
        from mesh_tools import mesh_status

        return mesh_status()
    if name == "mesh_from_image":
        from mesh_tools import mesh_from_image

        return mesh_from_image(
            str(args.get("path") or ""),
            args.get("resolution"),
            args.get("remove_bg", True),
            args.get("timeout"),
            args.get("foreground"),
        )
    if name == "mesh_decimate":
        from mesh_tools import mesh_decimate

        return mesh_decimate(str(args.get("name") or ""), args.get("ratio"), args.get("timeout"))
    if name == "photo_to_3d":
        from photo_to_3d import photo_to_3d as run_photo_to_3d

        return run_photo_to_3d(str(args.get("path") or ""), True, str(args.get("engine") or "WORKBENCH"),
                              args.get("resolution"), args.get("foreground"), args.get("timeout"))
    if name == "blender_render":
        from blend_tools import render

        return render(str(args.get("path") or ""), str(args.get("out") or ""), str(args.get("engine") or "WORKBENCH"))
    if name == "image_generate":
        from media_tools import image_generate, sd_model

        import render_swap

        def _draw(model_override=None):
            model = str(args.get("model") or "")
            if model_override is not None and not model:
                model = str(model_override)
            return image_generate(
                str(args.get("prompt") or ""),
                str(args.get("negative") or ""),
                args.get("width"),
                args.get("height"),
                args.get("steps"),
                args.get("cfg_scale"),
                model,
                bool(args.get("cpu")),
                args.get("timeout"),
            )

        if bool(args.get("cpu")):
            return _draw()          # the operator asked for the CPU: leave the brain alone
        # A render is minutes on the CPU and seconds on the card, and the card is only busy
        # because the chat model is sitting on it. Borrow it, then hand it straight back.
        #
        # QUALITY FIRST - the operator's explicit call. Render the DECLARED checkpoint; when the
        # card cannot hold its weights, the render goes to the CPU and takes minutes, and that
        # is the accepted cost. Substituting a smaller checkpoint to buy seconds silently halves
        # the resolution, which is a worse outcome than waiting. Set
        # config/console.json "image_prefer": "speed" to take the faster path instead.
        if render_swap.prefer_speed():
            # Ask for the SMALLEST declared checkpoint's worth of room, then take the largest
            # the freed card can hold - parking against a size the card can never reach fails
            # its own test, restores the brain, and leaves every render on the CPU.
            _cands = render_swap.checkpoints()
            _need = render_swap.need_for(_cands[0]) if _cands else render_swap.need_for(sd_model())
            with render_swap.borrowed_card(_need):
                return _draw(render_swap.best_checkpoint(sd_model()))
        # Size the park from the checkpoint this render will ACTUALLY open. Quality mode used to
        # size it from the default, so a request naming a bigger file could be told the machine was
        # already free and then be handed to the CPU anyway.
        _want = None
        try:
            from pathlib import Path as _P
            _m = str(args.get("model") or "").strip()
            if _m:
                _cand = _P(_m)
                _want = _cand if _cand.is_file() else None
        except Exception:
            _want = None
        with render_swap.borrowed_card(render_swap.need_for(_want or sd_model())):
            return _draw()
    if name == "sound_speak":
        from media_tools import sound_speak

        return sound_speak(
            str(args.get("text") or ""),
            str(args.get("voice") or ""),
            args.get("length_scale"),
            args.get("timeout"),
        )
    if name == "music_generate":
        from music_tools import music_generate

        return music_generate(
            str(args.get("style") or ""),
            str(args.get("lyrics") or ""),
            args.get("seconds"),
            str(args.get("model") or ""),
            args.get("seed"),
            args.get("profile"),
            args.get("timeout"),
        )
    if name == "video_generate":
        from video_harness import render as render_clip, weights_state

        import render_swap

        # `seconds` is what the operator thinks in; the engine wants a frame count, and the
        # recipe snaps it to a legal 4n+1 value. 24 fps is the recipe's own rate.
        length = args.get("length") or 0
        if not length and args.get("seconds"):
            try:
                length = int(round(float(args["seconds"]) * 24))
            except (TypeError, ValueError):
                length = 0

        def _clip():
            return render_clip(
                str(args.get("model") or ""),
                prompt=str(args.get("prompt") or ""),
                width=args.get("width") or 0,
                height=args.get("height") or 0,
                length=length,
                steps=args.get("steps") or 0,
                seed=args.get("seed") or 0,
                timeout_s=args.get("timeout"),
            )

        # A clip needs BOTH resources: ~13.5 GB of weights in RAM and its unet resident on the
        # card. A "shrink the chat model" park frees RAM but leaves a small model ON the card, and
        # MEASURED 2026-10-10 that stole enough VRAM to push ComfyUI into CPU offload - a clip that
        # takes 4.5 min was still running at 13+ with the card 770 MiB short. So pass the recipe's
        # resident need as well: `_room_now` then tests the card too, and the shrink (which only
        # helps RAM) correctly falls through to the stop, which frees everything.
        try:
            from video_harness import MODELS as _VMODELS
            from video_harness import weights_state as _wstate
            # The engine resolves an empty id to the FIRST recipe; do the same, or the floor
            # silently computes to 0 and the render never parks.
            _mid = str(args.get("model") or "").strip() or next(iter(_VMODELS))
            _recipe = _VMODELS.get(_mid) or {}
            # NOT `_ws`: that name is a module-level helper in this file and `extra()` calls it
            # (save_note, download_url, ...). Assigning it here made it LOCAL for the whole of
            # extra(), so every earlier `_ws(...)` call in this function raised UnboundLocalError
            # and save_note stopped working entirely (measured 2026-10-10: 6 suite errors).
            _weights = _wstate(_mid)
            _sizes = [int(f.get("bytes") or 0) // (1024 * 1024)
                      for f in (_weights.get("files") or [])]
            _vram_need = int(_recipe.get("vram_mib") or 0)
        except Exception as exc:
            _sizes, _vram_need = [], 0
            # A floor of 0 with no log is the "decline: room already" trap: the park is skipped and
            # the render fights the chat model for memory with nothing recorded. Log the failure.
            render_swap._dbg("video park: weight lookup failed (%s) - floor computed as 0"
                             % (type(exc).__name__,))
        _ram_floor = render_swap.need_ram_for(_sizes)
        with render_swap.borrowed_card(_vram_need, _ram_floor):
            return _clip()
    if name == "media_status":
        from media_tools import media_status

        return media_status()
    if name == "jina_fetch":
        from web_tools import jina_fetch

        return jina_fetch(str(args.get("url") or ""))
    if name == "sessions_list":
        from paths import SAVE

        d = SAVE / "sessions"
        names = [p.name for p in d.glob("*.json")] if d.is_dir() else []
        # The record of truth lives beside the session snapshots: one journal per session, plus the
        # sealed archive. Listing only *.json would hide exactly the conversations worth finding.
        journals = sorted(p.name for p in d.glob("journal-*.jsonl")) if d.is_dir() else []
        out: dict[str, Any] = {"ok": True, "files": names, "journals": journals}
        try:
            import compaction

            st = compaction.status()
            out["sealed"] = st.get("sessions_sealed")
            out["sealed_bytes"] = st.get("sealed_bytes")
            out["session_id"] = st.get("session_id")
        except Exception:  # noqa: BLE001
            pass
        return out
    if name in {"session_list", "session_catalog"}:
        import sessions

        out = sessions.safe_catalog(int(args.get("limit") or 50), str(args.get("q") or ""),
                                    str(args.get("tag") or ""), str(args.get("month") or ""))
        if not out.get("ok"):
            return out
        rows = out.get("sessions") or []
        return {
            "ok": True, "vault": out.get("vault"), "count": out.get("count"), "shown": len(rows),
            "turns": out.get("turns"), "tags": out.get("tags"),
            "sessions": [{"sid": r.get("sid"), "title": r.get("title"), "turns": r.get("turns"),
                          "started": str(r.get("created_iso") or "")[:16], "tags": r.get("tags") or [],
                          "note": r.get("note") or "", "pinned": bool(r.get("pinned")),
                          "folder": r.get("folder")} for r in rows],
        }
    if name == "session_open":
        import sessions

        out = sessions.safe_open(str(args.get("sid") or args.get("id") or ""),
                                 int(args.get("chars") or 12000))
        if out.get("ok"):
            out.pop("messages", None)  # the transcript is the readable form; this would double it
        return out
    if name == "session_search":
        import sessions

        return sessions.safe_search(str(args.get("q") or args.get("query") or ""),
                                    int(args.get("k") or 6))
    if name == "session_label":
        import sessions

        sid = str(args.get("sid") or args.get("id") or "")
        kw: dict = {}
        if args.get("title") is not None:
            kw["title"] = str(args["title"])
        if args.get("note") is not None:
            kw["note"] = str(args["note"])
        if args.get("tags") is not None:
            tags = args["tags"]
            kw["tags"] = (tags if isinstance(tags, list)
                          else [p.strip() for p in str(tags).split(",") if p.strip()])
        if args.get("pinned") is not None:
            kw["pinned"] = bool(args["pinned"])
        if not sid:
            filed = sessions.safe_vault("manual")  # no id: file the conversation in progress
            sid = str(filed.get("sid") or "")
            if not sid:
                return filed
        return sessions.safe_label(sid, **kw)
    if name == "session_resume":
        import sessions

        out = sessions.safe_resume(str(args.get("sid") or args.get("id") or ""))
        if out.get("ok"):
            out.pop("messages", None)  # the console reads those from its own session file
        return out
    if name == "recall_history":
        import compaction

        q = str(args.get("q") or args.get("query") or args.get("term") or "")
        try:
            k = int(args.get("k") or args.get("limit") or 6)
        except (TypeError, ValueError):
            k = 6
        return compaction.recall(q, k=k)
    if name == "self_test":
        return _self_test(str(args.get("target") or ""), args.get("timeout") or 900)
    if name == "self_seal":
        return _self_seal(bool(args.get("consent")), bool(args.get("dry_run")))
    if name == "self_restart":
        return _self_restart(bool(args.get("consent")), args.get("wait") or 0)
    if name == "toolchain_install":
        return _toolchain_install(str(args.get("what") or ""), str(args.get("package") or ""), bool(args.get("consent")))
    if name == "task_add":
        import tasking

        got = tasking.add(str(args.get("target") or ""), args.get("args") or {},
                          deadline=args.get("deadline") or 900, note=str(args.get("note") or ""),
                          origin="agent")
        if got.get("ok"):
            got["hint"] = "queued - answer the user now, and read it later with task_list"
        return got
    if name == "task_list":
        import tasking

        return tasking.list_tasks(args.get("limit") or 20, str(args.get("state") or ""))
    if name == "task_show":
        import tasking

        return tasking.show(str(args.get("id") or ""))
    if name == "task_cancel":
        import tasking

        return tasking.cancel(str(args.get("id") or ""))
    if name == "task_health":
        import tasking

        return tasking.health()
    if name == "task_recover":
        import tasking

        return tasking.watch()
    if name == "cron_add":
        import crons

        return crons.add_job(str(args.get("name") or ""), str(args.get("limb") or ""), args.get("args") or {},
                             every=args.get("every") or 0, at=str(args.get("at") or ""),
                             note=str(args.get("note") or ""))
    if name == "cron_list":
        import crons

        return crons.list_jobs()
    if name == "cron_remove":
        import crons

        return crons.remove_job(str(args.get("name") or ""))
    if name == "keeper_status":
        return _keeper_status()
    if name == "rea":
        return _rea(str(args.get("action") or args.get("op") or "doctor").strip().lower(),
                    str(args.get("path") or "").strip())
    if name == "plexo":
        # The download harness: LYGO's own parallel-range engine and the bridge to the
        # operator's Plexo. One action per call, a dict back, nothing raised, and the same
        # file the console's own buttons call - so a button and the agent cannot disagree.
        import plexo_tools

        return plexo_tools.plexo_limb(args)
    if name == "film":
        # FilmCraft's harness. One action per call, a dict back, nothing raised - and every call
        # goes through the same file the console's own buttons use, so the button and the agent can
        # never disagree about what happened.
        import film_tools

        return film_tools.film_limb(args)
    return None


# ------------------------------------------------------------------ self-build harness
# The console can read and edit its own source; these four let it check, seal, restart and equip
# itself. Every limb that changes the system refuses without consent=true and says why, mirroring
# the --i-consent rule the kit's own scripts already follow.

_TC_ACTIONS = ("check", "rust_gnu_target", "pip", "winget")


def _kit_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _consent_refused(what: str, hint: str) -> dict[str, Any]:
    return {"ok": False, "error": "consent_required",
            "hint": f"{what} Call it again with consent=true to approve. {hint}"}


def _self_test(target: str = "", timeout: int = 900) -> dict[str, Any]:
    """Run the kit's own pytest and report real numbers. Read-only: it never writes the tree."""
    import time as _time

    root = _kit_root()
    tests = (root / "tests").resolve()
    try:
        limit = max(30, min(1800, int(timeout)))
    except (TypeError, ValueError):
        limit = 900
    args = [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "--tb=line"]
    if target:
        cand = Path(target)
        if not cand.is_absolute():
            cand = root / cand
        try:
            cand = cand.resolve()
        except OSError:
            return {"ok": False, "error": "bad_target", "target": target}
        if not str(cand).lower().startswith(str(tests).lower()):
            return {"ok": False, "error": "outside_tests",
                    "hint": "self_test only runs the kit's own tests/ - pass e.g. tests/test_tool_routing.py"}
        args.append(str(cand))
    t0 = _time.time()
    code, out, err, timed_out = _run_capture(args, timeout=limit, cwd=str(root), env=_win_env())
    blob = (out or "") + "\n" + (err or "")
    m_pass = re.search(r"(\d+) passed", blob)
    m_fail = re.search(r"(\d+) failed", blob)
    m_sub = re.search(r"(\d+) subtests? passed", blob)
    failures = [ln.split("::")[-1].strip() for ln in blob.splitlines() if ln.startswith("FAILED")][:6]
    return {"ok": (not timed_out) and m_fail is None,
            "passed": int(m_pass.group(1)) if m_pass else 0,
            "failed": int(m_fail.group(1)) if m_fail else 0,
            "subtests": int(m_sub.group(1)) if m_sub else 0,
            "seconds": round(_time.time() - t0, 1), "timed_out": timed_out, "failures": failures,
            "target": target or "fast core set", "command": " ".join(args[1:]),
            "tail": " ".join(blob.strip().splitlines()[-1:])[:200] if blob.strip() else ""}


def _self_seal(consent: bool = False, dry_run: bool = False) -> dict[str, Any]:
    if not consent:
        return _consent_refused("Sealing rewrites this kit's own manifests (refresh + certify).",
                                "Certification is the operator's signature on a build, so it never happens by itself.")
    root = _kit_root()
    steps = ["scripts/refresh_manifests.py", "scripts/certify_build.py"]
    if dry_run:
        return {"ok": True, "ran": False, "would_run": [f"{sys.executable} {s}" for s in steps]}
    report = []
    for step in steps:
        code, out, err, timed_out = _run_capture([sys.executable, step], timeout=900, cwd=str(root), env=_win_env())
        blob = (out or "") + "\n" + (err or "")
        last = " ".join(blob.strip().splitlines()[-1:])[:200] if blob.strip() else ""
        report.append({"step": step, "code": code, "last": last, "timed_out": timed_out})
    verdict = report[-1]["last"] if report else ""
    return {"ok": "CERTIFIED BUILD" in verdict or "already correct" in json.dumps(report), "steps": report,
            "verdict": verdict,
            "next": "self_restart with consent=true loads the sealed build into the running console"}


def _self_restart(consent: bool = False, wait: int = 0) -> dict[str, Any]:
    if not consent:
        return _consent_refused("Restarting drops the page for about a minute while the model reloads.",
                                "Seal first, then restart - an unsealed change would be certified by nobody.")
    import time as _time
    import urllib.request as _url

    root = _kit_root()
    port = 9641
    try:
        cfg = json.loads((root / "config" / "console.json").read_text(encoding="utf-8"))
        port = int(cfg.get("port") or cfg.get("console_port") or port)
    except Exception:
        pass
    doorbell = port - 1
    tokf = root / "data" / ".lygo_doorbell_token"
    tok = tokf.read_text(encoding="utf-8").strip() if tokf.is_file() else ""
    if not tok:
        return {"ok": False, "error": "no_doorbell_token", "doorbell_port": doorbell,
                "hint": "start the console through LYGO_LLM_CONSOLE.bat so it writes data/.lygo_doorbell_token"}
    try:
        with _url.urlopen(f"http://127.0.0.1:{doorbell}/boot?token={tok}", timeout=90) as fh:
            body = fh.read(300).decode("utf-8", "replace")
    except Exception as exc:
        return {"ok": False, "error": "doorbell_down", "doorbell_port": doorbell,
                "detail": type(exc).__name__,
                "hint": "the doorbell is not listening - start it with tools/doorbell.py --detach, or the launcher"}
    if wait:
        _time.sleep(max(0, min(600, int(wait))))
    return {"ok": True, "rang": True, "doorbell_port": doorbell, "answer": body[:200],
            "note": "the console is relaunching itself; the page returns in about a minute"}


def _toolchain_install(what: str = "", package: str = "", consent: bool = False) -> dict[str, Any]:
    import shutil as _shutil
    import subprocess as _sub

    what = (what or "").strip().lower()
    pkg = (package or "").strip()
    if what not in _TC_ACTIONS:
        return {"ok": False, "error": "unknown_action", "hint": f"what must be one of: {', '.join(_TC_ACTIONS)}",
                "note": "this limb runs a known action, never a command line composed for it"}
    if what in ("pip", "winget") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,64}", pkg or ""):
        return {"ok": False, "error": "bad_package",
                "hint": "package must match [A-Za-z0-9][A-Za-z0-9._+-]* - no spaces, no flags, no shell"}
    if what == "check":
        found = {}
        for name in ("rustc", "cargo", "rustup", "link", "gcc", "cc", "clang", "ld", "winget", "cl"):
            where = _shutil.which(name)
            if where:
                found[name] = where
        host, rustc_out = "", ""
        if found.get("rustc"):
            r = _sub.run(["rustc", "-vV"], capture_output=True, text=True, timeout=120)
            rustc_out = (r.stdout or "")[:400]
            m = re.search(r"host:\s*(\S+)", rustc_out)
            host = m.group(1) if m else ""
        # Ask the toolchain, not the PATH: git-bash's `link.exe` is coreutils and answers to the name.
        fake = [n for n in ("link", "ld", "cc", "clang", "gcc") if found.get(n) and not _is_real_linker(found[n])]
        probe = _rust_probe()
        note = (f"Rust builds and runs here - a probe program was compiled and run via {probe.get('via')}."
                if probe.get("can_run") else
                f"Rust cannot link here yet: {probe.get('detail') or 'no working linker'}. "
                "what=rust_gnu_target adds a user-space linker target; VS Build Tools (C++ option) is the other way.")
        return {"ok": True, "ran": True, "found": found, "host": host, "rustc": rustc_out,
                "linker_present": bool(probe.get("can_run")), "rust_builds_and_runs": bool(probe.get("can_run")),
                "probe": probe, "not_a_linker": {n: found[n] for n in fake},
                "checked_but_not_run": bool(probe.get("checked")) and not probe.get("can_run"),
                "note": note}
    command = {"rust_gnu_target": ["rustup", "target", "add", "x86_64-pc-windows-gnu"],
               "pip": [sys.executable, "-m", "pip", "install", pkg],
               "winget": ["winget", "install", "--id", pkg, "--accept-source-agreements",
                          "--accept-package-agreements"]}[what]
    printable = " ".join(command)
    installs = {"rust_gnu_target": "a user-space MinGW-w64 linker through rustup - no administrator needed",
                "pip": f"the Python package {pkg} into {sys.executable}",
                "winget": f"the package {pkg} through winget - Windows may raise a UAC prompt"}[what]
    if not consent:
        return {"ok": True, "ran": False, "command": printable, "action": what, "installs": installs,
                "note": "plan only: call again with consent=true to run it, or run it yourself"}
    if not (_shutil.which(command[0]) or Path(command[0]).is_file()):
        return {"ok": False, "error": "missing_installer", "command": printable,
                "hint": f"{command[0]} was not found on PATH"}
    code, out, err, timed_out = _run_capture(command, timeout=900, cwd=str(_kit_root()), env=_win_env())
    return {"ok": code == 0 and not timed_out, "ran": True, "command": printable, "code": code,
            "timed_out": timed_out, "stdout": (out or "")[-800:], "stderr": (err or "")[-800:],
            "next": "run toolchain_install with what=check to confirm; then rust_exec can build and run"}


def _keeper_status() -> dict[str, Any]:
    """Ask the keeper what it is doing. Reads its state file; never starts it as a side effect."""
    import sys as _sys

    tools_dir = str(_kit_root() / "tools")
    if tools_dir not in _sys.path:
        _sys.path.insert(0, tools_dir)
    try:
        import keeper as _keeper

        st = _keeper.status()
    except Exception as exc:
        return {"ok": False, "error": "keeper_unavailable", "detail": type(exc).__name__,
                "hint": "run python tools/keeper.py --detach to supervise this console"}
    state = st.get("state") or {}
    return {"ok": True, "keeper_alive": st.get("keeper_alive"), "keeper_pid": st.get("keeper_pid"),
            "console_up": (st.get("console") or {}).get("up"),
            "doorbell_up": (st.get("doorbell") or {}).get("up"),
            "sweeps": state.get("sweeps"), "last_ring": state.get("last_ring"), "actions": state.get("actions"),
            "interval_s": st.get("interval_s"), "log": st.get("log"),
            "hint": "start it with: python tools/keeper.py --detach"}


def _rea_bin() -> str | None:
    """Where the REA CLI is: config first, then PATH, then npm's own shim directory.

    Resolved by NAME, never by a hardcoded drive letter or user path - this kit is copied to a USB
    stick and to the web edition, and a path that exists on this box does not exist there. The npm
    fallback is %APPDATA%-relative, so it travels with the USER instead of naming a drive. The
    config door is `rea_bin` in config/console.json, or in config/local.json which wins over it.
    """
    try:
        from paths import console_cfg

        pinned = str((console_cfg() or {}).get("rea_bin") or "").strip()
    except Exception:                                    # a config that cannot be read is not a crash
        pinned = ""
    if pinned and Path(pinned).is_file():
        return str(Path(pinned))
    names = ("rea.cmd", "rea.exe", "rea")
    for folder in (os.environ.get("PATH") or "").split(os.pathsep):
        if not folder:
            continue
        for n in names:
            cand = Path(folder) / n
            if cand.is_file():
                return str(cand)
    appdata = os.environ.get("APPDATA")
    if appdata:
        for n in names:
            cand = Path(appdata) / "npm" / n
            if cand.is_file():
                return str(cand)
    return None


def _rea(action: str, target: str) -> dict[str, Any]:
    """Run one REA action as a one-shot external service.

    One-shot ON PURPOSE: `_run_capture` kills the whole process TREE on timeout, so nothing is left
    listening and no orphan is left holding a file - the law for any limb whose engine is an external
    process. Output is bounded because REA's own envelopes can run to megabytes, and a limb that
    floods the window costs more than the answer is worth.
    """
    exe = _rea_bin()
    if not exe:
        return {"ok": False, "error": "rea_missing",
                "hint": "npm install --global rea-agents, or set rea_bin in config/local.json"}
    if action in ("doctor", "capabilities"):
        argv = [exe, action, "--format", "json"]
    elif action in ("analyze", "decompile"):
        if not target:
            return {"ok": False, "error": "target_required", "action": action,
                    "hint": f"give path: the app, bundle or binary to {action}"}
        argv = [exe, action, target, "--format", "json"]
    else:
        return {"ok": False, "error": "unknown_action", "action": action,
                "hint": "action is doctor, capabilities, analyze or decompile"}
    try:
        code, out, err, timed_out = _run_capture(argv, timeout=900, cwd=str(WORKSPACE), env=_tool_env())
    except OSError as exc:
        return {"ok": False, "error": f"spawn_failed:{type(exc).__name__}", "detail": str(exc)[:200]}
    if timed_out:
        return {"ok": False, "error": "timeout", "action": action,
                "hint": "the analysis did not finish within 900s"}
    # A NON-ZERO EXIT IS NOT ALWAYS A FAILED LIMB. `rea doctor` deliberately exits non-zero when the
    # environment is not fully healthy - that IS its answer, not an error, and reading the status code
    # alone reported a working tool as broken (measured on the first live call, 2026-10-10: doctor
    # exited 1 while carrying a complete, useful report). So the verdict is read OUT of the report. A
    # payload too large to hand back is truncated and SAID to be truncated rather than silently cut.
    payload: Any = None
    try:
        payload = json.loads(out.strip())
    except Exception:
        payload = None
    if action == "doctor" and isinstance(payload, dict):
        return {"ok": True, "action": action, "bin": exe, "code": code,
                "healthy": payload.get("healthy"),
                "environment_healthy": payload.get("environment_healthy"),
                "checks": payload.get("scope_checks") or payload.get("checks"),
                "note": "a non-zero exit from doctor means the environment is not FULLY healthy, not "
                        "that the call failed. Native-binary analysis needs an external engine "
                        "(Hopper, Ghidra or IDA); JavaScript, Electron and web analysis need none."}
    if isinstance(payload, (dict, list)):
        blob = json.dumps(payload)
        if len(blob) <= 12000:
            return {"ok": code == 0, "action": action, "code": code, "bin": exe, "result": payload}
        return {"ok": code == 0, "action": action, "code": code, "bin": exe, "result": payload,
                "truncated": True, "bytes_available": len(blob),
                "hint": f"the report is {len(blob)} chars; narrow it with a more specific path or read "
                        "the full envelope with rea's own --filter-output"}
    return {"ok": code == 0, "action": action, "code": code, "bin": exe,
            "stdout": out[-12000:], "stderr": err[-2000:]}
