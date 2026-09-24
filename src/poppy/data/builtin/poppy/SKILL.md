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
- **Uninstalling is destructive.** `poppy purge` removes the schedule,
  mirrored skills, the digest wiring, and — unless `--keep-data` — the whole
  library. Never run it without an explicit, informed decision.

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
| "Sync my machines" | `poppy sync run --json`; automatic sync runs on the timer `poppy sync init` installed (`poppy sync status` shows it). If it exits 2, resolve the conflict it reports |
| "Share this skill" | `poppy publish <name> --to <checkout>`, review the warnings, then `--commit [--push]` only after approval |
| "Update Poppy" | `poppy update --check` first; then `poppy update` (handles pipx, Homebrew, pip, and checkout installs; refreshes the builtin skills) |
| "Uninstall Poppy" | `poppy purge` prints the plan; `poppy purge --yes` applies it, `--keep-data` keeps the library. Only after an explicit, informed decision |
| "Is Poppy healthy?" | `poppy doctor --json`, `poppy status --json` |
| "Review from another device" | `poppy ui --host <addr> --token <secret>` (HTTP Basic). Never bind a non-loopback address without a token |
| "Why did mining miss X?" | `poppy sessions search "<term>" --json`; mining is bounded by the lookback window and the candidate cap |
| "Mining finds nothing / a source broke" | `poppy sources list`, `poppy sources test <name>`, `poppy doctor`; if the store moved or is missing, re-run the source-discovery section of the installer prompt |

Notes: `poppy sync run` exits 1 when it synced changes and 2 on conflicts —
read the JSON/status, not the exit code alone. `poppy ui` is the same review
surface for humans; everything it does is available on the CLI.

## Related skills

- `poppy-context` — fetch full memory entries whose headlines appear in your
  context.
- `poppy-propose` — file a durable fact, rule, or procedure into the review
  queue the moment it is learned.
