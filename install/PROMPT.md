# Install Poppy

You are an AI coding agent. Install Poppy — a weekly skill-mining system — for yourself on this machine. Poppy is harness-agnostic: **you** are the harness-specific part. Work through the steps in order and verify each one before moving on. Ask the user only for things you cannot detect (schedule yes/no, lookback window). Keep messages short.

If `~/.poppy` already exists, treat this as an update: read the existing config first and preserve the user's choices.

## 1. Install the tool

```bash
git clone https://github.com/dbmrq/poppy.git ~/.local/share/poppy
~/.local/share/poppy/bin/poppy init
```

Make `poppy` available on PATH (symlink `~/.local/share/poppy/bin/poppy` into `~/.local/bin`, or add it to the shell profile; report what you did). Verify with `poppy --version`.

## 2. Discover transcript sources

You know where your own transcripts live. Inspect this machine, and for every harness store that exists, configure a source and **verify it against real sessions**. Open the files/database and look — do not guess.

Likely locations (use what exists; ignore the rest):

| Harness | Where transcripts usually live |
| --- | --- |
| OpenCode v2 | `~/.local/share/opencode/opencode.db` (SQLite; `session_v2`, `session_message`) |
| OpenCode 1.x | same DB (older tables: `session`, `message`, `part`) |
| Pi | `~/.pi/agent/sessions/**/*.jsonl` |
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**/rollout-*.jsonl` (older files may be `.zst`) |
| Cursor (macOS) | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` |
| Gemini CLI | `~/.gemini/tmp/*/chats/*.jsonl` |

Prefer the harness's own commands when they exist (`type: command`): run `--help`, confirm the listing output is JSON, and confirm `read` returns text. Otherwise use `type: files` for JSONL/text directories, or `type: sqlite` with a read-only query.

Verified example for OpenCode v2 (tested 2026-09):

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

Reader contract:

- `list_query` may use `:limit`; extra parameters are ignored. Recognized column names: `id`/`session_id`/`sessionId`, `time`/`time_updated`/`updated`/`time_created`, `title`/`name`, `cwd`/`directory`. Timestamps may be seconds or milliseconds.
- `read_query` must return a column named `text`, one row per message; Poppy joins rows with blank lines.
- `files` sources take `path` (directory or file), optional `glob` (default `**/*`) and `extensions` (e.g. `[".jsonl"]`).
- `command` sources take `list_cmd` (must print a JSON array) and `read_cmd` (prints text; `{id}` is substituted).

Add each source (`poppy sources add --file <tmpfile>`) and run `poppy sources test <name>` until it passes. **A source that does not pass is not configured.** Note that some harness commands only cover the current project; prefer a database/file source when you need all projects.

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

## 4. Skills directories

`poppy init` auto-detected the skill directories that already exist on this machine (e.g. `~/.agents/skills`, `~/.claude/skills`, `~/.config/opencode/skills`, `~/.pi/agent/skills`). Check that the list covers where you actually load skills from, and fix it if not:

```bash
poppy config get skills_dirs
poppy config set skills_dirs '["~/.agents/skills"]'
```

At least one directory must be writable.

## 5. Schedule

Ask the user before installing a weekly job. Then:

```bash
poppy schedule install    # systemd user timer (Linux), launchd (macOS), or prints a cron line
poppy schedule status
```

Do **not** wait for the timer to fire; prove the pipeline with a manual run instead.

## 6. First mining run

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

## 7. Report

Summarize concisely:

- sources configured, and how each was verified
- agent commands configured (miner, writer)
- skills directories
- schedule state
- how to review the queue (`poppy ui`), how to load memories (`poppy context`)
- anything that failed or could not be verified

Never claim success for a step you did not verify.
