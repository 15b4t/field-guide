# Configuration reference

Read this only for `/field-guide help` and `/field-guide config`.

## Four layers

Each layer overrides the ones above it. `null` in a layer means "not set here".

| Layer | File | Committed | Holds |
|---|---|---|---|
| built-in | `fg.py` | n/a | recommended defaults |
| user | `~/.claude/field-guide.json` | no | your models and defaults for new guides, across all repos |
| workspace | `<workspace>/field-guide.config.json` | yes | the guide's definition, so everyone who refreshes it gets the same document |
| local | `<workspace>/field-guide.config.local.json` | no (`init` gitignores it) | your overrides for this one guide |

`fg.py config` prints every resolved value with the layer it came from; `--json` prints the values
alone. `--set key=value` edits one layer: personal keys go to local (or to user when there's no
workspace), and guide keys go to workspace. `--user`, `--local` or `--workspace` overrides the choice,
and `--set key=null` removes a key from a layer.

## Guide keys (workspace layer)

- `format`: `md` (default), `html` or `both`.
- `tone`: `peer` (default; a senior colleague's walkthrough) or `teaching` (patient, defines everything,
  for readers new to the field).
- `depth`: `orientation`, `standard` (default) or `deep`. Sets chapter length and the research budget.
- `assumes`: what the reader already knows, e.g. "Go, React, REST". The planner fills it in if null.
- `audience`, `title`, `where_to_look` (a key-files footer per chapter, default true).
- `scope` (path prefixes), `exclude` (globs), `include_tests`, `volume_threshold_lines`.
  `include_tests` (default false) controls the **inventory** only: whether test files count toward
  `map.md`'s line totals, slice sizing and the volume threshold. Leave it off unless you want tests
  sized as subject matter. Researchers look up the tests covering a concept either way, for each
  chapter's "Before you change this" footer.
- `repos`: only for a guide spanning several repos, set by `init --repo name=path`. Maps each short
  name to a directory, stored relative to the workspace so the committed config works for anyone with
  the same side-by-side layout. With `repos` set, every path is written `name:path`, and `scope` may use
  a bare name for a whole repo (`["api", "web:src/pages"]`). Without it, nothing changes.

`init` writes `format`, `tone` and `depth` explicitly into the workspace file (from flags, then user
defaults, then built-in), so a teammate with different defaults still produces the same guide.

## Personal keys (user or local layer)

- `models`, one per operation: `inherit` (the session's model), `opus`, `sonnet`, `haiku` or `fable`.

  | operation | default | why |
  |---|---|---|
  | `planner` | inherit | one cheap run, and the slicing decides everything downstream |
  | `research` | sonnet | the largest share of tokens; mostly grep and targeted reads, verified by `check` |
  | `writer` | inherit | the prose is the product |
  | `bookends` | inherit | the overview and closing are what readers see first |
  | `reviewer` | sonnet | reads chapters only and flags problems |

- `research_budget`: reading caps for each research agent. Unless set, scaled by depth:

  | depth | max_files | max_read_lines | notes_max_words |
  |---|---|---|---|
  | orientation | 15 | 1,500 | 1,000 |
  | standard | 25 | 3,000 | 1,600 |
  | deep | 40 | 6,000 | 2,400 |

  These are judgment calls, not measurements. The one data point: at 30 files / 4,000 lines, every
  set of notes landed at the 1,800-word cap, which suggests reading beyond what notes can hold. A
  partial dict overrides individual keys.
- `new_workspace_dir`: the location Setup suggests first (default `field-guide`).

## Cost estimates

Every subagent run is logged to `<workspace>/usage.jsonl` (gitignored, one JSON object per line:
`at`, `id`, `step`, `tokens`, and optionally `model`, `tool_uses`, `duration_ms`, `note`).
`fg.py usage` reports totals by step, model and slice; `fg.py usage --json` prints the raw log.

`fg.py remaining` and the picker estimate tokens per step. They start from unmeasured fallbacks
(research 75k, writing 30k, overview/closing 60k at standard depth, scaled by depth) and switch to the
median of this workspace's logged runs once a step has at least two. Every estimate is printed with
its basis.
