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

- **memory** — a durable fact or preference (user, machine, project, or task).
- **rule** — a negative constraint that prevented or would prevent a real
  mistake ("Never …", "Always avoid …").
- **skill** — a reusable procedure that saves real time next time.

Not: secrets or credentials, one-off task details, anything a capable agent
already knows, or anything already in `poppy library list` /
`poppy candidates list`.

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
