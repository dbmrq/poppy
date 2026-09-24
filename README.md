# Poppy

[![tests](https://github.com/dbmrq/poppy/actions/workflows/tests.yml/badge.svg)](https://github.com/dbmrq/poppy/actions/workflows/tests.yml)

Poppy turns your coding-agent session history into reusable skills — with evidence, human review, and no lock-in to any one agent.

You paste one prompt into whichever agent you use. The agent installs Poppy for itself: it finds where its transcripts live, configures a headless miner, installs a weekly schedule, and verifies the result. From then on, a scheduled agent mines recent sessions for procedures worth keeping, and you approve or reject them in a small local UI.

- **Harness-agnostic.** The installer is a prompt, not a plugin. It works with any agent that can read files and run commands.
- **Evidence-gated.** Every candidate cites verbatim excerpts from real sessions; a deterministic verifier checks the quotes before you see them.
- **Human-promoted.** Agents propose; you decide. Nothing enters your skill library automatically.
- **Dependency-free.** Python 3 standard library, plain files on disk. No server or database required.

Design and roadmap: [PLAN.md](PLAN.md).

## Agent-first

You don't drive Poppy's CLI; your agent does. The installer puts a few skills in your skill directories, and from then on you just ask:

- **`poppy`** — operate Poppy: "what's waiting for review?", "accept the cron one", "what do you know about deploys?", "sync my machines", "share this skill", "is Poppy healthy?". The skill carries the hard rule that the agent never promotes, archives, or publishes without your explicit decision.
- **`poppy-context`** — fetch full memory entries when a headline in your context is relevant.
- **`poppy-propose`** — capture a durable fact, rule, or procedure the moment you say "remember this"; it lands in the same review queue as mined candidates.

The CLI is the API the agent uses: the actions mirror the review UI (`accept`, `reject`, `install`, `archive`, `pin`, `publish`, …), the commands an agent drives take `--json`, and nothing is promoted automatically. `poppy ui` is still there when you want to browse yourself.

## Install

Paste this into your agent:

> Install Poppy on this machine: fetch `https://raw.githubusercontent.com/dbmrq/poppy/main/install/PROMPT.md` and follow it exactly. Ask me before enabling the weekly schedule or running the first mining pass.

Or paste the contents of [`install/PROMPT.md`](install/PROMPT.md) directly.

Prefer Homebrew? `brew tap dbmrq/tap && brew install poppy-ai` installs the CLI; run the installer prompt afterwards to configure sources and schedules.

The agent installs the `poppy-ai` package with pipx (or pip) — from PyPI when the release is published, from git otherwise; `poppy update` keeps it current whichever way it was installed (`poppy update --check` reports first). The agent also finds its own transcript store — there is no per-harness integration and no support matrix — and verifies it against real sessions before going further. Then review proposals at `http://127.0.0.1:8788` (`poppy ui`).

## Requirements

- Python 3.10+ (the runtime is standard library only — no dependencies)
- An agent CLI that can run headlessly, for scheduled mining and skill authoring
- `pipx` (recommended), `pip`, or `git` to install Poppy itself

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
                                   harness mirrors                    memory index digest
                                                                      (headlines; full entry via
                                                                       `poppy library show <ref>`)
```

The miner explores transcripts through `poppy sessions list/search/read` (one deterministic interface for every harness) and writes candidates with verified quotes. Poppy validates schema, evidence, secrets, and duplicates. Accepting a **skill** runs a writer agent that turns the candidate into a spec-compliant `SKILL.md`; accepting a **memory** or **rule** writes a scoped entry directly. Everything Poppy manages lives in `~/.poppy/library` — never mixed into your own skills or context files. Skills are mirrored into the skill directories you configure; memories and rules are surfaced on demand via `poppy context`. A deterministic decay scan proposes stale entries for archive; nothing is removed without your approval.

## Multi-machine

`~/.poppy` can be one private git repo shared by all your machines. `poppy sync init --remote <private-repo-url>` tracks the library, the candidate queue, and clean rejections; machine-local state (config, sources, mirrors, usage, logs) stays out. It also installs an **automatic sync timer** (every 30 minutes by default, `sync.interval_min`), so machines stay in sync without being prompted — `--no-schedule` opts out. `poppy sync run` commits, pulls, pushes, then reconciles skill mirrors and regenerates the memory digest. It soft-fails offline and retries; conflicts are surfaced for you to resolve, never auto-merged. Run the installer prompt on each machine with the same remote URL.

## Remote review

`poppy ui` binds localhost by default. To review from another device, bind an address and set a token (HTTP Basic; any username, the token as the password):

```bash
poppy ui --host 0.0.0.0 --token "$(openssl rand -hex 16)"
```

A non-loopback bind without a token is refused unless you pass `--insecure`. The token can also live in config (`poppy config set ui.token <secret>`) so it stays out of process listings. POSTs must be `application/json`, so a cross-site form cannot act on the library, but the UI can accept skills — treat the port as an admin endpoint. For remote access prefer an SSH tunnel or an authenticating proxy (Cloudflare Access, Tailscale); pass `--insecure` only when the port itself is unreachable from untrusted networks.

Want to look around before wiring Poppy into anything? `poppy ui --demo` serves the same UI over mock data: every card and row state, nothing read from or written to disk.

## Uninstall

```bash
poppy purge              # prints exactly what would be removed
poppy purge --yes        # removes the schedule, mirrors, digest wiring, and ~/.poppy
poppy purge --yes --keep-data   # same, but keeps the library and queue
```

Only skills Poppy manages are removed — your own skills are never touched — and the CLI itself is left in place with a printed removal command (`pipx uninstall poppy-ai`, `pip uninstall poppy-ai`, or removing the checkout). Without `--yes`, nothing happens.

## Publishing

The library is your private working set; a public skills repo is a curated subset. `poppy publish <skill> --to <checkout>` copies one reviewed skill into a local checkout of that repo:

```bash
poppy publish widget-deploys --to ~/src/agent-skills --subdir skills --commit --push
```

Set `publish.target` (and `publish.subdir`) to make `--to` optional. Poppy re-validates the skill, refuses to overwrite a destination that differs unless you pass `--force`, warns when the skill embeds machine-specific details (your home path or hostname), and can commit and push in the target checkout. Without `--commit` it just writes the files, so you can review the diff first.

## Commands

```
poppy init              create ~/.poppy (config, sources, library, builtins)
poppy sources list|test|add
poppy sessions list|search|read     transcript toolbox
poppy mine [--since 14d] [--dry-run] mine recent sessions for candidates
poppy ui [--host H] [--token T]
                        review queue + library (localhost by default)
poppy accept <id>       skill: run writer agent; memory/rule: accept into library
poppy reject <id> [--reason "..."]
poppy discard <id>      drop a skill draft and return the candidate to pending
poppy propose --file <json>
                        queue a candidate from an interactive session
poppy install <id>      install a validated skill draft (library + mirrors)
poppy uninstall <name>
poppy context show|export|wire|unwire|status|verify
poppy library list|show|verify|pin|unpin|archive|restore|adopt
poppy decay             scan for stale entries; resolve in the UI
poppy doctor [--agent]  verify the installation
poppy schedule install|status|uninstall
poppy sync init|run|status           private-repo sync across machines
poppy publish <skill> [--to DIR] [--subdir S] [--commit] [--push] [--force]
                        copy a reviewed skill into a public skills repo
poppy purge [--yes] [--keep-data]
                        remove Poppy from this machine
poppy update            update Poppy (pipx, pip, or checkout) and refresh builtins
poppy status            summary
```

## License

MIT — see [LICENSE](LICENSE).
