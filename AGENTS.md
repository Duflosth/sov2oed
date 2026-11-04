# AGENTS.md

Instructions for an AI coding agent (GitHub Copilot, Codex, Cursor, Claude Code...) working in this repository.

## Converting a schedule for a user

When the user gives a statement of values (xlsx, csv, PDF) and wants OED files, a quality score or questions for
the broker, follow [`skills/sov2oed/SKILL.md`](skills/sov2oed/SKILL.md). Run its commands as `sov2oed ...` (after
`pip install -e .`) instead of `python3 scripts/sov2oed_cli.py ...`. You write the reading plan, and for a PDF the
script that writes its rows file; the code reads every value. Never type a value from the schedule into a plan:
a value you state cites its cell and quotes it, or says `assumed: true`.

`sov2oed auto FILE --out OUT` does the same steps with a model called through its API (`sov2oed/agent.py`); use it
only when the user asks for it, since it sends the schedule to the model's provider.

## Changing the code

- Code, comments and docs in English. Each function serves a stated need or a real file that required it.
- Before a commit: `ruff check . && ruff format --check . && mypy && pytest`.
- Tests run on the fictional schedules of `fixtures/` only. Real schedules never enter the repository: a test
  rejects any file whose digest is listed in `tests/data/corpus.sha256`.
- `plugin/skills/` is generated from `skills/` and `sov2oed/` by `python tools/build_skill.py`: never edit it by
  hand; run the tool after a change (CI checks it is up to date).
- `RULES.md` documents every rule of `sov2oed/check/rules.py`: change both together.
