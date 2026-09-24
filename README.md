# field-guide

A Claude Code skill that writes a field guide to a codebase: one concept per chapter, followed end to
end across every layer it touches, in the voice of a senior colleague walking you through it.

It is built one chapter at a time with all state on disk, so the cost of a chapter doesn't grow with
the size of the repo. The orchestrator never reads source code — scripts inventory it, subagents read
it, and every fact lands in a citation-checked fact sheet before any prose is written.

## Install

```sh
git clone https://github.com/15b4t/field-guide ~/.claude/skills/field-guide        # personal, all repos
git clone https://github.com/15b4t/field-guide <repo>/.claude/skills/field-guide  # one project
```

Restart Claude Code, then run `/field-guide`. Needs python3 (standard library only); the picker opens
a local page in your browser.

## Using it

```
/field-guide                     set up, plan, and pick chapters to write
/field-guide notification system a guide for one area, scoped by searching the repo for it
/field-guide which <path>        which chapter explains this file, and whether it has moved since
/field-guide status              what is written, pending and stale
/field-guide update              re-research only the chapters whose code changed
/field-guide docs                docs that contradict the code, triaged for fixing
```

A large repo is split into **volumes**, which all live in one workspace: one glossary, one continuous
chapter numbering, one built guide with a section per volume. Chapters carry a freshness line, so a
reader coming back months later can see how far the code has moved since it was written.

Two things fall out of reading the docs and the code side by side:

- **Doc drift** — documents that contradict the code, triaged critical/major/minor. You pick which to
  correct in a browser picker; a finding whose citation no longer resolves is shown but can't be applied.
- **Concepts that cross repos** — a split frontend and backend is one product, and a concept whose
  halves live in different checkouts is one chapter. The skill finds related checkouts, measures how
  much of a concept lives in each, and asks before following it across.

## Output

A workspace (default `field-guide/`) holding the guide plus the state that makes it cheap to extend:

```
guide.html / GUIDE.md   the guide, with a searchable contents and a coverage map
FINDINGS.md             problems in the code, found by reading it, each one cited
DOC_FINDINGS.md         documents that disagree with the code
notes/                  cited fact sheets, one per chapter — refreshes re-read only what changed
plan.json               chapters, volumes, and the file hashes that detect staleness
```

## Layout

```
SKILL.md              orchestrator instructions
scripts/fg.py         inventory, planning state, citation checks, staleness, assembly
scripts/*.html        guide and picker templates
references/*.md       subagent briefs: planner, scout, research, writer, bookends, doc fixes
references/config.md  config keys, model defaults, budgets
```

Configuration resolves in four layers — built-in, user (`~/.claude/field-guide.json`), workspace,
local — so the guide's definition can be shared while models and budgets stay personal.

## Versions

See `git tag`. A fork tuned to one codebase lives in that project's `.claude/skills/` under its own
name and backports anything generally useful here.
