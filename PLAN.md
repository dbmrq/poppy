# Poppy — Plan

_Poppy turns coding-agent session history into reusable skills. It is harness-agnostic by construction: the agent you already use installs it, and an agent mines for it._

Status: **V4 complete** — agent-first operation, automatic multi-machine sync, clean uninstall, packaged installs (PyPI / pipx / Homebrew / checkout) with update checks, a token-protected remote review UI, and a harness-agnostic installer. Remaining nice-to-have: usage tracking from transcripts. This document is the persistent design and is updated as phases land.

---

## 1. Why

Agents forget. Every session re-derives procedures a previous session already worked out. Systems like Hermes fix this by learning continuously, but they lock you into one harness and grow an unbounded, unaudited library of skills.

Poppy keeps the good part — continuous learning — and drops the rest:

- **Harness-agnostic core.** No per-harness integrations. The installer is a prompt that an agent executes for itself, and the miner is an agent with ordinary shell tools.
- **Evidence-gated.** Every proposed skill cites verbatim excerpts from real sessions, and a deterministic verifier checks that the quotes exist before a human ever sees them.
- **Human-promoted.** Agents propose; you promote. Nothing enters the active skill library without review.
- **Bounded.** Scheduled runs have lookback windows, session/candidate caps, timeouts, and logs. Growth is deliberate.
- **Few/no dependencies.** Python 3 standard library, plain files, no server, no database, no daemon in the critical path.

## 2. Principles

1. **Deterministic where correctness matters, LLM only for judgment.** Finding/reading transcripts, validating evidence, storing candidates, scheduling, installing skills, and the review UI are code. Deciding what is worth keeping and writing a skill from evidence are prompts.
2. **Evidence or it does not exist.** A quote that cannot be located verbatim in the cited session invalidates the candidate.
3. **Nothing auto-lands.** Mining writes only to the inbox. Promotion and installation are explicit human actions.
4. **Bounded by default.** Overlapping lookback windows, per-run caps, timeouts, logs. Misses self-heal; runaway cost does not.
5. **Files over services.** State is markdown/JSON on disk. No server or daemon is required to read, review, or repair the system.
6. **Every phase ships standalone.** V1 (skills) is useful on its own; V2 (memory) and V3 (sync) are additive, never prerequisites.
7. **Removal is a feature.** (V2+) A library that only grows rots. Decay is proposed, never automatic, and demoted content is archived, not deleted.

## 3. The V1 loop

```
paste install/PROMPT.md into any agent
        │
        ▼
installer agent ── discovers transcript sources, headless commands, skills dirs
        │          writes ~/.poppy/config.json + sources.json
        │          verifies with `poppy doctor --agent`
        ▼
scheduler (weekly) ──▶ `poppy mine`
        │                 renders prompts/miner.md
        │                 runs the configured miner command headlessly
        │                 miner explores with `poppy sessions list/search/read`
        │                 miner writes candidate JSON into the inbox
        │                 poppy validates: schema, evidence quotes, secrets, duplicates
        ▼
`poppy ui` (localhost) ── accept / rewrite / reject candidates
        │                 write ─▶ writer agent drafts SKILL.md from evidence
        │                 draft validated ─▶ accept (installs the skill)
        │                 stale entries ─▶ archived automatically,
        │                                   undo card in the same queue
        ▼
installed skills ── copied into the configured skills directories
```

Nothing in the loop requires the miner and the user to share a session, a machine, or a harness.

## 4. Architecture

### Repository layout

