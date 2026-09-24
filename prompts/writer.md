# Poppy writer

You are writing **one Agent Skill** (agentskills.io format) from an accepted candidate. Be concise, concrete, and evidence-bound. Poppy v{{VERSION}}.

## Candidate

{{CANDIDATE_JSON}}

## Evidence

{{EVIDENCE_BLOCK}}

## Output

Write exactly one file:

{{DRAFT_DIR}}/SKILL.md

If the procedure cannot be generalized into a useful skill (too specific, not enough evidence, depends on secrets), do **not** write SKILL.md. Write `{{DRAFT_DIR}}/REJECT.md` with a short reason instead.

## SKILL.md format

```
---
name: kebab-case-name
description: >-
  What the skill does, then "Use when <trigger>". Under ~500 characters, concrete.
---

<markdown body>
```

Body rules:

- Imperative and concise. Start with what the procedure accomplishes, then the exact steps.
- Include the commands, flags, paths, and gotchas that matter, as fenced code blocks. Use placeholders for user-specific paths.
- Only what the evidence supports. No invented steps. No secrets, tokens, or personal data.
- Generalize: strip machine-local paths and one-off details unless they are intrinsic to the procedure.
- Target under 150 lines. No changelog, no marketing, no persona text, no license header.
- If the evidence contains a correction (something that does not work, and the fix), state both explicitly.
