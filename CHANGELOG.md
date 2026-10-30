# Changelog — LYGO LLM Console

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project uses
`MAJOR.MINOR.PATCH` and marks public betas with a `-beta` tag suffix.

## [1.9.1] — 2026-10-30 — **Public Beta 1**

The first public beta of the working system. Everything below was in the tree when the tag was cut.

### Added
- **Public tree hygiene.** Builder paths are replaced with `<CONSOLE_DIR>`-style placeholders at
  packaging time, and the build fails if an absolute builder path or a secret shape survives.
- **Installer sources for 1.9.1** — `installer/lygo_pc_191.iss` (Inno Setup 6), output
  `LYGO_LLM_CONSOLE_1.9.1_PC_SETUP`. Supersedes `lygo_pc_170.iss`.
- **`config/local.json.example`** — a committable template; the real `local.json` is withheld.
- **Standalone documentation** — `STANDALONE.md`, `HARDENING.md`, `MODELS.md` and this repository's
  `INSTALL.md`, `DISCLAIMER.md`, `SECURITY.md`.

### Changed
- **Version stamped 1.9.1** in `VERSION` and the surfaces that report it.
- **Anchor model recorded**: `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (2,382 MB), 64K context on an
  8 GB card. The model is not vendored; see `MODELS.md`.
- **Quiet process spawning** — `win_quiet.py` patches `Popen` with `CREATE_NO_WINDOW`, and
  `cloud_api._opencode_chat` passes `creationflags`. This fixed a popup-window storm caused by a
  per-turn `opencode.exe` spawn.

### Known at release
- `brain=missing` on a fresh clone until `engine/` and a `.gguf` are supplied. This is honest state,
  not a defect.
- One heavy engine at a time on an 8 GB card; the media limbs park rather than overlap.
- Public betas move: config keys and endpoints may change in the next beta without a migration.

### Not included in this repository
`data/`, `save/`, `workspace/`, `models/`, `engine/`, `utilities/filmcraft/`, and every
`config/api.json` / `local.json` / `admin.json`.

## Licence

LYGO Sovereign License v3.0 (`Δ9Φ963-LICENSE-v3.0`) — source-available, not OSI open source. See
[`LICENSE`](LICENSE).