```
bin/poppy            checkout entry point (installed copies use the console script)
pyproject.toml       packaging: distribution "poppy-ai", console script "poppy"
src/poppy/           Python 3 stdlib package (no third-party imports)
  cli.py             subcommands
  config.py          ~/.poppy/config.json
  sources.py         transcript sources: files | sqlite | command
  sessions.py        list/search/read toolbox (miner-facing, deterministic)
  candidates.py      candidate schema, validation, inbox processing, dedupe
  skills.py          SKILL.md validation, install/uninstall, manifest
  agent.py           headless agent invocation ({prompt} / {prompt_file})
  prompts.py         prompt rendering ({{PLACEHOLDER}} substitution)
  propose.py         mid-session proposals (quote location + validation)
  publish.py         export a reviewed skill into a public repo checkout
  purge.py           remove Poppy from a machine
  update.py          install-appropriate self-update (pipx | pip | checkout)
  doctor.py          environment + installation checks
  schedule.py        systemd user timer | launchd | cron instructions
  sync.py            multi-machine git sync: commit, pull, push, materialize
  ui.py              review UI server (stdlib http.server, localhost only)
  __main__.py        python -m poppy
  data/              runtime data shipped inside the package
    prompts/         miner.md, writer.md
    builtin/         builtin skills (poppy, poppy-context, poppy-propose)
    ui/index.html    review UI (no external assets)
install/PROMPT.md    the copy-paste installer prompt (the product's front door)
examples/            example source configurations
tests/               fixtures + stdlib unittest (source checkout only)
```

### State layout (`~/.poppy`, override with `POPPY_HOME`)

```
config.json          settings (see below)
sources.json         transcript sources written by the installer
library/             canonical Poppy-owned content (V2)
  skills/<name>/     approved skills (SKILL.md + support files)
  memory/<scope>/    memory entries (one markdown file each)
  rules/<scope>/     rule entries (negative constraints)
  archive/           demoted entries, restorable
candidates/          validated candidates and decay proposals (one JSON file each)
drafts/<id>/         writer output (SKILL.md + optional supporting files)
inbox/               miner output drop directory (validated on the way in)
rejected/            invalid candidates + rejection index (so they are not re-proposed)
installed.json       manifest of Poppy-installed skills and their mirrors
state/usage.json     last-used bookkeeping for decay
logs/                one log per run (mine-*.log, accept-*.log)
locks/               per-candidate locks (no double writer runs)
```

**Sync (V3):** `~/.poppy` itself is the git repo. **Tracked:** `library/`, `candidates/`, and one clean rejection file per rejected candidate (`rejected/<id>.json`) — one file per artifact, so merges are trivial. **Ignored (machine-local):** `config.json`, `sources.json`, `installed.json`, `inbox/`, `drafts/`, `logs/`, `locks/`, `state/`, `context/`, and derived data (`rejected/index.json`, `rejected/invalid-*.json`). The rejection index and the mirrors manifest are local caches rebuilt from synced files.

### Config (defaults)

```json
{
  "version": 1,
  "lookback_days": 14,
  "max_candidates_per_run": 5,
  "min_evidence": 1,
  "skills_dirs": ["~/.agents/skills"],
  "agent": {
    "miner":  {"cmd": ["<your agent>", "...", "{prompt}"], "timeout_sec": 1800},
    "writer": {"cmd": ["<your agent>", "...", "{prompt}"], "timeout_sec": 900}
  },
  "ui": {"host": "127.0.0.1", "port": 8788}
}
```

`{prompt}` is replaced by the rendered prompt in any argv element; `{prompt_file}` writes it to a temp file and substitutes the path (for agents that take a file). The installer fills these in by inspecting the agent's own CLI.

## 5. Deterministic vs. LLM boundary

| Concern | Owner | Basis |
| --- | --- | --- |
| Where transcripts live, how to read them | installer agent, once | writes `sources.json`; `poppy sources test` + `doctor` verify |
| Listing/searching/reading transcripts | `poppy sessions` | deterministic; same interface for every harness |
| Scheduling | `poppy schedule` | systemd/launchd/cron, detected and verified |
| What is worth keeping | miner agent, per run | judgment; bounded by window, caps, timeout |
| Evidence verification | `poppy` | quote must appear verbatim (whitespace-normalized) in the source |
| Dedupe + rejection memory | `poppy` | content hashes and title hashes |
| Skill authoring | writer agent, on accept | fresh context; candidate + excerpts + template |
| Skill quality gates | `poppy` | spec fields, naming, size, secret scan |
| Installing + tracking skills | `poppy` | explicit dirs + manifest; removable |
| Multi-machine sync | `poppy sync` | deterministic git operations; conflicts surfaced, never auto-merged |
| Review/promotion | `poppy ui` | human only |

