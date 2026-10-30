# Skills module (OpenClaw-compatible)

A **skill** is a folder with `SKILL.md` (YAML frontmatter + markdown body), same as [OpenClaw / ClawHub](https://docs.openclaw.ai/tools/creating-skills).

## Load order (highest first)

1. `workspace/skills/`
2. `save/skills/installed/` (ClawHub downloads)
3. Extra dirs: `enabled.json` `extra_dirs`, env `LYGO_SKILLS_DIRS`, then `~/.agents/skills`, `~/.openclaw/skills`, `~/.grok/skills`
4. Bundled `skills/champions/` (15 Δ9 seats)

## What the agent sees

Enabled skills inject **name + description only**. Full `SKILL.md` is loaded with `skill_read` when the operator invokes that seat (`Invoke ARKOS`, `/skill champion-lyra`, Skills panel click).

This console **does not execute** skill `.exe` / `.bat` / scripts. Instructions only.

## SkillHub (wired)

Page: https://chatagent.ca/lygoskillhub.html

| Channel | Catalog | Install |
|---------|---------|---------|
| Public tentacle | `data/lygoskillhub_catalog.json` | ClawHub zip → `save/skills/installed/` |
| FULL engineer | `data/lygo-full-skills/catalog.json` | dist zip **SHA-256 checked** then unpack. Live Star Chart / git push still need human consent. |

Tools: `skillhub_list` · `skillhub_install` (`full=true` for Channel B).

## ClawHub

Search: `GET https://clawhub.ai/api/v1/search`  
Install: download zip → `save/skills/installed/<slug>/` → enable. Malware-blocked skills are refused.

## Public vs admin

Public `prompts/SOUL.md` offers the 15 champions — no private biography. Admin `workspace/SOUL.md` (gitignored) may stay steward-specific.
