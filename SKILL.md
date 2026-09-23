---
name: field-guide
description: Write or refresh a field guide that explains how a codebase works, one concept per chapter, end to end across layers, built incrementally so it scales to very large repos. Use when asked for an onboarding guide, primer, codebase walkthrough, "how does this codebase work" doc, or to continue or refresh an existing field guide.
argument-hint: "[<topic> | help | status | which <path> | plan | next [N] | slice <id> | finish | update | rewrite <id|all> | build | usage | config]"
---

# Field guide

Produces one document: a field guide an engineer new to a codebase reads to build a real mental model
of it, organised by concept (one slice = one chapter, end to end across layers), written by default as a
senior colleague's walkthrough. It's built one slice at a time with all state on disk, so the cost per
chapter doesn't grow with the size of the repo.

## Rules

These keep token usage bounded. Follow them exactly.

1. **You (the orchestrator) never read source code.** Scripts inventory the code, subagents read it,
   and you read only their short replies and script output.
2. **One subagent per slice per step, within the resolved `research_budget`.** Research writes cited
   facts to `notes/` and checks its own citations. The writer works from the notes only.
3. **Nothing is read twice.** Notes persist. The overview and closing come from `FG digest`, not from
   re-reading chapters or code. Refreshes re-research only slices whose relied-on files changed.
4. **Mechanical work goes through `FG`**: inventory, sizing, citation checks, staleness, assembly.
5. **Don't read the brief files yourself.** Pass their paths to the subagents.
6. **Use the resolved config.** Run `FG config --json` once per session and pass its values on. For a
   model of `inherit`, omit the Agent tool's `model` parameter; otherwise pass the name.
7. **Log every subagent run.** Each time an agent returns, take the usage from its result (total
   tokens, tool uses, duration) and log it before doing anything else:
   `FG usage log <id> <step> <tokens> --model <m> --tool-uses <n> --duration-ms <ms>`, where step is
   `planner`, `research`, `write`, `bookends`, `reviewer` or `followup`, id is `-` for guide-level
   steps, and `--note` marks a refresh or rewrite. Estimates switch from fallbacks to measured medians
   as the log grows; `FG usage` reports totals by step, model and slice.

`FG` means `python3 <this skill's dir>/scripts/fg.py`, run from the repo root.

## Workspace

Default `field-guide/` at the repo root; `FG locate` finds it up to 3 levels deep.

```
field-guide.config.json        the guide's definition (committed)
field-guide.config.local.json  personal overrides: models, budget (gitignored)
map.md, inventory.json         scripted inventory
plan.json                      volumes, and slices: volume, type, direction, entry, status, hashes
usage.jsonl                    one line per subagent run (gitignored)
notes/NN-slug.md               cited fact sheets
chapters/NN-slug.md            prose (00-overview and 99-closing are the bookends)
glossary.md                    terms, appended per chapter
GUIDE.md / guide.html          assembled output (FG build)
FINDINGS.md                    every open gap, severity-tagged and cited (FG build)
DOC_FINDINGS.md                docs that disagree with the code, triaged (FG docs)
```

**One workspace, however big the repo.** A codebase too large for a single sequence is split into
**volumes**, and they all live here: one `plan.json`, one glossary, one chapter numbering that runs
continuously across volumes, one built guide with a sidebar group per volume. A volume is a named slice
of `scope`, not another workspace, so a chapter in one volume can refer to a chapter in another and
`which`, `topic` and the coverage page see the whole repo. Never run `init` a second time to make a
volume; use `FG volume add` and `FG volume start`.

Config resolves in four layers: built-in, user (`~/.claude/field-guide.json`), workspace, local. For
`help` or `config`, read `references/config.md`, which covers the keys, the model defaults and the
budgets.

## Commands

- no argument: `FG locate`. No workspace: **Setup**. No slices yet: **Plan**. Otherwise `FG status`,
  then **Select and run**.
- **anything else that isn't a command below** (e.g. `notification system`, `checkout`, `device sync`):
  a topic. Go to **Topic guide**.
- `help`: list these commands one line each, plus the config summary from `references/config.md`.
- `status`: `FG status`, plus `FG volume list` when the guide has volumes.
- `topic <terms>`: `FG topic <terms>` alone, reported in a line or two. (A bare topic with no such
  chapters yet is a **Topic guide** request, not this.)
- `which <path...>`: `FG which <paths>`, then report in one or two lines which chapter to read before
  touching those files, and say so plainly when none covers them. This is the way in for someone about
  to change code they haven't seen in a while; don't read the chapter yourself to answer it.