## 6. Transcript access: three tiers

The installer picks the best tier per harness and **verifies it against real sessions**. There is no per-harness support matrix: discovery is a procedure (own CLI → conventional data directories → per-session files → SQLite → single history file), and a harness the prompt has never heard of is handled by the agent that installs Poppy, not by code.

1. **Harness commands** (`type: command`) — prefer the harness's own listing/export commands. Stability is delegated to the harness. `list_cmd` must print a JSON array whose items carry an id (plus optional time/title/cwd); `read_cmd` prints the session text (`{id}` substituted).
2. **Generic readers** (`type: files`, `type: sqlite`) — JSONL/text directories, or a read-only SQLite connection with two queries (`list_query`, `read_query`; the read query must alias a text column as `text`). Works for most harnesses without per-harness code.
3. **Custom command** — if neither fits, the installer writes a small read-only extractor and configures it as tier 1.

Example configs that are verified against real installations live in `examples/sources.example.json`.

## 7. Candidate and skill formats

**Candidate** (written by the miner into the inbox, validated by Poppy):

```json
{
  "kind": "skill | memory | rule",
  "title": "<= 100 chars",
  "summary": "what this is and why it is worth keeping",
  "trigger": "Use when ... (when it applies)",
  "scope": "user | machine | project | task   (memories and rules; default user)",
  "project": "absolute path of the project, for scope=project",
  "evidence": [
    {"source": "opencode", "session": "ses_...", "quote": "verbatim excerpt"}
  ]
}
```

`kind` defaults to `skill` for backwards compatibility. Skills must describe a reusable procedure; memories are durable facts or preferences; rules are negative constraints ("never X") that prevent recurring mistakes. Poppy adds `id`, `status`, `created_at`, and a stored `excerpt` per evidence item (so review and authoring still work after transcripts rotate away). Decay proposals are internal candidates with `kind: "decay"` that reference a library entry. Validation rejects: unknown source/session, unverifiable quotes, high-confidence secrets, duplicates of pending/installed entries, and previously rejected proposals.

**Skill** (written by the writer agent, validated by Poppy):

- `SKILL.md` with YAML frontmatter: `name` (kebab-case, matches directory, ≤64 chars) and `description` (≤1024 chars, ideally containing "Use when").
- Body: concise, imperative, evidence-backed; size-bounded.
- Canonical copy in `~/.poppy/library/skills/<name>/`; mirrored into each configured `skills_dirs` entry (copy or symlink) with provenance in `~/.poppy/installed.json`.

**Memory / rule entry** (built deterministically from an accepted candidate):

- Markdown file under `~/.poppy/library/{memory,rules}/<scope>/<id>.md` with frontmatter: `id`, `kind`, `title`, `scope`, `project`/`machine` when relevant, `status`, `pinned`, `created`, `last_verified`, `source_candidate`.
- Body: the statement, when it applies, and an evidence section with the verified quotes.
- Surfaced only through `poppy context` (and `poppy library`); Poppy never edits user context files.

## 8. Promotion UI

`poppy ui` serves a localhost-only review page:

- **Queue** — candidates with title, summary, trigger, and evidence excerpts. Skills: accept (writer drafts a SKILL.md) or reject. Memories/rules: pick a scope and accept, or reject. Accepting writes directly into the library.
- **Decay** — proposals to archive entries that have not been used or verified in `decay_after_days`. Resolve with archive / keep / pin. Archiving moves the entry to `library/archive/` and removes its mirrors; nothing is deleted.
- **Library** — the canonical Poppy-owned content, grouped by kind, with scope, pinned state, and last-verified age. Actions: verify, pin/unpin, archive, and (for skills) uninstall.

Rejected candidates are remembered so the miner is not asked to judge them again.

## 9. Reliability and failure modes

