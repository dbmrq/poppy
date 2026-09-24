---
name: poppy
description: >-
  Operate Poppy, the user's reviewed library of mined skills, memories, and
  rules. Use when the user mentions Poppy, asks what it learned, wants to
  review or clean up candidates, inspect or change the library, run a mining
  pass, sync machines, publish a skill, or check Poppy's health.
---

# Poppy

Poppy mines the user's coding-agent sessions into reviewed skills, memories,
and rules. You drive it through the `poppy` CLI; the user decides what is
promoted. `poppy <command> --help` documents each command; prefer `--json`
where offered and summarize the result for the user instead of pasting raw
JSON.

## Non-negotiables

- **Ask before changing anything.** Accepting, installing, rejecting,
  discarding, archiving, pinning, and publishing all change the user's
  library. Show what you found (with evidence) and get an explicit decision
  first.
- **Evidence is verified.** Candidates carry verbatim quotes checked against
  the original session; show them when asking.
- **Nothing is deleted.** Archiving is reversible (`poppy library restore`);
  say so when the user hesitates.
- **Do not hand-edit `~/.poppy`.** Use the CLI; the queue and library are
  Poppy's to manage.

## Routing

| The user asks | Run |
| --- | --- |
| "What's waiting for review?" | `poppy candidates list --json`; summarize titles + evidence, offer accept/reject |
| "Show me candidate X" | `poppy candidates show <id> --json` (includes the draft for skills) |
| "Accept X" | `poppy accept <id> --json`; if it returns `draft`, show it and `poppy install <id>`; memories/rules become active immediately |
| "Reject X" | `poppy reject <id> --reason "..."` |
| "Redo that skill draft" | `poppy discard <id>`, then `poppy accept <id>` |
| "What do you know about X?" | `poppy library list --json`, `poppy context show --cwd <dir>`; full text with `poppy library show <ref>` |
| "Forget X" | `poppy library archive <ref>` (restorable) |
| "Learn from my recent work" | `poppy mine --json` (invokes the miner agent; can take minutes) |
| "Sync my machines" | `poppy sync run --json`; if it exits 2, `poppy sync status` |
| "Share this skill" | `poppy publish <name> --to <checkout>`, review the warnings, then `--commit [--push]` only after approval |
| "Is Poppy healthy?" | `poppy doctor --json`, `poppy status --json` |
| "Why did mining miss X?" | `poppy sessions search "<term>" --json`; mining is bounded by the lookback window and the candidate cap |

Notes: `poppy sync run` exits 1 when it synced changes and 2 on conflicts —
read the JSON/status, not the exit code alone. `poppy ui` is the same review
surface for humans; everything it does is available on the CLI.

## Related skills

- `poppy-context` — fetch full memory entries whose headlines appear in your
  context.
- `poppy-propose` — file a durable fact, rule, or procedure into the review
  queue the moment it is learned.
