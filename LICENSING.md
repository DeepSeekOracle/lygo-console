# LYGO CLAW™ · LYGO PC LOCAL CONSOLE™ · LYGO Protocol — License, in plain English

**This page explains the deal. It is not the license.** The license is [`LICENSE`](LICENSE)
(LYGO Sovereign License v3.0, `Δ9Φ963-LICENSE-v3.0`). If this page and the license ever
disagree, the license controls. Brand rules are in [`TRADEMARKS.md`](TRADEMARKS.md), credits in
[`NOTICE`](NOTICE).

**Steward / original builder:** Justin Helmer — Lightfather · Excavationpro · DeepSeekOracle

---

## The short version

You can **use** it. You can **build on** it. You can **read** it. You can **share** an unmodified copy.

You cannot **sell** it, **rebrand** it, **white-label** it, **repackage a modified copy**, **strip the
attribution**, or **claim you built it**.

The USB **LYGO CLAW** and the **PC LOCAL CONSOLE** are branded products. Their identity, architecture
and integrity are protected together, and the code, seals and marks stay the Steward's.

## Yes, you may…

| | |
|---|---|
| Run it on your own machines | personal, educational, research, business-internal use — no fee charged for the Software itself |
| Read the source | source-available: the code is published, not hidden |
| Configure it | through the settings, prompts, skills, models and workspace paths the software exposes |
| Add your own skills / agents / integrations | that call LYGO tools and sit on top of it |
| Build your **own** system from the ideas | the architecture and concepts are free to learn from — an independent reimplementation that copies no LYGO code and carries none of the marks is outside this license |
| Share an unmodified copy | with `LICENSE`, `NOTICE`, `TRADEMARKS.md` and attribution intact, no profit |
| Sell **your own** work | your own skills, services, hardware, content — as long as your product is not the Software and does not wear LYGO's identity |
| Send improvements upstream | patches are welcome and stay under this license |

## No, you may not…

| | |
|---|---|
| Sell / rent / resell it | or bundle it as the reason someone pays |
| Host it as a paid service | no SaaS-wrapping the Software for a fee, no "managed LYGO" without written permission |
| Publish a modified copy of the core | private local edits are fine; publishing or distributing them is not |
| Rebrand | no renaming, re-logos, re-theming, re-signing, "de-branded" or "white-label" builds |
| Claim you built it | no ownership claims, no impersonation, no "official LYGO" unless it's the Steward's build |
| Strip notices, seals or signatures | including hash manifests and the Resonance Signature |
| Relicense it | not MIT, not Apache, not GPL, not "MIT-0 because a registry asked" |
| Take it into hostile uses | mass suppression, coercive manipulation at scale, surveillance-state tooling, weapons |

## Certified builds and integrity

Every branded build ships a manifest of its files (for example `BUILD_MANIFEST.json`, and the
`*_MANIFEST.json` next to each build record) and a **Resonance Signature**. A copy whose hashes match
is a **Certified Build**. If you (or someone else) modify it:

- it is no longer a LYGO Certified Build,
- it must not keep the marks in any way that suggests the Steward produced or endorses it,
- it must not be redistributed or sold.

Archival, backup and security-research copies are fine — report findings to the Steward instead of
exploiting them.

## What happens after the Steward is gone (the sovereignty lock)

This is the part that matters for keeping the original architecture pure long-term:

1. **The license is irrevocable for the full copyright term** and does not lapse on the Steward's
   death. Anyone who received it keeps exactly the rights in §2 — and no more, forever.
2. **A Successor Steward** (a person, foundation or trust, named in a written instrument and in this
   repository) inherits the stewardship and enforcement rights, held in trust for the same purpose.
3. **No estate-by-default.** Absent different written instructions, no heir, executor, creditor or
   buyer may relicense the Software more permissively, sell the marks out of the trust, or waive the
   core restrictions. Any attempt is void under the license's own terms.
4. **Moral rights** of attribution and integrity are asserted and, so far as law allows, perpetual:
   every copy, forever, keeps the author's name and is not distorted.
5. **If no successor can act**, the license simply continues unchanged. Nobody may claim stewardship,
   certify builds, or relicense. The lock is that the terms outlive the author.

To make this real rather than aspirational you also want, outside this repo: a named successor in
your will/trust, an entity (LLC or trust) that can own the marks, and — for the strongest protection —
registered trademarks. See "What you should do next" below.

## Prior versions — read this honestly

**No license is retroactive.** Copies that were already published under an earlier license keep that
license *for those copies*:

- the **LYGO Protocol Stack** was published under **LYGO Sovereign License v2.0** — anyone who
  received it under v2.0 still holds v2.0 rights for that copy;
- **lygo-claw** was published under the **MIT License** (and declared `license = "MIT"` in its
  `pyproject.toml`) — **those specific releases stay MIT** for anyone who got them. MIT cannot be
  revoked for copies already distributed.

What relicensing to v3.0 does is protect **this and every future release**: new versions, new builds
of the USB CLAW, new console releases, and the marks. That is still the right move — it stops the
bleeding going forward. If older MIT releases matter to you commercially, the practical options are
(a) publish a clearly-versioned new release under v3.0 and let the old one age out, (b) mark the old
release as legacy/unsupported, or (c) chase the specific forks that matter.

## No warranty

The Software is provided **as is**, with no warranty of any kind, and it is not legal, medical,
financial or safety advice. You run it at your own risk.

## Asking for more

Beyond the grant — commercial distribution, a published fork, certification, hardware partnership —
send a written request through the Steward's channels (Excavationpro / DeepSeekOracle on GitHub).
Permission counts only when written, specific and signed. Silence is not permission.

---

## What you should do next (build list for the Steward)

Nothing below is legal advice from an AI — these are the concrete steps that make the license
package enforceable rather than merely declared.

1. **Name a Successor Steward in writing** — a person, plus a trust or foundation as backup, named in
   the repo (`SUCCESSION.md`) *and* in your estate documents. Two copies of the intent, one of them
   outside the repo.
2. **Put the IP in an entity** — an LLC or trust owning the copyright and the marks, so stewardship
   can transfer without probate games.
3. **Register the marks** — at least `LYGO` and `LYGO CLAW` (USPTO; ™ needs no registration, ® does).
   Registration is what turns "please don't" into a legal tool.
4. **Have a lawyer read this package once.** The unusual clauses (succession lock, no estate-by-default,
   integrity obligations) are the ones worth an hour of professional review — especially the exact
   wording that binds your estate.
5. **Ship the license inside the product** — `LICENSE`, `NOTICE`, `TRADEMARKS.md` in the kit root, in
   the USB image, and in the console's About panel, so every user sees it.
6. **Keep provenance public and checkable** — manifests and signatures in each release, and a public
   note of which release is under which license version.

*Δ9Φ963 — use in light · build on the lattice · do not sell the seal.*