| Failure | Effect | Recovery |
| --- | --- | --- |
| Miner crashes or times out | nothing new in the queue | next scheduled run; window overlaps so nothing is lost |
| Miner hallucinates a quote | candidate discarded at validation | evidence gate; nothing reaches the UI |
| Harness changes its storage format | source stops resolving | `doctor` / `sources test` flags it; fix is a config edit (re-run the installer prompt) |
| Transcripts rotate away | evidence still available | excerpts are stored with the candidate at validation time |
| Agent skips loading memories | context not applied | the memory index is materialized into context files (V2.5), so headlines are always visible; full entries are one `poppy library show` away |
| Schedule never fires | no new candidates | `poppy schedule status` shows the timer; installer fires one run to prove it |
| Same entry edited on two machines | rebase aborts, both versions stay local | `poppy sync status` shows the divergence; resolve in `~/.poppy` and re-run |
| Machine offline during sync | local commits stay put | next run pushes them; nothing is lost |
| UI unavailable | no effect on data | queue is files on disk; CLI can accept/reject/install |

## 10. Security

- The miner runs headlessly against untrusted-ish content (transcripts can embed tool output and web text). Treat its environment as untrusted: it only needs read access to the session sources and write access to the Poppy inbox. Use a restricted agent profile where the harness supports it; never expose credentials to it that it does not need.
- Candidates are validated and human-reviewed before installation; no automatic execution of anything mined.
- High-confidence secret patterns reject candidates outright; excerpts are stored locally and never sent anywhere by Poppy itself (the configured agent sends prompts to its model provider, as it already does for normal sessions).
- The UI binds to `127.0.0.1` only and holds no credentials.

## 11. Roadmap

**V1 — skills (done).** Installer prompt, transcript toolbox, miner/writer prompts, candidate pipeline, review UI, scheduler, doctor, tests. Single machine, local state.

**V2 — memories, rules, and decay (done).** Same mining pipeline, three artifact kinds: `skill`, `memory`, `rule`. Accepted memories and rules become entries in the canonical **library** (`~/.poppy/library/`), surfaced to agents through `poppy context` (a builtin `poppy-context` skill teaches agents when to call it). A deterministic decay scan proposes stale entries (unused for `decay_after_days`, default 90) into the same review queue; nothing is archived without approval, pinning exempts an entry, and archives are restorable. Harness copies are *derived mirrors*:

- **Canonical:** `~/.poppy/library/{skills,memory,rules,archive}` — Poppy-owned, one tree, the thing worth backing up or syncing. Never mixed into user skill directories or user context files.
- **Mirrors:** skills are **copied** into each configured `skills_dirs` entry (never symlinked — symlinked skill directories behave inconsistently across harnesses and installers). The manifest records provenance so `poppy uninstall` removes only Poppy's own copies.
- **No user-file writes:** Poppy never edits `AGENTS.md`, memory directories, or any file outside `~/.poppy`. Rules are surfaced on demand via `poppy context`; materializing a managed block into AGENTS.md is explicitly deferred (V3+) to avoid entangling user-managed context files — derive such blocks, never sync them.
- **Usage tracking:** `poppy context` records `last_used` in `~/.poppy/state/usage.json` (state, not library, so library files stay stable under sync).

**V2.5 — memory delivery: progressive disclosure (done).** Loading memories is only reliable if the agent sees *that they exist* without loading them all. Poppy therefore materializes one generated digest, `~/.poppy/context/poppy.md`:

- **Binding rules in full** (user + this-machine scope; few by design, and constraints must be exact).
- **Headlines only** for memories and scoped rules: `- [scope] Title (ref) — trigger`, with an explicit "fetch before acting" instruction.
- **Full entries on demand:** `poppy library show <ref>` (unique short refs work).

The digest is regenerated deterministically after every approval, archive, pin, install, and sync; it has hard budgets (20 binding rules, 40 headlines, 8 KB) and drops the least-used entries with a note. The installer wires it into the harness's native include mechanism where one exists, or inserts a delimited managed block (`poppy context wire --file …`), and must prove visibility with `poppy context verify` (a canary headless run). Nothing executes at session time, and there is no per-harness code: delivery is files, placement is installer configuration, and the proof is a passing test. `pin` remains only a decay exemption — "must always apply" is what rules are for.

