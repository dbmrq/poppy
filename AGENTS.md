# Working on Poppy

Poppy turns coding-agent session history into reviewed skills, memories, and rules.
It is installed by pasting one prompt into whichever agent the user runs.

## Poppy is harness-agnostic — keep it that way

Poppy has no per-harness integrations and no support matrix. The installing
agent **is** the harness-specific part: it finds its own transcript store,
headless invocation, and instruction mechanism, and proves each one before
relying on it.

One rule follows, and it is absolute for this repository:

- **Install instructions and agent-facing prompts must not contain
  harness-specific instructions.** No "if you are harness X, do Y" recipes, no
  named command lines, no transcript paths keyed to one product, no support
  entry for a harness however popular. A future harness nobody has heard of yet
  must be able to follow `install/PROMPT.md` without it being updated.
- **Write requirements and verification, not mechanisms.** State what must be
  true — the source must return real conversation text; the agent command must
  work non-interactively; the command must resolve in the scheduler's minimal
  environment; the digest must reach a fresh session — and give the command
  that proves it (`poppy sources test`, `poppy doctor --agent`,
  `poppy context verify`). Let the installing agent discover the mechanism.
- **Shapes are fine, products are not.** Describing the *shape* of a store
  (per-session JSONL, a SQLite database with one message row per record, a
  single history file, a CLI that prints sessions as text) or of a headless
  invocation (prompt argument, `{prompt_file}`, stdin) is encouraged; attaching
  those shapes to product names in instructional text is not.
- Product names may appear only in non-instructional places: tests, fixtures,
  demo data (`src/poppy/demo.py`), and the skill-directory detection
  convenience list (`COMMON_SKILL_DIRS` in `src/poppy/config.py`), which is
  explicitly labelled as detection convenience rather than support.

When a real install surfaces a harness-shaped problem, fix it generically in
Poppy — a new mechanism, documented, with a check that proves it — or leave it
for the installing agent to discover. Do not patch the prompt with one
harness's answer.

## Development

- Python 3.10+, standard library only — no runtime dependencies.
- Tests: `python3 -m unittest discover -s tests -t .` (also `poppy selftest`).
- Scheduler and agent code must survive minimal environments
  (launchd/systemd/cron): prefer absolute paths, carry a usable `PATH` into
  installed timers, and prove behavior under a stripped environment rather than
  only your shell.
- Releases are tag-driven: bump `src/poppy/__init__.py`, commit, tag, push —
  see `packaging/README.md`. The Homebrew tap follows PyPI automatically.