- `usage`: `FG usage`, shown compactly.
- `plan`: **Plan** again (keeps slices that aren't `proposed`).
- `next [N]`: run the next N (default 1) selected or proposed slices in order, without asking.
- `slice <id>`: **Run one slice** for that id.
- `finish`: show `FG remaining --include-proposed`'s total in one line, then open **the picker** with
  `--default-run all`, and run `run_now` in order. Then **Bookends**, `FG build`, **Doc fixes** and the final report.
- `update` (or `refresh`): **Refresh**.
- `rewrite <id|all>`: re-run only the Write step from existing notes (after changing `tone`, `depth`
  or `assumes`, or after a review), in plan order; then **Bookends** and `FG build`.
- `build`: **Bookends** if any chapter is newer than them, then `FG build`.
- `docs`: **Doc fixes**, without rebuilding.
- `config`: show `FG config` compactly and apply changes with `FG config --set`. For model or budget
  changes, ask "just this guide or all repos?" and offer the models with `AskUserQuestion`.

## Setup

**First, `FG locate`.** If a workspace already exists, do not `init` over it — that is how a guide gets
destroyed, because the planner then writes a fresh slice list and the existing chapters become
unreachable. A new area of the same repo is a **volume** of the existing guide (see **Topic guide**);
only a genuinely separate codebase gets its own `--dir`. `init` refuses to re-scope a workspace holding
written chapters, and `FG status` warns about chapter files no slice claims.

Run `FG config --json` (works without a workspace) for the user's defaults, then ask with
`AskUserQuestion`, skipping anything already answered:

- Call 1: workspace location (`new_workspace_dir` recommended, `docs/field-guide/`, or a gitignored
  folder); format (Markdown recommended, HTML, both); depth (standard recommended, orientation, deep);
  scope (whole repo, or directories via "Other").
- Call 2: tone (peer recommended, or teaching); research model (Sonnet recommended, Inherit, Opus,
  Haiku); writer model (Inherit recommended, Opus, Sonnet, Haiku); save the models for all repos (yes
  recommended, or just this guide). Skip the model questions if the user file already sets `models`;
  just name the models in use.

**Multi-repo guides.** Run `FG siblings` during setup. It reports checkouts next to this one that
build packages of the same name - the signal that they are one product split across repos, not merely
two repos by the same team. If one scores high, name it and ask (one `AskUserQuestion`, default no)
whether concepts that span it should be followed across; the user may also ask for this directly.
On yes, add `--repo <name>=<path>` to `init`, once
per repo including the current one, with short names the user will recognise. Put the workspace in one
of the repos or in their common parent. Subagents will read the other repos, so if those are outside
this session's working directory, tell the user to add them first (`/add-dir <path>`, or start Claude
with `--add-dir`); otherwise agents stall on permission prompts. `FG map` then also reports code shared
across the repos, and drifted copies go into FINDINGS.md.

Then `FG init --dir <dir> --format <f> --depth <d> --tone <t> [--scope a,b]`; `FG config --set
models.<op>=<m>` for answers that differ from the defaults (add `--user` to save for all repos);
`FG map`. Report the map's headline in one or two lines.

## The picker

Slice and volume choices go through a browser picker, since real plans have too many slices for
`AskUserQuestion`.

1. Run `FG pick` with `run_in_background: true`. Tell the user in one line that it's open, with the
   `picker: <url>` it prints. **End your turn**; you're re-invoked when the process exits (on confirm,
   cancel, or a 30-minute timeout).
2. Read `<dir>/.pick-result.json`. The picker has already updated `plan.json`.
   - `{"action": "run", "run_now": [ids]}`: run exactly those, in order.
   - `{"action": "volume", "volume", "paths"}`: `FG volume start <volume>`, `FG map`, then **Plan**
     for that volume, here in this same workspace. The slice view also lists volumes not started yet.
   - `cancel` or `timeout`: stop and say so in one line.
3. If no browser can open it (e.g. SSH): `FG pick --no-open` and give the URL. If that fails too, fall
   back to `AskUserQuestion` with `multiSelect: true`, 16 slices per call, foundations recommended.

## Topic guide

`/field-guide <topic>` builds a guide for one area of the codebase, as a volume of the same guide.

1. **Check what already exists.** No workspace: **Setup** first (scope: leave empty, the topic sets it).
   Otherwise `FG topic <terms>`, passing the topic's words. If it reports chapters that already cover
   the topic, stop and put the choice to the user with `AskUserQuestion`, in one line each: refresh the
   stale ones (**Refresh**), extend with the concepts they don't cover (continue below, and tell the
   scout what's already covered so it doesn't re-scope it), or build it fresh anyway. Never silently
   re-research something a current chapter already explains.
2. **Scout the paths.** Spawn `models.planner`: "Read `<skill>/references/scout-brief.md` and follow it.
   Topic: `<topic>`. Workspace: `<dir>`. Skill dir: `<skill>`." For an extend, add "Already covered, do
   not scope to these: `<chapter titles>`."
3. **Make it a volume.** From the scout's JSON: `FG volume add <id> --title "<title>" --paths <a,b,c>`
   with the next free letter, then `FG volume start <id>`, then `FG map`.