**V3 — sync + scopes (done).** One private git repo per user, rooted at `~/.poppy`; `poppy sync init --remote <url>` sets it up, `poppy sync run` commits, fetches, rebases, pushes, then materializes locally (skill mirrors reconciled against the library, digest regenerated). Exit codes: 0 = nothing to do, 1 = synced/materialized or a soft remote error to retry, 2 = conflict or hard error. Offline runs soft-fail and push on the next run; conflicts abort the rebase and are surfaced (`poppy sync status`), never auto-merged. `poppy schedule install` also installs a frequent sync timer (default every 30 minutes, `sync.interval_min`) when sync is enabled. Uninstall/archive propagates: the library deletion syncs and other machines remove their mirrors on the next run. The inbox stays local (validated candidates are the shared artifact); usage, mirrors, logs, and indexes are per-machine and rebuilt locally. Project-scope entries can be keyed by git remote (`remote:<url>`) so the same project matches at different paths on different machines.

**V3.5 — installer polish (done).** Two setup-time improvements, both prompt-only (no per-harness code):

- **Reuse an existing data repo.** Before creating one, the installer reuses `sync.remote` when this machine is already configured, asks whether the user has a Poppy data repo from another machine, and otherwise checks the user's account for one (a `library/` tree plus the Poppy `.gitignore` header). It offers to reuse what it finds and never adopts a repo without explicit confirmation. `poppy sync status` points at the same path when sync is not initialized.
- **Model selection for miner and writer.** The miner is the quality bottleneck (long-context judgment across many sessions, tool use); the writer is a constrained rewrite of one accepted candidate. The installer lists the models it can actually use, proposes concrete options with cost/quality trade-offs (one model for both / strong miner + cheap writer / strongest for both, or the CLI default), gets the user's approval, bakes model flags into `agent.miner.cmd` and `agent.writer.cmd`, and verifies both with `poppy doctor --agent`.

**V4 — productize (done).** Publishing flow (V4.0), CI (V4.0), packaging (V4.7), remote review UI (V4.8), installer self-sufficiency with no per-harness matrix (V4.9), and the release pipeline (V4.10).

- **Publishing (V4.0).** `poppy publish <skill> --to <checkout> [--subdir …] [--commit] [--push] [--force]` exports one reviewed skill into a public skills repo: validation is re-run, a destination that differs is never overwritten without `--force`, machine-specific details (home path, hostname) produce warnings, and the target checkout can be committed/pushed for you. The private library stays the working set; the public repo is a curated subset.
- **CI (V4.0).** GitHub Actions runs the stdlib test suite (Python 3.10 and 3.13) on every push and pull request.

**V4.5 — agent-first interface (done).** Users operate Poppy through their agent, not the CLI; the CLI is the API the agent drives.

- **Skills are the UI.** The installer mirrors builtin skills into the user's skill directories: `poppy` (router for status, review, library, mining, sync, publish, and doctor, with a hard "ask before promoting" rule), `poppy-context` (memory fetching, V2.5), and `poppy-propose` (capture mid-session). Builtins refresh by re-running `poppy init`: unmodified copies are updated, user-edited copies are left alone (`state/builtins.json`, machine-local).
- **The CLI is complete for agents.** Review actions missing from the CLI were added (`reject`, `discard`), `candidates show` includes the draft text, and the commands an agent drives take `--json` (`mine`, `accept`, `install`, `uninstall`, `reject`, `discard`, `publish`, `status`, `schedule status`, `sync run`). Command-specific exit codes are documented in the `poppy` skill.
- **Mid-session capture.** `poppy propose --file <candidate.json>` queues a candidate immediately: the agent supplies judgment plus verbatim quotes, Poppy locates the session, re-verifies the quotes, applies the miner's validation (secrets, dedupe), and lands it in the same queue. Promotion stays human.

**V4.6 — lifecycle: automatic sync + clean uninstall (done).**

