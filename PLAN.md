# Poppy — Plan

_Poppy turns coding-agent session history into reusable skills. It is harness-agnostic by construction: the agent you already use installs it, and an agent mines for it._

Status: **V1** (skills only). This document is the persistent design and is updated as phases land.

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
`poppy ui` (localhost) ── accept / reject candidates
        │                 accept ─▶ writer agent drafts SKILL.md from evidence
        │                 draft validated ─▶ install / discard
        ▼
installed skills ── copied into the configured skills directories
```

Nothing in the loop requires the miner and the user to share a session, a machine, or a harness.

## 4. Architecture

### Repository layout

```
bin/poppy            entry point (executable)
src/poppy/           Python 3 stdlib package (no third-party imports)
  cli.py             subcommands
  config.py          ~/.poppy/config.json
  sources.py         transcript sources: files | sqlite | command
  sessions.py        list/search/read toolbox (miner-facing, deterministic)
  candidates.py      candidate schema, validation, inbox processing, dedupe
  skills.py          SKILL.md validation, install/uninstall, manifest
  agent.py           headless agent invocation ({prompt} / {prompt_file})
  prompts.py         prompt rendering ({{PLACEHOLDER}} substitution)
  doctor.py          environment + installation checks
  schedule.py        systemd user timer | launchd | cron instructions
  ui.py              review UI server (stdlib http.server, localhost only)
prompts/miner.md     recurrent judgment: find reusable procedures with evidence
prompts/writer.md    turn one accepted candidate into a SKILL.md
install/PROMPT.md    the copy-paste installer prompt (the product's front door)
ui/index.html        review UI (no external assets)
examples/            example source configurations
tests/               fixtures + stdlib unittest
```

### State layout (`~/.poppy`, override with `POPPY_HOME`)

```
config.json          settings (see below)
sources.json         transcript sources written by the installer
inbox/               miner output drop directory (validated on the way in)
candidates/          validated candidates (one JSON file each)
drafts/<id>/         writer output (SKILL.md + optional supporting files)
rejected/            invalid candidates + rejection index (so they are not re-proposed)
installed.json       manifest of Poppy-installed skills
logs/                one log per run (mine-*.log, accept-*.log)
locks/               per-candidate locks (no double writer runs)
```

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
| Review/promotion | `poppy ui` | human only |

## 6. Transcript access: three tiers

The installer picks the best tier per harness and **verifies it against real sessions**:

1. **Harness commands** (`type: command`) — prefer the harness's own listing/export commands. Stability is delegated to the harness. `list_cmd` must print a JSON array whose items carry an id (plus optional time/title/cwd); `read_cmd` prints the session text (`{id}` substituted).
2. **Generic readers** (`type: files`, `type: sqlite`) — JSONL/text directories, or a read-only SQLite connection with two queries (`list_query`, `read_query`; the read query must alias a text column as `text`). Works for most harnesses without per-harness code.
3. **Custom command** — if neither fits, the installer writes a small read-only extractor and configures it as tier 1.

Example configs that are verified against real installations live in `examples/sources.example.json`.

## 7. Candidate and skill formats

**Candidate** (written by the miner into the inbox, validated by Poppy):

```json
{
  "title": "<= 100 chars",
  "summary": "what the procedure is and why it is reusable (2-5 sentences)",
  "trigger": "Use when ...",
  "evidence": [
    {"source": "opencode", "session": "ses_...", "quote": "verbatim excerpt"}
  ]
}
```

Poppy adds `id`, `status`, `created_at`, and a stored `excerpt` per evidence item (so review and authoring still work after transcripts rotate away). Validation rejects: unknown source/session, unverifiable quotes, high-confidence secrets, duplicates of pending/installed skills or previously rejected candidates.

**Skill** (written by the writer agent, validated by Poppy):

- `SKILL.md` with YAML frontmatter: `name` (kebab-case, matches directory, ≤64 chars) and `description` (≤1024 chars, ideally containing "Use when").
- Body: concise, imperative, evidence-backed; size-bounded.
- Installed by copying into each configured `skills_dirs` entry, with provenance in `~/.poppy/installed.json`.

## 8. Promotion UI

`poppy ui` serves a localhost-only review page:

- **Queue** — candidates with title, summary, trigger, evidence excerpts; accept / reject (with reason).
- **Drafts** — SKILL.md preview after the writer runs; install / discard.
- **Installed** — Poppy-installed skills with uninstall.

Rejected candidates are remembered so the miner is not asked to judge them again.

## 9. Reliability and failure modes

| Failure | Effect | Recovery |
| --- | --- | --- |
| Miner crashes or times out | nothing new in the queue | next scheduled run; window overlaps so nothing is lost |
| Miner hallucinates a quote | candidate discarded at validation | evidence gate; nothing reaches the UI |
| Harness changes its storage format | source stops resolving | `doctor` / `sources test` flags it; fix is a config edit (re-run the installer prompt) |
| Transcripts rotate away | evidence still available | excerpts are stored with the candidate at validation time |
| Schedule never fires | no new candidates | `poppy schedule status` shows the timer; installer fires one run to prove it |
| UI unavailable | no effect on data | queue is files on disk; CLI can accept/reject/install |

## 10. Security

- The miner runs headlessly against untrusted-ish content (transcripts can embed tool output and web text). Treat its environment as untrusted: it only needs read access to the session sources and write access to the Poppy inbox. Use a restricted agent profile where the harness supports it; never expose credentials to it that it does not need.
- Candidates are validated and human-reviewed before installation; no automatic execution of anything mined.
- High-confidence secret patterns reject candidates outright; excerpts are stored locally and never sent anywhere by Poppy itself (the configured agent sends prompts to its model provider, as it already does for normal sessions).
- The UI binds to `127.0.0.1` only and holds no credentials.

## 11. Roadmap

**V1 — skills (this repo).** Installer prompt, transcript toolbox, miner/writer prompts, candidate pipeline, review UI, scheduler, doctor, tests. Single machine, local state.

**V2 — memory + decay.** The same pipeline with additional destinations: memory entries (on-demand files with an index) and rules (small AGENTS.md additions, negative constraints only). A monthly pass proposes stale entries for archive; nothing is removed without approval.

**V3 — sync + scopes.** One private data repo per user; per-machine inbox namespaces; derived indexes regenerated locally; an automatic sync agent (systemd/launchd) that commits, rebases, pushes, and materializes after every pull. Scopes: user > machine > project > task, resolved at read/install time.

**V4 — productize.** More sources verified by the installer, packaging, remote UI option, publishing flow from the private library to a public skills repo.

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
