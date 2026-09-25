---
name: poppy-propose
description: >-
  Queue a durable fact, rule, or reusable procedure into Poppy for the user to
  review. Use when the user says "remember this", "always do X", "never do Y",
  or when a hard-won procedure or preference clearly deserves to outlive this
  session.
---

# Proposing to Poppy

Poppy's scheduled miner reads sessions later; this skill captures something
durable *now*, while you have the context. Proposals land in the same review
queue as mined candidates — nothing becomes active until the user accepts it.

## What qualifies

The bar is the miner's bar: **could a capable agent work this out in a few
minutes on its own?** If yes, it is not worth keeping. Skills and rules must
have cost real work in this session — a failed attempt, a correction, an
environment quirk, a non-obvious flag, a dead end before it clicked.

**The exception is a memory the user stated**: a preference, constraint, path,
service, account, or piece of environment or project knowledge they mentioned
in passing qualifies even when nothing was hard — as long as the next agent
could not have learned it without being told. Their own words are the
evidence, and capturing these is how Poppy learns how the user's world is set
up. Still not worth queueing: anything in the repo or its docs, anything
detectable on the machine in seconds, or anything already in the library.

- **memory** — a durable fact or preference (user, machine, project, or task)
  that is not derivable from the code, the docs, or common knowledge.
- **rule** — a negative constraint that prevented or would prevent a real
  mistake ("Never …", "Always avoid …").
- **skill** — a reusable procedure that took real effort to work out and saves
  that effort next time.

Not: secrets or credentials, one-off task details, anything a capable agent
already knows (repo conventions, documented library behavior, standard tool
usage), work that succeeded on the first try, or anything already in
`poppy library list` / `poppy candidates list`.

## Propose proactively

When a session just produced something that passes the bar — even if the user
did not ask you to remember it — say so in one line and queue it. Then tell the
user it is waiting in the review queue and offer to open the Poppy UI
(`poppy ui`); nothing becomes active until they accept it there. Do not queue
weak candidates to be helpful: every one costs review time.

## How

1. Pick the kind, a title (≤ 100 chars), a 2–5 sentence summary, and a trigger
   ("Applies when …" / "Use when …"). For project scope prefer
   `remote:<git remote URL>`; otherwise the absolute project path.
2. Collect **verbatim quotes** from this conversation that show the fact,
   mistake, or procedure — never paraphrase. One clearly durable quote is
   enough; Poppy locates which session it came from.
3. Write a candidate JSON file and run:

```bash
poppy propose --file /tmp/poppy-candidate.json --json
```

```json
{
  "kind": "memory | rule | skill",
  "title": "…",
  "summary": "…",
  "trigger": "…",
  "scope": "user | machine | project | task",
  "project": "remote:<url> or absolute path (scope=project only)",
  "evidence": [{"quote": "verbatim excerpt from this session"}]
}
```

Poppy validates the schema, locates and re-checks every quote, rejects
high-confidence secrets, and dedupes against the library and queue. If it
reports an error, fix the quote or fields and retry once; then tell the user it
is queued (review with `poppy ui`) instead of retrying endlessly. If
`poppy propose` is missing, Poppy is outdated: update the checkout and re-run
`poppy init`.