- **Automatic sync is the default.** `poppy sync init` installs/refreshes the sync timer itself (systemd/launchd; on cron-only systems it prints the line), so machines sync without being prompted. `--no-schedule` opts out, `sync.interval_min` controls the cadence (default 30), and `poppy sync status` reports whether auto-sync is live.
- **Complete uninstall.** `poppy purge` prints a plan; `--yes` removes the schedule, Poppy's mirrored skills, the digest wiring blocks, and `~/.poppy`; `--keep-data` removes the integration but keeps the library and queue. Unmanaged skills are never touched, the CLI checkout is left in place with an explicit removal command (it may be the thing running the purge), and the `poppy` skill requires an informed decision before the agent runs it.

**V4.7 — packaging (done).** Poppy installs as a normal Python distribution (`poppy-ai`; the console script stays `poppy`) with all runtime data — prompts, builtin skills, review UI — shipped inside the package, so an installed copy is self-contained. The installer prompt prefers `pipx install git+https://github.com/dbmrq/poppy.git`, falls back to pip, then to a checkout; `poppy update` detects the install mode and runs the right command, then refreshes the builtin skills with the new code; `poppy doctor` reports the install mode and version; `poppy purge` prints the matching removal command. Schedulers use the checkout's `bin/poppy` or `python -m poppy` depending on how Poppy was installed, and CI installs the built package into a fresh venv and smoke-tests it.

**V4.8 — remote review UI (done).** `poppy ui` still binds localhost by default; `--host` binds elsewhere, and a non-loopback bind without a token is refused unless `--insecure` explicitly acknowledges the risk. With a token, every request needs HTTP Basic auth (any username, the token as the password) and POSTs must be `application/json`, so a cross-site form cannot act on the library. The token can come from `ui.token` or `--token`; startup output states the URL and the auth mode. Meant for a LAN address, an SSH tunnel, or an authenticating proxy (Cloudflare Access, Tailscale) — the UI can accept skills, so it is an admin endpoint.

**V4.9 — installer self-sufficiency, no per-harness matrix (done).** Source discovery is a procedure, not a list: the installer prompt walks any harness through finding its own transcript store (own CLI export commands first, then the conventional data directories, then per-session files → SQLite → single history file), configuring it with the three generic readers, and proving it with `poppy sources test` plus a real end-to-end read. Awkward shapes — one history file for everything, or an exotic format — are handled by a small read-only extractor configured as a `command` source, kept in `~/.poppy/sources/`. The table of known harnesses is explicitly labeled examples/shapes, not a support list: adding a harness is something that harness's own agent does at install time, in the prompt, not in Poppy's code.

**V4.10 — release pipeline (done).** Publishing is tag-driven: bump `__version__`, tag `vX.Y.Z`, and the `release` workflow checks the tag against the package version, builds the sdist and wheel, publishes to PyPI with trusted publishing (OIDC — no tokens), renders the Homebrew formula from the sdist's PyPI URL and sha256, and creates a GitHub release with all artifacts. The tap (`dbmrq/homebrew-tap`) keeps `Formula/poppy-ai.rb` current from PyPI with a daily secret-free workflow; `poppy update --check` reports whether a newer version exists (PyPI for pip/pipx installs, upstream commits for checkouts) and `poppy update` applies it; the installer prompt prefers `pipx install poppy-ai` with the git URL as fallback. One-time setup (a PyPI pending trusted publisher and the `dbmrq/homebrew-tap` formula) is documented in `packaging/README.md`.

**V4.11 — a calmer review flow + legend (done).** The review UI was collapsed to three choices per card — install, rewrite, reject: "discard draft" is gone (the CLI keeps `discard` as a primitive for agents), the pending-skill action is "Write skill" (no double accept before Install), and failed/invalid/writer-rejected drafts offer "Rewrite…" instead of a separate retry. `poppy accept <id> --instructions "<what to change>"` (and the rewrite box in the UI) carries reviewer steering into the writer prompt and stores it on the candidate (`writer_instructions`, capped, cleared by an empty note) so the box prefills on the next rewrite. A Legend button in the header explains every option — install/rewrite/reject, scope, verify, pin/unpin, archive, uninstall, restore, decay resolutions — opens by default on a first visit, and remembers the choice per browser.

