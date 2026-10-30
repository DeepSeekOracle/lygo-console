---
name: unity-harness
description: Unity harness for the LYGO Manager. Read unity-cli and the Unity plugin skills before any Editor command.
---

# Unity harness

This skill is the map. The tools live in the Unity agent plugin and in the named Unity skills. The Manager roster card and `lygo_roster` list them. Read a skill with `skill_read` before following it.

## Read first

- `unity-cli` — drive a running Unity Editor, or install and open a project, from the command line.
- `unity-2d-3d-building` — Battle TV painted art. Built-in pipeline, not URP.
- `lygo-unity-arcade-build` — a Unity game for the chatagent arcade.
- `unity-agent-web-games` — agents wiring a browser-playable Unity game.
- `unity-webgl-publishing` — a WebGL build on a static host.
- `forge-unity-worker` — Forge jobs that touch a Unity project.

The plugin skills under the Unity agent plugin (UI Toolkit, uGUI, sprites, tilemaps, navigation, audio, and the rest) are the rest of the harness. Their names are on the roster card. Source `unity` in the Skills panel is that plugin.

## Rules

- Read `unity-cli` before any `unity` command. Do not invent Editor flags.
- Do not launch the Unity Editor unless the operator asked for that. A roster check is a list, not a boot.
- One heavy job at a time on this machine. The chat model already holds the GPU.
- The console does not copy Unity. It points at the skills already installed for this user.
