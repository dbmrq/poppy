# Install Poppy

You are an AI coding agent. Install Poppy — a weekly skill-mining system — for yourself on this machine. Poppy is harness-agnostic: **you** are the harness-specific part. Work through the steps in order and verify each one before moving on. Ask the user only for things you cannot detect (schedule yes/no, lookback window). Keep messages short.

If `~/.poppy` already exists, treat this as an update: read the existing config first and preserve the user's choices. If `poppy` is already on PATH, run `poppy update` and `poppy init` (step 1) instead of reinstalling; if `poppy update` says a package manager owns the install, upgrade through that manager instead (for Homebrew: `brew upgrade poppy-ai`).

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
2. **Your data directory.** Session stores follow ordinary conventions: `~/.local/share/<name>/`, `~/.config/<name>/`, `~/.<name>/`, `~/Library/Application Support/<name>/` (macOS), editor or IDE plugin storage (a `globalStorage/<publisher>.<extension>/` directory), or whatever your docs, `--help`, or environment (`$XDG_DATA_HOME`) mention.
3. **Prefer, in order:** per-session files (`*.jsonl`, `*.json`, `*.md` in a sessions directory) → a SQLite database (`*.db`, `*.sqlite`, `state.vscdb`) → a single history/log file for everything.
4. **If you cannot find a store, ask the user where transcripts live.** Never invent a source, and never configure one whose read returns metadata instead of conversation text.

There is no list of known stores to consult: open the directories, list the tables, and look at real rows until you find conversation text. For a SQLite store, Poppy needs two queries — a `list_query` selecting one row per session (`id` plus any of `time*`, `title`/`name`, `cwd`/`directory`) and a `read_query` returning one row per message with a column named `text`, in time order. If messages are stored as JSON blobs, extract the text in SQL; if several parts make up one assistant turn, concatenate them.

### Configure it

Three generic readers; pick the shape that fits:

- **`files`** — `path` (directory or file), optional `glob` (default `**/*`) and `extensions` (e.g. `[".jsonl"]`). Best for per-session files.
- **`sqlite`** — read-only `list_query` (may use `:limit`) and `read_query` (must return a column named `text`, one row per message; Poppy joins rows with blank lines). Recognized list columns: `id`/`session_id`/`sessionId`, `time`/`time_updated`/`updated`/`time_created`, `title`/`name`, `cwd`/`directory`. Timestamps may be seconds or milliseconds.
- **`command`** — `list_cmd` prints a JSON array of sessions (items with an id, plus optional time/title/cwd); `read_cmd` prints one session as text (`{id}` is substituted).

Awkward shapes are still covered:

- **One file for everything** (a chat history or log): point `files` at it (it becomes one long session — coarse but workable), or write a small read-only splitter and configure it as a `command` source.
- **An exotic format:** the same escape hatch — a tiny read-only extractor in `~/.poppy/sources/` configured as a `command` source. Keep extractors read-only and inside `~/.poppy`.

### Prove it

Add each source (`poppy sources add --file <tmpfile>`) and run `poppy sources test <name>` until it passes. Then read a session end-to-end (`poppy sessions read <id> --source <name>`) and confirm it is real conversation text. **A source that does not pass is not configured.** Some harness commands only cover the current project; prefer a database/file source when you need all projects. `poppy doctor` must show every source healthy when you are done.

## 3. Configure the agent commands

Poppy runs agents headlessly. Determine how to invoke **yourself** non-interactively, with permissions that do not need a human, and with the prompt as an argument. Inspect your own CLI (`--help`; look for a run/print/exec mode, approval flags, and an output format) — do not copy a command line from anywhere, including this document. Use `{prompt}` as the placeholder:

```bash
poppy config set agent.miner.cmd '["<agent>", "...", "{prompt}"]'
poppy config set agent.writer.cmd '["<agent>", "...", "{prompt}"]'
```

If the CLI reads stdin instead of taking a prompt argument, omit the placeholder. `{prompt_file}` substitutes a temp file path. Then run `poppy doctor --agent`; both agent tests must pass.

Scheduled runs do not get your shell environment. Poppy embeds a PATH into installed timers and `poppy doctor` checks that your commands resolve under it, but anything else that depends on your interactive shell (aliases, exported variables, startup files) will be missing on a timer. Prefer an absolute program path, and pass any extra environment inside the command itself — for example a small wrapper script. You will re-verify once the schedule exists (step 7).

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

`poppy init` auto-detected the skill directories that already exist on this machine. Check that the list covers where you actually load skills from, and fix it if not:

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

- **Preferred:** point whatever mechanism your harness *actually loads* for global instructions at that file — an include, an import, a native instructions path. Do not trust acceptance as evidence: find the place a fresh session really reads, and keep Poppy content in its own file when the mechanism allows it.
- **Otherwise:** insert a managed block into your global context file:

```bash
poppy context wire --file <path-to-global-context-file>
```

A config entry that is accepted but never loaded is a common trap; if the digest does not arrive in a fresh session, the mechanism does not count. Then prove it actually reaches sessions:

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

The timer runs in the scheduler's environment, not your shell. If git credentials come from shell startup files, exported variables, or a vault, the timer will not see them and every scheduled sync will stay offline while local commits pile up. `poppy sync init` probes this and reports whether the timer can reach the remote. When it cannot, point `sync.env_file` at a readable `KEY=value` file that holds the credentials (machine-local — it is never synced), then re-run `poppy sync init`:

```bash
poppy config set sync.env_file '~/.cache/<your-vault>/env'
poppy sync init --remote <private-repo-url>
```

## 7. Schedule

Ask the user before installing a weekly job. Then:

```bash
poppy schedule install    # systemd user timer (Linux), launchd (macOS), or prints cron lines
poppy schedule status
```

When sync is enabled, this also installs a frequent sync timer. After installing, run `poppy doctor --agent` once more: the schedule check verifies the agent commands resolve under the timer's PATH, which is smaller than your shell's. Do **not** wait for the timers to fire; prove the pipeline with a manual run instead.

## 8. First mining run

Ask the user for a lookback window (default: 7 days for the first run). Then:

```bash
poppy mine --since 7d
```

This invokes the miner agent, which may take several minutes. Candidates can be **skills** (reusable procedures), **memories** (facts and preferences), or **rules** (negative constraints). When it finishes, report how many candidates were accepted, then point the user at the review UI:

```bash
poppy ui --demo    # optional: the same UI over mock data, nothing is written
poppy ui           # http://127.0.0.1:8788
```

In the UI: a skill candidate starts with **Write skill** (drafts it with the writer agent), then **Accept** installs it; a memory or rule is accepted directly at a scope. Each card also offers **Rewrite** (with an optional note that steers the writer) and **Reject** (remembered so it is not proposed again). The ⓘ panel explains every option; the gear opens the settings the installer wrote (miner/writer commands and models, timeouts, decay window, skill directories) with **Run doctor**, **Test agent commands**, and **Trigger miner** actions. Stale entries are archived automatically by decay (pinned ones never are); each gets a card in the queue to **Restore** or confirm.

Nothing is ever written into the user's own skill directories or context files — harness skill directories only receive mirrors of accepted library skills, and memories/rules are surfaced on demand via `poppy context`.

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
