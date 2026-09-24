---
name: poppy-context
description: >-
  Fetch full Poppy memory entries whose headlines appear in the Poppy memory
  index. Use when a headline seems relevant to the task, when the user
  references past decisions or preferences, or before acting on project
  conventions.
---

# Poppy context

Your context contains a generated **Poppy memory index** (wired in by the
installer): binding rules in full, plus one-line headlines for memories and
scoped rules. The headlines tell you what exists without loading everything.

When a headline seems relevant, fetch the full entry **before acting**:

```bash
poppy library show <ref>
```

- `<ref>` is the short id in parentheses, e.g. `(1a2b3c)`; unique prefixes work.
- `poppy library list` shows everything Poppy knows.
- `poppy context show` prints the entries that apply to this machine and
  project.
- Poppy entries are evidence-backed but not infallible. If one contradicts what
  you observe, say so — the user can verify, pin, or archive it in `poppy ui`.

If you do not see a Poppy memory index block in your context at all, run
`poppy context show` instead and tell the user the index is not wired in.

Do not edit files under `~/.poppy` by hand: the user reviews and promotes
changes through the Poppy UI, and manual edits break the provenance chain.
