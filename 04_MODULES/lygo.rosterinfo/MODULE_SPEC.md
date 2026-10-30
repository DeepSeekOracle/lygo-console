# lygo.rosterinfo

Read-only card for the Manager roster.

- `GET /api/roster` lists skill counts, Unity skill names, and console limb names.
- `GET /api/roster?q=unity` narrows that list. Counts on the lights stay the full totals.
- Hermes calls the same route with the `lygo_roster` tool.
- `health()` does not walk skill folders.
- The card does not launch Unity, a model, or a network call.