**V4.12 — one verb per decision; decay by itself (done).** The review vocabulary is collapsed: **Accept** is the single promotion action (a skill draft is installed straight into the library and agent skill dirs; a memory or rule becomes active at the chosen scope), replacing the old accept-then-install split; **Archive** is the single removal action (a skill's "Uninstall" was the same thing, minus a word). Decay is no longer a proposal: `poppy decay` (and the decay scan inside each mining run) archives entries nobody used or verified for `decay_after_days` — pinned entries and builtin skills stay exempt — and leaves one card per archived entry in the same queue; the card is the undo (Restore brings it back and refreshes the clock, Archive confirms and clears it), and restoring from the Archived list also refreshes the clock so decay never bounces an entry back. `poppy decay --dry-run` previews, `--resolve <id> --resolution restore|archive` resolves. The review UI also drops its Verify button — pin is how an entry is protected, otherwise decay decides (`poppy library verify` stays a CLI primitive, and both restore paths refresh the clock) — and the header is now an info button. The panel is three terse sections: Buttons (write, accept, rewrite, reject, pin/unpin, archive, restore), Behavior (automatic decay, nothing deleted, rejections and notes remembered), and Paths (the library and the agents' skill dirs, which used to sit in the header). It opens on the first visit only and remembers the collapsed state per browser. Library rows show usage only (no verified date), the scope selector sits on its own line above the button row, and while a writer runs the card shows a live elapsed clock with the page polling every second (five seconds when idle).

**V4.13 — settings in the review UI (done).** A gear beside the info button opens the editable slice of `config.json`: the miner and writer commands (one argv token per line — switching a model means editing the `--model` token, and the field help explains `{prompt}`/`{prompt_file}`/stdin), their timeouts, the mining lookback, `decay_after_days`, and `skills_dirs`. `poppy.settings` owns the field spec, so the same code drives the panel, validation (unknown keys, whole numbers in range, non-empty commands, and the program must exist on PATH), and the write — a bad value is rejected with a readable message before `config.json` is touched, and only the fields the user changed are submitted. A save mutates the running server's config (no restart) and persists it; the panel shows the config path and UI address. The demo edits an in-memory copy with binary checks skipped. The state poll also re-schedules itself right after an action, so a fresh writer run is tracked within a second.

**V4.14 — one panel at a time, and the gear runs things (done).** The info and settings panels are mutually exclusive: opening one closes the other and remembers both states per browser, and the settings button is a real cog. The settings card gained an Actions section — *Run doctor* (the fast checks, rendered inline with ✓/⚠/✗), *Test agent commands* (`poppy doctor --agent`), and *Trigger miner*, which runs `poppy mine` in a background thread, writes progress to `state/mine.json`, and reports the elapsed clock, the summary (sessions/candidates/invalid/decayed), or the error in the panel; the page polls every second while a run is active. `pipeline.mine` now takes a `mine` lock, so the UI, the CLI, and the scheduled timer can never run two mining passes at once.

**V4.15 — macOS/Homebrew scheduling fixes (pre-install audit).** `launch_command()` now prefers the `poppy` console script on PATH when it demonstrably belongs to this install (its real target sits next to the running interpreter), so launchd and systemd jobs point at the stable `bin` shim instead of a versioned interpreter path that `brew upgrade` deletes — found before installing on the Mac, where the tool comes from Homebrew. The launchd plists also carry `HOME` alongside `POPPY_HOME`, since scheduled runs expand `~` in `skills_dirs`. Wheel/sdist contents were verified (`demo.py`, `settings.py`, all prompts and builtins) and a fresh-venv install of the wheel passed `init`, `doctor`, and `ui --demo` end to end.

