# Install Poppy

You are an AI coding agent. Install Poppy — a weekly skill-mining system — for yourself on this machine. Poppy is harness-agnostic: **you** are the harness-specific part. Work through the steps in order and verify each one before moving on. Ask the user only for things you cannot detect (schedule yes/no, lookback window). Keep messages short.

If `~/.poppy` already exists, treat this as an update: read the existing config first and preserve the user's choices. If `poppy` is already on PATH, run `poppy update` and `poppy init` (step 1) instead of reinstalling.

## 1. Install the tool

Prefer an isolated install (no sudo) and verify it before moving on:

```bash
# preferred: pipx, from PyPI when the release is published
pipx install poppy-ai
# if PyPI does not have it yet, install from git instead:
pipx install git+https://github.com/dbmrq/poppy.git
```

On macOS, if the user prefers Homebrew: `brew tap dbmrq/tap && brew install poppy-ai`.

If pipx is unavailable, use `python3 -m pip install --user poppy-ai` (or the same command with the git URL above, or `pip install` inside a virtualenv). If neither works — no pip, or an externally-managed Python — fall back to a checkout:

```bash
git clone --depth 1 https://github.com/dbmrq/poppy.git ~/.local/share/poppy
~/.local/share/poppy/bin/poppy --version
```

Then initialize and verify:

```bash
poppy --version
poppy init
```

Make sure `poppy` is on PATH (pipx and `pip --user` do this automatically; for the checkout, symlink `~/.local/share/poppy/bin/poppy` into `~/.local/bin` or add it to the shell profile; report what you did).

## 2. Discover transcript sources

Poppy has no per-harness integrations — **you** are the harness-specific part. Find where *your* transcripts live, configure a source, and **verify it against real sessions**. Open the files/database and look; do not guess.

### Find the store

Work down this list for your harness, and for any other agent that has run on this machine:

1. **Your own CLI first.** Look for session/export commands (`<agent> --help`; subcommands like `sessions`, `history`, `logs`, `export`, `resume --list`). If one lists sessions and prints one as text, configure `type: command` — that is the most stable option.
2. **Your data directory.** Harnesses follow conventions: `~/.local/share/<name>/`, `~/.config/<name>/`, `~/.<name>/`, `~/Library/Application Support/<name>/` (macOS), VS Code-family extensions (`.../User/globalStorage/<publisher>.<extension>/`), or whatever your docs, `--help`, or environment (`$XDG_DATA_HOME`) mention.
3. **Prefer, in order:** per-session files (`*.jsonl`, `*.json`, `*.md` in a sessions directory) → a SQLite database (`*.db`, `*.sqlite`, `state.vscdb`) → a single history/log file for everything.
4. **If you cannot find a store, ask the user where transcripts live.** Never invent a source, and never configure one whose read returns metadata instead of conversation text.

Examples verified by other agents — treat them as *shapes*, not a support list, and do not stop at them:

| Harness | Shape |
| --- | --- |
| OpenCode v2 | SQLite (`~/.local/share/opencode/opencode.db`; `session_v2`, `session_message`) |
| OpenCode 1.x | same DB, older tables (`session`, `message`, `part`) |
| Pi | JSONL per session (`~/.pi/agent/sessions/**/*.jsonl`) |
| Claude Code | JSONL per project (`~/.claude/projects/**/*.jsonl`) |
| Codex | JSONL rollouts (`~/.codex/sessions/**/rollout-*.jsonl`; older files may be `.zst`) |
| Cursor | VS Code globalStorage SQLite (`state.vscdb`) |
| Gemini CLI | JSONL chats (`~/.gemini/tmp/*/chats/*.jsonl`) |

### Configure it

Three generic readers; pick the shape that fits:

- **`files`** — `path` (directory or file), optional `glob` (default `**/*`) and `extensions` (e.g. `[".jsonl"]`). Best for per-session files.
- **`sqlite`** — read-only `list_query` (may use `:limit`) and `read_query` (must return a column named `text`, one row per message; Poppy joins rows with blank lines). Recognized list columns: `id`/`session_id`/`sessionId`, `time`/`time_updated`/`updated`/`time_created`, `title`/`name`, `cwd`/`directory`. Timestamps may be seconds or milliseconds.
- **`command`** — `list_cmd` prints a JSON array of sessions (items with an id, plus optional time/title/cwd); `read_cmd` prints one session as text (`{id}` is substituted).

