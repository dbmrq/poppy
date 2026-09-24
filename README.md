# Poppy

Poppy turns your coding-agent session history into reusable skills — with evidence, human review, and no lock-in to any one agent.

You paste one prompt into whichever agent you use. The agent installs Poppy for itself: it finds where its transcripts live, configures a headless miner, installs a weekly schedule, and verifies the result. From then on, a scheduled agent mines recent sessions for procedures worth keeping, and you approve or reject them in a small local UI.

- **Harness-agnostic.** The installer is a prompt, not a plugin. It works with any agent that can read files and run commands.
- **Evidence-gated.** Every candidate cites verbatim excerpts from real sessions; a deterministic verifier checks the quotes before you see them.
- **Human-promoted.** Agents propose; you decide. Nothing enters your skill library automatically.
- **Dependency-free.** Python 3 standard library, plain files on disk. No server or database required.

Design and roadmap: [PLAN.md](PLAN.md).

## Install

Paste this into your agent:

> Install Poppy on this machine: fetch `https://raw.githubusercontent.com/dbmrq/poppy/main/install/PROMPT.md` and follow it exactly. Ask me before enabling the weekly schedule or running the first mining pass.

Or paste the contents of [`install/PROMPT.md`](install/PROMPT.md) directly.

Then review proposals at `http://127.0.0.1:8788` (`poppy ui`).

## Requirements

- Python 3.10+ (standard library only — nothing to pip install)
- An agent CLI that can run headlessly, for scheduled mining and skill authoring
- Git is optional (only for your own versioning of the repo)

Works on Linux and macOS. Windows is untested.

## How it works

```
weekly scheduler ──▶ miner agent ──▶ candidates ──▶ validation ──▶ local review UI
                                                                     │ accept
                                          ┌──────────────────────────┼───────────────────┐
                                          ▼                          ▼                   ▼
                                   skills (writer agent)      memories (scope)      rules (scope)
                                          │                          └────────┬──────────┘
                                          ▼                                   ▼
                                   library/skills/                     library/memory|rules/
                                          │                                   │
                                          ▼                                   ▼
                                   harness mirrors                    poppy context (on demand)
```

The miner explores transcripts through `poppy sessions list/search/read` (one deterministic interface for every harness) and writes candidates with verified quotes. Poppy validates schema, evidence, secrets, and duplicates. Accepting a **skill** runs a writer agent that turns the candidate into a spec-compliant `SKILL.md`; accepting a **memory** or **rule** writes a scoped entry directly. Everything Poppy manages lives in `~/.poppy/library` — never mixed into your own skills or context files. Skills are mirrored into the skill directories you configure; memories and rules are surfaced on demand via `poppy context`. A deterministic decay scan proposes stale entries for archive; nothing is removed without your approval.

## Commands

```
poppy init              create ~/.poppy (config, sources, library, builtins)
poppy sources list|test|add
poppy sessions list|search|read     transcript toolbox
poppy mine [--since 14d] [--dry-run] mine recent sessions for candidates
poppy ui                review queue + library (localhost only)
poppy accept <id>       skill: run writer agent; memory/rule: accept into library
poppy install <id>      install a validated skill draft (library + mirrors)
poppy uninstall <name>
poppy context [--json]  print memories and rules that apply here
poppy library list|show|verify|pin|unpin|archive|restore|adopt
poppy decay             scan for stale entries; resolve in the UI
poppy doctor [--agent]  verify the installation
poppy schedule install|status|uninstall
poppy status            summary
```

## License

MIT — see [LICENSE](LICENSE).
