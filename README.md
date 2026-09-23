# field-guide

A Claude Code skill that writes a field guide to a codebase: one concept per chapter, end to end
across layers, built one slice at a time with all state on disk so cost per chapter stays flat on
very large repos.

## Install

    git clone <this repo> ~/.claude/skills/field-guide     # personal, all repos
    git clone <this repo> <repo>/.claude/skills/field-guide # single project

Restart Claude Code, then run `/field-guide`. Requires python3 (standard library only); the slice
picker opens a local browser page.

## Layout

    SKILL.md              orchestrator instructions
    scripts/fg.py         all mechanical work: inventory, planning state, citations, staleness, build
    scripts/*.html        guide and picker templates
    references/*.md       subagent briefs: planner, scout, research, writer, bookends; config reference

## Versions

See `git tag`. Forks for a specific codebase (e.g. `biddaan-field-guide`) live in that project's
`.claude/skills/` and backport anything generally useful here.