Awkward shapes are still covered:

- **One file for everything** (a chat history or log): point `files` at it (it becomes one long session — coarse but workable), or write a small read-only splitter and configure it as a `command` source.
- **An exotic format:** the same escape hatch — a tiny read-only extractor in `~/.poppy/sources/` configured as a `command` source. Keep extractors read-only and inside `~/.poppy`.

Verified SQLite example (OpenCode v2, tested 2026-09):

```json
{
  "name": "opencode",
  "type": "sqlite",
  "description": "OpenCode v2 sessions",
  "path": "~/.local/share/opencode/opencode.db",
  "list_query": "SELECT id, title, directory AS cwd, time_updated FROM session_v2 ORDER BY time_updated DESC LIMIT :limit",
  "read_query": "SELECT CASE WHEN type='user' THEN json_extract(data,'$.text') ELSE (SELECT group_concat(json_extract(value,'$.text'), char(10)) FROM json_each(session_message.data,'$.content') WHERE json_extract(value,'$.type') IN ('text','reasoning')) END AS text FROM session_message WHERE session_id = :id ORDER BY seq"
}
```

### Prove it

Add each source (`poppy sources add --file <tmpfile>`) and run `poppy sources test <name>` until it passes. Then read a session end-to-end (`poppy sessions read <id> --source <name>`) and confirm it is real conversation text. **A source that does not pass is not configured.** Some harness commands only cover the current project; prefer a database/file source when you need all projects. `poppy doctor` must show every source healthy when you are done.

## 3. Configure the agent commands

Poppy runs agents headlessly. Determine how to invoke **yourself** non-interactively, with permissions that do not need a human, and with the prompt as an argument. Use `{prompt}` as the placeholder. Examples (verify the flags with `--help`):

- Claude Code: `["claude", "-p", "{prompt}"]`
- OpenCode: `["opencode", "run", "--auto", "--format", "json", "{prompt}"]`
- Codex: `["codex", "exec", "{prompt}"]` (check approval flags)
- Pi: check its CLI for a print/non-interactive mode

Set both roles:

```bash
poppy config set agent.miner.cmd '["<agent>", "...", "{prompt}"]'
poppy config set agent.writer.cmd '["<agent>", "...", "{prompt}"]'
```

If the CLI reads stdin instead of taking a prompt argument, omit the placeholder. `{prompt_file}` substitutes a temp file path. Then run `poppy doctor --agent`; both agent tests must pass.

### Choose models for the miner and writer

The two roles are not equally hard, so do not pick models by accident:

- **Miner** — reads many sessions, decides what is worth keeping, and writes candidates. This is the quality bottleneck and the expensive run: it needs a long context and reliable tool use.
- **Writer** — turns one accepted candidate plus its evidence into a `SKILL.md`. Shorter and more constrained; a mid-tier model is usually enough.

Find the models you can actually use (ask your own CLI — e.g. a `models` subcommand — and check which providers are authenticated), then propose 2–3 concrete options and let the user approve one:

1. one capable model for both roles (simplest; the default if the user is unsure),
2. a strong model for the miner and a cheaper one for the writer (best cost/quality split),
3. the strongest model available for both (best quality, highest cost).

Keeping your CLI's default model is also fine if the user prefers. Very cheap models tend to produce candidates that waste review time — the evidence gate catches invented quotes, not weak judgment — so if the user is cost-sensitive, split the roles rather than downgrading the miner. Whatever you choose must run non-interactively and use tools. Put the model flag in both configured commands, make sure `poppy doctor --agent` still passes, and report which models you chose and why.

## 4. Skills directories

`poppy init` auto-detected the skill directories that already exist on this machine (e.g. `~/.agents/skills`, `~/.claude/skills`, `~/.config/opencode/skills`, `~/.pi/agent/skills`). Check that the list covers where you actually load skills from, and fix it if not:

```bash
poppy config get skills_dirs
poppy config set skills_dirs '["~/.agents/skills"]'
```

At least one directory must be writable. Poppy ships builtin skills (`poppy`, `poppy-context`, `poppy-propose`) that teach your agent how to operate it; `poppy init` mirrors them here, so the user can ask you for Poppy tasks afterwards.

## 5. Wire the memory index

Poppy materializes a small always-on digest at `~/.poppy/context/poppy.md`: binding rules in full, plus one-line headlines for memories and scoped rules. Wire it into the mechanism you already use for global instructions, so every session sees it without any runtime hook:

```bash
poppy context export
```

- **Preferred:** point your harness's native include/instructions mechanism at that file (for example, OpenCode's `instructions` config entry, or an import of `~/.poppy/context/poppy.md` in Claude Code's `CLAUDE.md`). This keeps Poppy content in its own file.
- **Otherwise:** insert a managed block into your global context file (`AGENTS.md`, `CLAUDE.md`, …):

```bash
poppy context wire --file <path-to-global-context-file>
```

Then prove it actually reaches sessions:

```bash
poppy context verify
```

`verify` starts a headless session and asks the model to quote a line from the digest; it fails if the block is not visible. Fix the wiring and retry until it passes. Do not claim success without a passing verify.

## 6. Multi-machine sync (optional)

Ask the user whether they use Poppy on more than one machine. If yes, one **private** git repo becomes the shared library. Find out whether that repo already exists before creating anything:

1. If `~/.poppy/config.json` already has `sync.remote`, reuse it (this machine was configured before).
2. Ask the user whether they already have a Poppy data repo from another machine; if they give a URL, use it.
3. Otherwise check their account for one — for example `gh repo list --limit 200 --json name,url,isPrivate,description` — and look for a name or description suggesting Poppy data (`poppy-data`, `poppy-library`, …). A real Poppy data repo contains a `library/` tree and a `.gitignore` starting with `# Machine-local state — never synced`; confirm that before offering it. Never adopt a repo without the user's explicit confirmation, and never touch a repo that is not theirs.
4. Only if none exists, offer to create an empty private repo (for example `gh repo create poppy-data --private`) — do not initialize it with a README — and use its URL.

Then run (on this and every other machine, with the same URL):

```bash
poppy sync init --remote <private-repo-url>
```

`~/.poppy` itself becomes the git repo: the library, candidate queue, and clean rejections are tracked; machine-local state (config, sources, mirrors, usage, logs, drafts) is ignored. The command commits, pushes, and materializes mirrors on this machine. It also installs the **automatic sync timer** (systemd/launchd; every `sync.interval_min`, default 30 minutes) so machines stay in sync without being prompted — pass `--no-schedule` if the user does not want background jobs. Conflicts are never auto-merged: `poppy sync status` explains what to resolve. Skip this step entirely if the user has one machine.

## 7. Schedule

Ask the user before installing a weekly job. Then:

```bash
poppy schedule install    # systemd user timer (Linux), launchd (macOS), or prints cron lines
poppy schedule status
```

When sync is enabled, this also installs a frequent sync timer. Do **not** wait for the timers to fire; prove the pipeline with a manual run instead.

## 8. First mining run

Ask the user for a lookback window (default: 7 days for the first run). Then:

```bash
poppy mine --since 7d
```

This invokes the miner agent, which may take several minutes. Candidates can be **skills** (reusable procedures), **memories** (facts and preferences), or **rules** (negative constraints). When it finishes, report how many candidates were accepted, then point the user at the review UI:

```bash
poppy ui    # http://127.0.0.1:8788
```

In the UI: accepting a skill runs a writer agent and produces a `SKILL.md`; accepting a memory or rule writes a scoped entry into the Poppy library. Nothing is ever written into the user's own skill directories or context files — harness skill directories only receive mirrors of library skills, and memories/rules are surfaced on demand via `poppy context`.

No candidates is an acceptable outcome — say so plainly rather than forcing candidates. If every candidate was rejected as invalid, read `~/.poppy/logs/mine-*.log`, fix the likely cause (usually a source or agent configuration problem), and retry once.

## 9. Report

Summarize concisely:

- sources configured, and how each was verified
- agent commands configured (miner, writer)
- skills directories
- schedule state
- how to review the queue (`poppy ui`; for another device, `poppy ui --host <addr> --token <secret>`)
- how to load memories (`poppy library show <ref>`, indexed in the digest)
- that the builtin skills are installed, so the user can ask you to operate Poppy (review, library, mining, sync, publish, doctor)
- how to share a reviewed skill (`poppy publish <name> --to <checkout of a public skills repo>`)
- how to remove Poppy later (`poppy purge` previews; `poppy purge --yes` removes everything, `--keep-data` keeps the library)
- memory index wiring: where you wired it, and that `poppy context verify` passed
- sync (if enabled): the remote, and that `poppy sync status` is clean
- anything that failed or could not be verified

Never claim success for a step you did not verify.
