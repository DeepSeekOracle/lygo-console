# DISCLAIMER — LYGO LLM Console 1.9.1

**Read this before you install, build, or run anything from this repository.**

This file is a plain-language disclaimer. It is not the licence — the licence is
`LICENSE` (or `LICENSE.md`) (see also `LICENSING.md` / `TRADEMARKS.md` where present). Where the two
differ, the licence governs your rights and this file governs your expectations.

---

## 1. This is Public Beta software

LYGO LLM Console is at **1.9.1**, published as the **first Public Beta of the working system**.
"Public Beta" means, precisely:

- it works, and it is what the steward runs day to day;
- it has **not** been hardened for hostile input, multi-tenant use, or unattended operation;
- interfaces, file formats and configuration keys **can change without notice**;
- a release that works on the steward's machine is not a promise that it works on yours.

Treat every beta release as reversible. Keep your own backups. Do not put it in a position where
its failure costs you something you cannot restore.

## 2. No warranty

The software is provided **as is**, without warranty of any kind, express or implied, including
but not limited to merchantability, fitness for a particular purpose, title, and
non-infringement. There is no guarantee that it is free of defects, that it will run
uninterrupted, or that defects will be corrected.

## 3. Limitation of liability

To the maximum extent permitted by law, the author and copyright holder is **not liable** for any
claim, damages or other liability — whether in an action of contract, tort or otherwise — arising
from, out of, or in connection with the software or its use. That includes lost data, lost
profits, lost time, hardware wear, and any consequence of a model producing a wrong answer.

You run this on your own machine, at your own risk, with your own data.

## 4. It runs a local language model on your machine behind a loopback HTTP console, and can drive media limbs. Nothing is uploaded by the authors; there is no telemetry.

A language model with tool access can read files you point it at, run scripts and fetch public HTTPS. It can also be wrong. You are responsible for what you ask it to do and for reviewing what it produces before anything leaves your machine.

## 5. AI output is not authority

Where this system runs a language model or any generative component, its output is **generated
text, not a fact, not advice, and not a professional opinion**. It can be confidently wrong,
invent citations, misread a file, or repeat a bias in its training data.

Do not use it as the sole basis for medical, legal, financial, safety, employment, housing or
immigration decisions. Do not present its output as human-reviewed when it was not. If a decision
matters, a human verifies it against a source that is not this software.

## 6. Your data, your responsibility

Model files, prompts and console records stay in the install directory on your own disk. API keys you configure are read from local config at runtime and are never part of this repository.

- Nothing is uploaded by the authors. There is **no telemetry, no analytics, no auto-update, and
  no phone-home**.
- Anything that leaves your machine does so because **you** configured a remote model, a bridge,
  a proxy or an upload target. That destination's terms and privacy policy apply to what you send.
- Back up before you upgrade. Beta upgrades are not guaranteed to migrate state.

## 7. Models, weights and third-party components are not covered by this licence

This repository ships **code**. It does not ship model weights, engines or third-party
applications unless a folder says otherwise.

- Model weights you download are governed by **their own licences**, which may restrict
  commercial use, redistribution or output ownership. Check the model card before you ship
  anything built with it.
- Bundled or referenced third-party components keep **their own licences**, which prevail for
  those components. See `NOTICE`, `licenses/`, and any `LICENSE` file inside a `tools/`
  subfolder.
- No third-party trademark is granted to you. Product names are used descriptively.

## 8. Security is a shared responsibility

The console, engine and bridges bind to 127.0.0.1. If you expose a port, you own the firewall in front of it. Tool execution runs with your user privileges — the same privileges as any script you run yourself.

Never commit secrets to this repository, and never paste an API key into an issue, a screenshot or
a log. If you find a vulnerability, see [`SECURITY.md`](SECURITY.md) — please do not open a public
issue with a working exploit.

## 9. Not for regulated or high-assurance use

Do not deploy this in a context where failure could cause injury, environmental damage, financial
loss at scale, or a regulatory breach — including medical devices, aviation, vehicle control,
industrial safety systems, and unsupervised financial execution — without your own engineering
review, validation and legal sign-off.

## 10. No support obligation, no roadmap promise

Issues and pull requests may be read; they are not a support contract. Nothing in this repository
is a commitment to a feature, a date or a fix. The steward may change, pause or retire any part of
the system.

## 11. Local law and export

You are responsible for using this software in compliance with the laws that apply to you,
including export control, sanctions, data protection (GDPR/CCPA/etc.), and the terms of any API
or model you connect it to.

## 12. Changes to this disclaimer

This file may change between releases. The version in the tag or commit you cloned is the one that
applies to that copy.

---

**Steward:** Justin Helmer — Lightfather · Excavationpro · DeepSeekOracle
**Status:** 1.9.1 Public Beta · **Trademarks and brand rules:** see `TRADEMARKS.md` where present.