**V4.16 — a stricter bar, proactive capture, lean facts, steadier UI, and mining cadence (done).** The miner prompt now states the qualifier plainly — *could a capable agent work this out in a few minutes on its own?* — and defaults to no unless the session shows real work (a failed attempt, a correction, a dead end, roughly three or more turns); the `poppy-propose` skill carries the same bar plus an explicit "propose proactively after successful work, then point the user at the UI", and the always-on digest repeats that nudge every session. Fact entries are lean now: `fact_body` stores one paragraph (summary + trigger) and evidence stays on the source candidate and in the review UI, so agents never read the same information twice (`delivery_body` strips legacy evidence sections at read time). The UI keeps state across its polling: expanded `<details>`, open boxes, typed values, and scope selections survive re-renders, and it never replaces a card while one of its inputs has focus; both panels start collapsed every time. Scheduling gains a cadence — `schedule.mine` = daily / every-other-day / weekly / smart / off (`poppy schedule install --mine …`, asked in the installer prompt), where *smart* runs a daily `poppy mine --if-due` that skips until `schedule.smart_min_sessions` (default 5) new sessions have accumulated; every mining run records progress and outcome in `state/mine.json` for the UI and the due-check.

### Nice-to-haves (recorded, not yet built)

**V4.17 — review by email (done).** New candidates are announced by mail, with per-candidate Accept/Reject links. Deliberately small and generic: SMTP only — no provider preset table and no API senders — because host/port/TLS are protocol facts while a provider's values are something the installing agent looks up (and `poppy email test` proves, reporting the SMTP error verbatim). `poppy email set|show|test` is the agent-drivable surface (`--json`, the password never echoed); the credential goes to Poppy's 0600 config, and `SMTP_*` environment variables override stored settings on machines that already have them. Notifications fire when a mining run or a proposal queues candidates (a mining run triggered from the UI opts out, since the user is watching); the mail carries the kind, title, trigger, one evidence line, and links. Tokens are HMAC-signed over candidate + action + expiry with a machine-local secret, expire after 14 days, and are consumed on apply; a GET only ever renders a confirmation page (no state change on link prefetch), and the POST applies the decision and shows the outcome. Doctor reports email readiness. Deferred until someone actually needs them: provider presets (they go stale), one-key HTTP senders (provider-specific code), and mailbox-free push channels.

- **Miner follow-ups:** track entry usage from transcripts (a skill loaded mid-session is visible in the session JSON) so decay can use real usage rather than age alone.

## 12. Long game: the seven rules

V1 uses rules 1-3 implicitly; V2/V3 add the rest.

1. **Narrow by default; widen only by promotion.** New artifacts start at the narrowest scope that fits.
2. **Promotion requires independent evidence.** Cross-context recurrence (different project/machine/day), counterevidence checked, human approved. Same-context repetition never promotes.
3. **Review what changes blast radius.** One queue for candidates, promotions, retirements, supersession, and context-file edits.
4. **Decay by use, not age alone.** Unused → stale → archived (restorable). `pin` exempts. Contradictions or harmful feedback go straight to review.
5. **Supersede, never overwrite.** Contradictions create a successor and invalidate the predecessor with a link. Conflicts surface; they are never silently merged.
6. **Context files are caches with budgets.** Rules are negative constraints; anything that must always hold belongs in hooks or CI. Adding requires removing or compressing.
7. **Capture locally; sync with git; index locally.** Per-machine inboxes; no database syncing; search indexes are rebuilt locally; offline soft-fail.

## 13. Non-goals

- No chat interface, no agent runtime, no model host.
- No MCP server in V1 (revisit only if a concrete need appears).
- No vector database, graph store, or embedding service at personal scale.
- No automatic promotion or automatic deletion, ever.
- No per-harness code in the core.

## 14. Open questions

- Default schedule cadence (currently weekly: Mon 09:00, 30 min jitter).
- Whether accepted skills should optionally be pushed to a shared/public skills repo from the UI (V4).
- Miner model/budget defaults per harness; V1 leaves them to the installer with caps in config.
- Whether Poppy should ship a small self-description skill so interactive agents can propose candidates mid-session without being asked (deferred).