4. **Span check.** For each repo in the config beyond this one, `FG probe <alias> <vocabulary>`. If a
   sibling holds a real share of the concept, say so in one line with the numbers ("notifications:
   592 mentions across 96 files in web") and ask whether this guide should follow it across. On yes,
   the volume's paths gain that repo's areas as `alias:path`, and the planner is told the concept spans
   both. On no, note it in `not_covered` so nobody re-asks.
5. **Plan** it, passing the scout's `vocabulary` and `related` to the planner, then **Select and run**.

A topic volume is a volume like any other: same chapter numbering, same glossary, one built guide.

## Plan

Spawn a planner (`general-purpose`, `models.planner`): "Read `<skill>/references/planner-brief.md`
and follow it. Workspace: `<dir>`. Skill dir: `<skill>`." When planning inside a volume, add "Volume:
`<id>`; tag every slice you add with it."

If it proposed **volumes**, run the picker, then `FG volume start <id>` and `FG map`, and **Plan**
again — in this same workspace, which now holds both volumes. Volumes are never separate workspaces:
one directory, one glossary, one chapter numbering, one built guide. Otherwise continue with
**Select and run**.

## Select and run

Run the picker. For each id in `run_now`, do **Run one slice**. Research for independent slices may
run in parallel (at most 3); writing is sequential in plan order, since each chapter builds on the
earlier recaps. Then `FG build` and the final report.

## Run one slice

A slice whose notes already exist skips step 1 (`FG remaining` shows this).

1. **Research** (`general-purpose`, `models.research`): "Read `<skill>/references/research-brief.md`
   and follow it. Slice: `<slice JSON>`. Notes: `<dir>/notes/<id>-<slug>.md`. Budget: `<research_budget>`.
   Citation checker: `python3 <skill>/scripts/fg.py --dir <dir> check <id>`." For a multi-repo guide,
   add "Repos: `<FG repos output>`".
2. **Verify**: `FG check <id>`. If anything still fails, delete the failing claims from the notes; don't
   start another research round. `FG mark <id> --status researched`.
3. **Write** (`general-purpose`, `models.writer`): "Read `<skill>/references/writer.md` and follow it.
   Slice: `<slice JSON>`. Notes: `<notes path>`. Chapter: `<dir>/chapters/<id>-<slug>.md`. Glossary:
   `<dir>/glossary.md`. Config: `<tone, audience, assumes, depth, where_to_look>`. Earlier recaps:
   `<FG digest --recaps-only>`."
4. If the writer reports **Missing** facts that matter, run at most one narrow follow-up research request
   (5-file budget), append the result to the notes, and have the writer revise. Report **Conflicts** to
   the user.
5. `FG mark <id> --status done`, which records the relied-on file hashes for Refresh.

## Doc fixes

The guide is the only thing that reads the documents and the code side by side, so it is the only
thing that can tell you a document has gone wrong. After a full run, offer to act on that.

1. `FG docs`. No findings: say so in one line and stop.
2. Report the counts in one line (`3 critical, 5 major, 2 minor; 2 unverified`), then run **the
   picker** with `--docs`. Unverified findings are shown but can't be selected.
3. `cancel`/`timeout`, or nothing selected: stop, and say the report is in `DOC_FINDINGS.md`.
4. Otherwise spawn `models.writer`: "Read `<skill>/references/doc-brief.md` and follow it. Workspace:
   `<dir>`. Apply these findings: `<the selected rows from doc-findings.json>`."
5. Report what it applied and what it skipped, one line each, and say the edits are uncommitted.

Never edit a document without the picker, and never apply an unverified finding. A wrong correction
to a document outlasts a wrong chapter, because people and agents act on documents.

## Bookends

When a chapter is newer than the overview or closing: spawn `models.bookends`: "Read
`<skill>/references/bookends-brief.md` and follow it. Workspace `<dir>`. Digest: `<FG digest>`."

## Refresh

1. `FG map`, then `FG stale`.
2. Ask with `AskUserQuestion` (multiSelect) which stale slices to refresh, and whether any large
   uncovered area deserves a new slice (that means **Plan** again).
3. For each: research in refresh mode (pass the old notes path and the changed files), `FG check`,
   the writer revises the chapter, `FG mark <id> --status done`.
4. **Bookends** and `FG build`. Mention the refresh once in the overview, never in chapter bodies.

## Reader review (on request, or after the first 2-3 chapters)

Spawn `models.reviewer` on the chapter files only: "You're a capable engineer new to this codebase.
Explain each section back in two sentences, then list anything you had to reread, couldn't follow, or
found patronizing, over-explained or padded. Under 300 words." Pass the findings to a rewrite of the
affected sections.

## Final report

Keep it short: chapters done, pending and skipped (`FG status`); tokens used this run (from the
usage log); output paths; the `[high]` findings in
`FINDINGS.md`, one line each; anything unverified or in conflict; the command to continue.
