---
name: poppy-context
description: >-
  Load Poppy memories and rules that apply to the current machine and project.
  Use when starting non-trivial work, when the user references past decisions
  or preferences, or before acting on project conventions.
---

# Poppy context

Poppy keeps durable memories and rules about the user, this machine, and this
project. They are managed under `~/.poppy/library` (never mixed into user
files) and every entry is backed by evidence from real sessions.

Before non-trivial work — or whenever you are unsure about a convention,
preference, or past decision — load the entries that apply here:

```bash
poppy context
```

- `poppy context --json` prints the same content as JSON.
- `poppy library list` shows everything Poppy knows (skills, memories, rules).
- Entries are reviewed and evidence-backed; treat them as high-confidence but
  not infallible. If one contradicts what you observe, say so — the user can
  verify, pin, or archive it in `poppy ui`.

Do not edit files under `~/.poppy` by hand: the user reviews and promotes
changes through the Poppy UI, and manual edits break the provenance chain.
