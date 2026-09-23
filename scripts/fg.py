#!/usr/bin/env python3
"""field-guide helper: every mechanical step of the skill, so the model never
spends tokens on inventory, bookkeeping, staleness or assembly.

Subcommands:
  locate                         print the workspace dir (searches cwd, depth <= 3)
  init --dir D [--format F] [--repo NAME=PATH ...]
                                 create workspace, field-guide.config.json, plan.json
  config [--set K=V] [--user|--local|--workspace] [--json]
                                 resolved config with each value's layer; edit one layer
  map [--dir D]                  inventory the codebase -> map.md + inventory.json (+ shared.json)
  repos                          repo aliases -> directories (multi-repo guides)
  measure PATH...                sum in-scope lines under paths/prefixes
  status                         table of slices from plan.json
  check ID                       verify path:line citations in a slice's notes
  mark ID [--status S]           record file hashes a slice relied on; set status
  pick [--no-open] [--timeout S]  browser picker for slices/volumes; applies choice, prints RESULT json
  usage [--json]                 report on usage.jsonl: totals by step, model and slice
  usage log ID STEP TOKENS [--model M --tool-uses N --duration-ms N --note T]
                                 append one subagent run (ID '-' for planner/bookends)
  remaining [--include-proposed] unfinished slices, the step each resumes at, rough cost
  which PATH...                  which chapter explains a file, and whether it has changed since
  files ID                       the files a chapter was written from, marking changed ones
  volume [list|add|start]        volumes inside this workspace; one guide, one glossary, one numbering
  topic TERM...                  what the guide already says about a topic, before researching it again
  stale                          slices whose relied-on files changed; unassigned areas
  digest [--recaps-only]         chapter recaps (+ open gaps) for writers and bookends
  build                          assemble GUIDE.md / guide.html, and FINDINGS.md from notes

Standard library only.
"""
import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

CONFIG_MARKER = "field_guide"
WS_CONFIG = "field-guide.config.json"            # the guide's definition, committed
LOCAL_CONFIG = "field-guide.config.local.json"   # personal overrides, gitignored
USAGE_LOG = "usage.jsonl"                         # one line per subagent run, gitignored
GITIGNORED = [LOCAL_CONFIG, USAGE_LOG, ".pick-result.json"]
LEGACY_CONFIGS = {"config.json": WS_CONFIG, "config.local.json": LOCAL_CONFIG}
DEFAULT_CONFIG = {
    CONFIG_MARKER: 1,
    "format": "md",                 # md | html | both
    "tone": "peer",                 # peer (senior colleague) | teaching (defines everything)
    "assumes": None,                # what the reader already knows; planner fills from the map
    "audience": "a capable engineer who has never seen this codebase",
    "depth": "standard",            # orientation | standard | deep
    "where_to_look": True,          # short key-files footer per chapter
    "scope": [],                    # path prefixes; empty = whole repo
    "exclude": [],                  # extra glob patterns
    "include_tests": False,
    "volume_threshold_lines": 250000,
    "research_budget": None,        # null = scaled by depth (BUDGETS); a dict overrides
    "models": None,                 # filled from MODEL_DEFAULTS; a dict overrides per operation
    "new_workspace_dir": "field-guide",  # suggested location when setting up a new guide
    "title": None,
}

# Research reading caps per depth. Notes are capped too, so reading far beyond what the notes can
# hold is paid for and then thrown away.
BUDGETS = {
    "orientation": {"max_files": 15, "max_read_lines": 1500, "notes_max_words": 1000},
    "standard":    {"max_files": 25, "max_read_lines": 3000, "notes_max_words": 1600},
    "deep":        {"max_files": 40, "max_read_lines": 6000, "notes_max_words": 2400},
}
# Model per operation. "inherit" = the session's model (omit the Agent tool's model param).
# Research is the biggest cost and is mostly grep + targeted reads; the writer's prose is the product.
MODEL_DEFAULTS = {
    "planner": "inherit",
    "research": "sonnet",
    "writer": "inherit",
    "bookends": "inherit",
    "reviewer": "sonnet",
}
MODEL_CHOICES = ["inherit", "opus", "sonnet", "haiku", "fable"]

PRUNE_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "out", "target", ".next", ".nuxt",
    "coverage", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    ".gradle", ".idea", ".vscode", "bower_components", ".turbo", ".cache", "tmp",
    ".svelte-kit", "Pods", "DerivedData",
}
SOURCE_EXT = {
    ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".m", ".mm", ".swift",
    ".java", ".kt", ".kts", ".scala", ".groovy", ".cs", ".fs", ".vb",
    ".py", ".rb", ".php", ".pl", ".lua", ".r", ".jl", ".ex", ".exs", ".erl", ".clj", ".hs", ".ml",
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro", ".dart",
    ".sql", ".graphql", ".proto", ".sh", ".bash", ".zsh", ".ps1",
    ".s", ".S", ".asm", ".zig", ".nim", ".v", ".sv", ".vhd",
}
TEST_PATTERNS = [
    "*_test.go", "*.test.*", "*.spec.*", "test_*.py", "*_test.py", "*Test.java", "*Tests.cs",
    "*/test/*", "*/tests/*", "*/__tests__/*", "*/spec/*", "*/testdata/*", "*/fixtures/*",
    "*/e2e/*", "*/cypress/*", "*/__mocks__/*",
]
GENERATED_PATTERNS = [
    "*.min.js", "*.min.css", "*.pb.go", "*_pb2.py", "*.generated.*", "*/generated/*",
    "*.d.ts", "*/migrations/*", "*.lock", "*-lock.json", "*.map",
]
ENTRY_PATTERNS = {
    "http route": re.compile(
        r"""(\b(app|router|r|e|g|api|server|mux|route)\.(get|post|put|patch|delete|all|use|route|GET|POST|PUT|PATCH|DELETE|Handle|HandleFunc)\s*\(\s*['"`/])"""
        r"""|(@(Get|Post|Put|Patch|Delete|RequestMapping|GetMapping|PostMapping|app\.route|router\.(get|post))\b)"""
        r"""|(\bHandleFunc\s*\()|(\bpath\(\s*['"])"""),
    "program main": re.compile(r"(^func main\(\))|(if __name__ == ['\"]__main__['\"])|(\bint main\s*\()|(public static void main\()", re.M),
    "cli command": re.compile(r"(cobra\.Command\{)|(@click\.command)|(argparse\.ArgumentParser\()|(\.command\(\s*['\"])|(yargs\b)"),
    "websocket/realtime": re.compile(r"(websocket\.Upgrader)|(new WebSocket\()|(\.on\(\s*['\"]connection['\"])|(socket\.io)|(@WebSocketGateway)"),
    "job/queue/cron": re.compile(r"(\bcron\.)|(node-cron)|(@Cron\()|(\bnew (Queue|Worker)\()|(\bcelery\b)|(\bsidekiq\b)|(\bconsumer\b.*subscribe)", re.I),
    "ui route/page": re.compile(r"(<Route\b)|(createBrowserRouter)|(path:\s*['\"]/)|(defineRoutes)"),
    "syscall/driver init": re.compile(r"(SYSCALL_DEFINE\d)|(module_init\()|(__initcall\()"),
}
UI_PAGE_GLOBS = ["*/pages/*", "*/app/*/page.*", "*/views/*", "*/screens/*", "*/routes/*"]


# ---------------------------------------------------------------- helpers

def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def ensure_gitignore(d):
    """Personal and machine-local files never get committed with the guide."""
    gi = os.path.join(d, ".gitignore")
    text = open(gi).read() if os.path.exists(gi) else "# personal and machine-local files; see fg.py\n"
    missing = [x for x in GITIGNORED if not re.search(r"(?m)^" + re.escape(x) + r"$", text)]
    if missing or not os.path.exists(gi):
        with open(gi, "w") as f:
            f.write(text.rstrip("\n") + "\n" + "".join(x + "\n" for x in missing))


def migrate(d):
    """Rename pre-rename workspace config files in place (config.json -> field-guide.config.json)."""
    old = os.path.join(d, "config.json")
    if os.path.exists(os.path.join(d, WS_CONFIG)) or not os.path.exists(old):
        return
    if CONFIG_MARKER not in (load_json(old, {}) or {}):
        return
    for a, b in LEGACY_CONFIGS.items():
        if os.path.exists(os.path.join(d, a)):
            os.rename(os.path.join(d, a), os.path.join(d, b))
    gi = os.path.join(d, ".gitignore")
    if os.path.exists(gi):
        text = open(gi).read()
        if LOCAL_CONFIG not in text:
            with open(gi, "w") as f:
                f.write(re.sub(r"(?m)^config\.local\.json$", LOCAL_CONFIG, text))
    print(f"note: renamed {d}/config*.json to {WS_CONFIG} / {LOCAL_CONFIG}", file=sys.stderr)


def locate(root="."):
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS or d == "tmp"]
        if depth >= 3:
            dirnames[:] = []
        if WS_CONFIG in filenames:
            return os.path.normpath(dirpath)
        if "config.json" in filenames:
            cfg = load_json(os.path.join(dirpath, "config.json"), {}) or {}
            if isinstance(cfg, dict) and CONFIG_MARKER in cfg:
                migrate(dirpath)
                return os.path.normpath(dirpath)
    return None


USER_CONFIG = os.path.expanduser(os.environ.get("FIELD_GUIDE_USER_CONFIG", "~/.claude/field-guide.json"))
# Keys about how *you* run the pipeline rather than what the guide is. They default to the local
# (gitignored) or user layer so a committed workspace never imposes one person's models on a team.
PERSONAL_KEYS = {"models", "research_budget", "new_workspace_dir"}
NESTED_KEYS = {"models", "research_budget"}


def config_layers(d):
    """(name, path, dict) for each layer, lowest precedence first. Missing files are empty."""
    builtin = json.loads(json.dumps(DEFAULT_CONFIG))
    builtin["models"] = dict(MODEL_DEFAULTS)
    out = [("built-in", None, builtin), ("user", USER_CONFIG, load_json(USER_CONFIG, {}) or {})]
    if d:
        for name, fn in (("workspace", WS_CONFIG), ("local", LOCAL_CONFIG)):
            path = os.path.join(d, fn)
            out.append((name, path, load_json(path, {}) or {}))
    return out


def resolve_layers(d):
    """Merge the layers (null = unset, so it never overrides a lower layer). Returns the resolved
    config and a map of dotted key -> layer name it came from."""
    merged, src = {}, {}
    for name, _, cfg in config_layers(d):
        for k, v in cfg.items():
            if v is None or k == CONFIG_MARKER:
                continue
            if k in NESTED_KEYS and isinstance(v, dict):
                merged.setdefault(k, {})
                for sk, sv in v.items():
                    if sv is not None:
                        merged[k][sk] = sv
                        src[f"{k}.{sk}"] = name
            else:
                merged[k] = v
                src[k] = name
    depth = merged.get("depth", "standard")
    budget = dict(BUDGETS.get(depth, BUDGETS["standard"]))
    for k in budget:
        src.setdefault(f"research_budget.{k}", f"depth={depth}")
    budget.update(merged.get("research_budget") or {})
    merged["research_budget"] = budget
    return merged, src


def workspace(args):
    d = getattr(args, "dir", None) or locate()
    if not d:
        die("no field-guide workspace found; run `fg.py init --dir <dir>` first")
    migrate(d)
    if load_json(os.path.join(d, WS_CONFIG)) is None:
        die(f"{d}/{WS_CONFIG} missing")
    ensure_gitignore(d)
    return d, resolve_layers(d)[0]


def is_git(cwd=None):
    return subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=cwd,
                          capture_output=True, text=True).returncode == 0


def git_head(cwd=None):
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


ALIAS_RX = re.compile(r"^[A-Za-z0-9_-]+$")


class Roots:
    """Where file keys live on disk.

    Single-repo mode (no `repos` in config): a key is a path relative to the current directory, exactly
    as before. Multi-repo mode: every key is `<alias>:<path relative to that repo>`, and `repos` maps
    each alias to a directory, stored relative to the workspace so a committed config works for anyone
    with the same side-by-side checkout layout."""

    def __init__(self, d, cfg):
        repos = cfg.get("repos") or {}
        base = os.path.abspath(d)
        self.multi = bool(repos)
        self.items = ([(a, os.path.normpath(os.path.join(base, p))) for a, p in repos.items()]
                      if self.multi else [("", os.getcwd())])
        self.by_alias = dict(self.items)

    def key(self, alias, rel):
        return f"{alias}:{rel}" if self.multi else rel

    def split(self, key):
        if self.multi:
            alias, sep, rel = key.partition(":")
            if sep and alias in self.by_alias:
                return alias, rel
            return None, key
        return "", key

    def fs(self, key):
        """Filesystem path for a key, or None if the key names an unknown repo."""
        alias, rel = self.split(key)
        if alias is None:
            return None
        return os.path.join(self.by_alias[alias], rel) if self.multi else rel

    def normalize(self, key):
        """Tolerate a stray prefix in single-repo notes, and strip a trailing :line or :a-b."""
        key = re.sub(r":\d+(-\d+)?$", "", key.strip())
        if not self.multi and ":" in key and not os.path.exists(key):
            key = key.split(":", 1)[1]
        return key

    def in_prefix(self, key, prefix):
        """Is key under prefix? A bare alias as prefix means the whole repo."""
        prefix = prefix.rstrip("/")
        if self.multi and prefix in self.by_alias:
            return key.startswith(prefix + ":")
        return key == prefix or key.startswith(prefix + "/") or fnmatch.fnmatch(key, prefix)


def list_files(root, ws):
    """Repo-relative paths of candidate files under root, skipping the workspace itself."""
    ws_abs = os.path.abspath(ws)
    if is_git(root):
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=root,
                             capture_output=True, text=True).stdout.splitlines()
        files = [f for f in out if not any(p in PRUNE_DIRS - {"tmp"} or p.startswith(".")
                                           for p in f.split("/")[:-1])]
    else:
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in PRUNE_DIRS and not x.startswith(".")]
            for fn in filenames:
                files.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return [f for f in files if not os.path.abspath(os.path.join(root, f)).startswith(ws_abs + os.sep)]


def matches(path, patterns):
    p = "/" + path
    return any(fnmatch.fnmatch(p, "*/" + pat.lstrip("*/")) or fnmatch.fnmatch(p, pat) for pat in patterns)


def classify(rel, key, cfg, roots):
    """None if out of scope, else 'source' | 'test'. Scope entries are keys or key prefixes (in
    multi-repo mode a bare alias means that whole repo); exclude globs may match either form."""
    ext = os.path.splitext(rel)[1]
    if ext not in SOURCE_EXT:
        return None
    scope = cfg.get("scope") or []
    if scope and not any(roots.in_prefix(key, p) for p in scope):
        return None
    excl = cfg.get("exclude") or []
    if matches(rel, GENERATED_PATTERNS) or matches(rel, excl) or matches(key, excl):
        return None
    if matches(rel, TEST_PATTERNS):
        return "test"
    return "source"


def count_lines(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return 0, b""
    if b"\0" in data[:4096]:
        return 0, b""
    n = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    return n, data


def sha1(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return None


def plan_path(d):
    return os.path.join(d, "plan.json")


def load_plan(d):
    return load_json(plan_path(d), {"slices": []})


def find_slice(plan, sid):
    for s in plan["slices"]:
        if s["id"] == sid or s.get("slug") == sid:
            return s
    die(f"no slice {sid!r} in plan.json")


def slice_file(d, kind, s):
    return os.path.join(d, kind, f"{s['id']}-{s['slug']}.md")


def volumes_of(plan):
    return plan.get("volumes") or []


def find_volume(plan, vid):
    for v in volumes_of(plan):
        if str(v.get("id")).lower() == str(vid).lower():
            return v
    return None


def slice_volume(s):
    """A slice's volume id, or None for a guide that never needed volumes."""
    return s.get("volume")


def next_slice_id(plan):
    """Chapter numbers run continuously across volumes, so ids stay unique and one chapter can
    refer to another in a different volume without qualification."""
    nums = [int(s["id"]) for s in plan["slices"] if str(s["id"]).isdigit()]
    return f"{(max(nums) + 1) if nums else 1:02d}"


def volume_label(v):
    """Short enough for a sidebar: a volume title often carries its own subtitle after a colon."""
    t = re.split(r"[:—]| - ", v.get("title") or "")[0].strip()
    if len(t) > 46:
        t = t[:44].rsplit(" ", 1)[0] + "…"
    return f"Volume {v['id']}: {t}".rstrip(": ")


def measure_paths(d, roots, paths):
    """In-scope lines under paths, from the last inventory. 0 until `map` has seen them, which is
    expected for a volume whose paths were only just brought into scope."""
    inv = (load_json(os.path.join(d, "inventory.json")) or {}).get("files") or {}
    return sum(n for f, n in inv.items() if any(roots.in_prefix(f, p.rstrip("/")) for p in paths))


def coverage_index(plan):
    """file key -> [slice, ...], from the files each slice recorded at `mark` time."""
    idx = defaultdict(list)
    for s in plan["slices"]:
        for f in (s.get("files") or {}):
            idx[f].append(s)
    return idx


def file_state(key, stored_hash, roots):
    """'' | 'changed' | 'deleted' for one relied-on file."""
    fp = roots.fs(key)
    if not fp or not os.path.exists(fp):
        return "deleted"
    return "" if sha1(fp) == stored_hash else "changed"


def slice_drift(s, roots):
    """(changed_or_deleted, total) for a slice's relied-on files."""
    files = s.get("files") or {}
    return sum(1 for f, h in files.items() if file_state(f, h, roots)), len(files)


# ---------------------------------------------------------------- commands

def cmd_locate(args):
    d = locate()
    if d:
        print(d)
    else:
        sys.exit(1)


def workspace_warnings(d, plan):
    """Signs that a plan was replaced rather than extended. Chapters and notes are expensive and are
    only reachable through plan.json, so a file no slice claims is work that has been orphaned -
    which is what a re-plan that drops earlier slices looks like from the outside."""
    out = []
    expected = {f"{s['id']}-{s['slug']}.md" for s in plan["slices"]} | {"00-overview.md", "99-closing.md"}
    for kind in ("chapters", "notes"):
        folder = os.path.join(d, kind)
        if not os.path.isdir(folder):
            continue
        orphans = sorted({f for f in os.listdir(folder) if f.endswith(".md")} - expected)
        if orphans:
            out.append(f"{len(orphans)} file(s) in {kind}/ that no slice in plan.json claims: "
                       + ", ".join(orphans[:6]) + (" ..." if len(orphans) > 6 else ""))
    gone = [s["id"] for s in plan["slices"]
            if s.get("status") == "done" and not os.path.exists(slice_file(d, "chapters", s))]
    if gone:
        out.append(f"slices marked done with no chapter file: {', '.join(gone)}")
    return out


def cmd_init(args):
    d = args.dir
    prev = load_json(os.path.join(d, WS_CONFIG))
    if prev and not args.force:
        # One workspace holds every volume, so re-scoping it in place is how a guide gets destroyed:
        # the planner writes a fresh slice list and the old chapters become unreachable.
        plan0 = load_plan(d)
        written = [s for s in plan0["slices"] if s.get("status") in ("researched", "written", "done")]
        new_scope = [s for s in (args.scope or "").split(",") if s]
        if written and args.scope is not None and new_scope != (prev.get("scope") or []):
            die(f"{d} already holds {len(written)} researched or written chapter(s) under a different "
                f"scope.\n  To cover a new area, add it as a volume of this guide:\n"
                f"    volume add <id> --title '<title>' --paths {args.scope}\n"
                f"    volume start <id>\n"
                f"  To start a separate guide, pass a different --dir.\n"
                f"  To re-scope this one anyway and orphan those chapters, add --force.")
    os.makedirs(os.path.join(d, "notes"), exist_ok=True)
    os.makedirs(os.path.join(d, "chapters"), exist_ok=True)
    migrate(d)
    cfg_path = os.path.join(d, WS_CONFIG)
    cfg = load_json(cfg_path) or {}
    cfg[CONFIG_MARKER] = 1
    # The guide's definition is written explicitly into the committed file, so everyone who refreshes
    # it produces the same document regardless of their own user defaults.
    base, _ = resolve_layers(None)
    for key in ("format", "tone", "depth"):
        cfg[key] = getattr(args, key) or cfg.get(key) or base[key]
    if args.scope is not None:
        cfg["scope"] = [s for s in args.scope.split(",") if s]
    cfg.setdefault("scope", [])
    if args.audience:
        cfg["audience"] = args.audience
    if args.repo:
        repos = {}
        for spec in args.repo:
            alias, eq, path = spec.partition("=")
            if not eq or not ALIAS_RX.match(alias):
                die(f"--repo expects name=path with a short name (letters, digits, - or _), got {spec!r}")
            if not os.path.isdir(path):
                die(f"--repo {alias}: {path} is not a directory")
            # stored relative to the workspace, so the committed config works on any machine with the
            # same side-by-side layout
            repos[alias] = os.path.relpath(os.path.abspath(path), os.path.abspath(d))
        cfg["repos"] = repos
    save_json(cfg_path, cfg)
    ensure_gitignore(d)
    if not os.path.exists(plan_path(d)):
        save_json(plan_path(d), {"slices": []})
    g = os.path.join(d, "glossary.md")
    if not os.path.exists(g):
        with open(g, "w") as f:
            f.write("<!-- one line per term: - **term** - plain definition (ch NN) -->\n")
    print(f"workspace ready: {d}")


def set_key(cfg, key, val):
    parts = key.split(".")
    node = cfg
    for k in parts[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    if val is None:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = val


def cmd_config(args):
    """Show the resolved config with each value's source layer; --set edits one layer.

    Layers, lowest to highest precedence: built-in, user (~/.claude/field-guide.json), workspace
    (field-guide.config.json, committed), local (field-guide.config.local.json, gitignored)."""
    d = getattr(args, "dir", None) or locate()
    if d:
        migrate(d)
    explicit = "user" if args.user else "local" if args.local else "workspace" if args.workspace else None
    edits = defaultdict(list)
    for kv in args.set:
        key, eq, raw = kv.partition("=")
        if not key or not eq:
            die(f"--set expects KEY=VALUE, got {kv!r}")
        try:
            val = json.loads(raw)
        except json.JSONDecodeError:
            val = raw
        parts = key.split(".")
        if parts[0] == "models" and len(parts) == 2:
            if parts[1] not in MODEL_DEFAULTS:
                die(f"unknown operation {parts[1]!r}; one of {', '.join(MODEL_DEFAULTS)}")
            if val is not None and val not in MODEL_CHOICES:
                die(f"unknown model {val!r}; one of {', '.join(MODEL_CHOICES)}")
        target = explicit or (("local" if d else "user") if parts[0] in PERSONAL_KEYS else "workspace")
        if target in ("workspace", "local") and not d:
            die(f"no workspace found for a {target} setting; pass --dir, or use --user")
        edits[target].append((key, val))
    for target, kvs in edits.items():
        path = USER_CONFIG if target == "user" else os.path.join(
            d, WS_CONFIG if target == "workspace" else LOCAL_CONFIG)
        cfg = load_json(path, {}) or {}
        for key, val in kvs:
            set_key(cfg, key, val)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        save_json(path, cfg)
        print(f"updated {target}: {path}")
    merged, src = resolve_layers(d)
    if args.json:
        print(json.dumps(merged, indent=2))
        return
    print(f"workspace: {d or '(none)'}   user file: {USER_CONFIG}")
    for k in sorted(merged):
        v = merged[k]
        if isinstance(v, dict):
            for sk in sorted(v):
                print(f"  {k}.{sk} = {json.dumps(v[sk])}   ({src.get(f'{k}.{sk}', '?')})")
        else:
            print(f"  {k} = {json.dumps(v)}   ({src.get(k, '?')})")
    if d:
        shared = load_json(os.path.join(d, WS_CONFIG), {}) or {}
        leaked = [k for k in PERSONAL_KEYS if shared.get(k) not in (None, {}) and
                  not (isinstance(shared.get(k), dict) and all(x is None for x in shared[k].values()))]
        if leaked:
            print(f"note: {', '.join(sorted(leaked))} set in the shared {WS_CONFIG}; personal settings "
                  f"usually belong in {LOCAL_CONFIG} (--local) or the user file (--user)")


def find_shared(inv, hashes, texts, roots):
    """Code that exists in more than one repo: identical copies (same content) and copies that have
    drifted (same trailing path, similar but different content). Hand-maintained sharing between repos
    tends to drift silently, so drifted copies are reported as findings."""
    import difflib
    by_hash = defaultdict(list)
    for k, h in hashes.items():
        by_hash[h].append(k)
    identical = []
    for h, ks in by_hash.items():
        if len({roots.split(k)[0] for k in ks}) > 1:
            identical.append({"files": sorted(ks), "lines": inv[ks[0]]})
    same = {k for g in identical for k in g["files"]}

    def tails(k):
        parts = roots.split(k)[1].split("/")
        return {"/".join(parts[-2:])} if len(parts) >= 2 else set()

    by_tail = defaultdict(list)
    for k in inv:
        for t in tails(k):
            by_tail[t].append(k)
    drifted, seen, budget = [], set(), 4000
    for t, ks in by_tail.items():
        for i, a in enumerate(ks):
            for b in ks[i + 1:]:
                if roots.split(a)[0] == roots.split(b)[0] or hashes[a] == hashes[b] or (a, b) in seen:
                    continue
                seen.add((a, b))
                if budget <= 0 or max(inv[a], inv[b]) > 5000:
                    continue
                budget -= 1
                la, lb = texts[a].splitlines(), texts[b].splitlines()
                sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
                if sm.real_quick_ratio() < 0.5 or sm.quick_ratio() < 0.5:
                    continue
                r = sm.ratio()
                if r >= 0.5:
                    changed = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")
                    drifted.append({"files": [a, b], "similarity": round(r, 2), "lines": [inv[a], inv[b]],
                                    "changed_lines": changed})
    drifted.sort(key=lambda x: (-x["changed_lines"], x["files"]))
    return {"identical": sorted(identical, key=lambda g: -g["lines"]), "drifted": drifted,
            "identical_lines": sum(g["lines"] * (len(g["files"]) - 1) for g in identical)}


def cmd_repos(args):
    """Repo aliases and where they are on this machine, for agent prompts in multi-repo guides."""
    d, cfg = workspace(args)
    roots = Roots(d, cfg)
    if not roots.multi:
        print(f"single repo: {os.getcwd()} (paths are repo-relative, no prefix)")
        return
    for a, r in roots.items:
        print(f"{a}={r}" + ("" if os.path.isdir(r) else "   (MISSING)"))


def cmd_map(args):
    d, cfg = workspace(args)
    roots = Roots(d, cfg)
    inv, tests, hashes, texts, docs = {}, {}, {}, {}, []
    entry = defaultdict(Counter)
    churn = Counter()
    contents_scanned = 0
    for alias, root in roots.items:
        if not os.path.isdir(root):
            die(f"repo '{alias}' not found at {root}")
        files = list_files(root, d)
        for rel in files:
            key = roots.key(alias, rel)
            if re.search(r"(^|/)(README|ARCHITECTURE|CONTRIBUTING|CLAUDE|AGENTS)[^/]*\.md$", rel, re.I) or \
                    (rel.endswith(".md") and re.match(r"(docs?|documentation)/", rel)):
                docs.append(key)
            kind = classify(rel, key, cfg, roots)
            if not kind:
                continue
            n, data = count_lines(os.path.join(root, rel))
            if n == 0:
                continue
            if kind == "test" and not cfg.get("include_tests"):
                tests[key] = n
                continue
            inv[key] = n
            if roots.multi:
                hashes[key] = hashlib.sha1(data).hexdigest()
            if len(data) < 1_000_000:
                contents_scanned += 1
                text = data.decode("utf-8", "ignore")
                if roots.multi:
                    texts[key] = text
                for label, rx in ENTRY_PATTERNS.items():
                    c = len(rx.findall(text))
                    if c:
                        entry[label][key] = c
            if matches(rel, UI_PAGE_GLOBS):
                entry["ui route/page"][key] += 1
        if is_git(root):
            r = subprocess.run(["git", "log", "--since=1.year", "--name-only", "--pretty=format:"],
                               cwd=root, capture_output=True, text=True)
            for line in r.stdout.splitlines():
                k = roots.key(alias, line)
                if k in inv:
                    churn[k] += 1

    total = sum(inv.values())
    save_json(os.path.join(d, "inventory.json"), {"generated": now(), "files": inv})

    # Refresh sizes only for volumes already in scope. An unstarted volume's paths are outside the
    # inventory, so measuring it here would report near-zero and hide how big it really is; its
    # number comes from the planner, which measured against the whole repo.
    plan = load_plan(d)
    changed = False
    for v in volumes_of(plan):
        if v.get("status") == "started":
            v["lines"] = measure_paths(d, roots, v.get("paths") or [])
            changed = True
    if changed:
        save_json(plan_path(d), plan)

    by_ext = Counter()
    for f, n in inv.items():
        by_ext[os.path.splitext(f)[1]] += n

    # directory rollup: a dir shows if it holds >= 1% of lines (top levels always), depth <= 4
    dir_lines, dir_files, dir_depth = Counter(), Counter(), {}
    for f, n in inv.items():
        alias, rel = roots.split(f)
        parts = rel.split("/")[:-1]
        for i in range(0 if roots.multi else 1, min(len(parts), 5) + 1):
            key = roots.key(alias, "/".join(parts[:i])) if roots.multi else "/".join(parts[:i])
            dir_lines[key] += n
            dir_files[key] += 1
            dir_depth[key] = i if roots.multi else i - 1
    thresh = max(total * 0.01, 1)
    rows = sorted(k for k in dir_lines if dir_lines[k] >= thresh or dir_depth[k] == 0)[:160]

    out = []
    title = cfg.get("title") or os.path.basename(os.path.abspath("."))
    out.append(f"# Map of {title}\n")
    out.append(f"Generated {now()} by fg.py map. Scope: {', '.join(cfg['scope']) or 'everything'}.\n")
    if roots.multi:
        out.append("Repos (cite files as `name:path`): " +
                   ", ".join(f"`{a}` = {p}" for a, p in roots.items) + "\n")
    out.append(f"- In-scope source: **{len(inv):,} files, {total:,} lines**")
    out.append(f"- Tests (excluded from counts): {len(tests):,} files, {sum(tests.values()):,} lines")
    out.append(f"- Content scanned for entry points: {contents_scanned:,} files")
    if total > cfg["volume_threshold_lines"]:
        out.append(f"\n> **Large scope** ({total:,} lines > {cfg['volume_threshold_lines']:,}). "
                   "Plan volumes first: pick one area, narrow `scope` in the workspace config, re-run map.\n")
    out.append("\n## Languages (lines)\n")
    for ext, n in by_ext.most_common(12):
        out.append(f"- `{ext}` {n:,} ({100*n/max(total, 1):.1f}%)")
    out.append("\n## Directory rollup (dirs with >= 1% of lines)\n")
    out.append("| dir | files | lines | % |\n|---|---:|---:|---:|")
    for k in rows:
        label = k if k.endswith(":") else k + "/"
        out.append(f"| `{'  ' * dir_depth[k]}{label}` | {dir_files[k]:,} | {dir_lines[k]:,} | {100*dir_lines[k]/max(total, 1):.1f} |")
    shared = None
    if roots.multi:
        shared = find_shared(inv, hashes, texts, roots)
        save_json(os.path.join(d, "shared.json"), shared)
        out.append("\n## Code shared across repos\n")
        out.append(f"- Identical copies: {len(shared['identical'])} groups, "
                   f"{shared['identical_lines']:,} duplicated lines. Read one copy; the others are the same bytes.")
        out.append(f"- Drifted copies (same trailing path, similar but different): {len(shared['drifted'])}. "
                   "Each is listed in FINDINGS.md.")
        for g in shared["drifted"][:25]:
            a, b = g["files"]
            out.append(f"  - `{a}` vs `{b}`: {int(g['similarity'] * 100)}% similar, ~{g['changed_lines']} lines differ")
        for g in shared["identical"][:15]:
            out.append(f"  - identical: " + ", ".join(f"`{k}`" for k in g["files"]) + f" ({g['lines']} ln)")
    out.append("\n## Entry-point candidates (heuristic; verify before trusting)\n")
    for label in ENTRY_PATTERNS.keys() | {"ui route/page"}:
        c = entry.get(label)
        if not c:
            continue
        out.append(f"\n**{label}** ({len(c)} files)\n")
        for f, k in c.most_common(20):
            out.append(f"- `{f}` ({k}, {inv.get(f, 0):,} ln)")
    out.append("\n## Largest files\n")
    for f, n in sorted(inv.items(), key=lambda x: -x[1])[:25]:
        out.append(f"- `{f}` {n:,}")
    if docs:
        out.append("\n## Docs present (leads, not facts)\n")
        for f in sorted(docs, key=lambda x: (x.count("/"), x))[:40]:
            out.append(f"- `{f}`")
    if churn:
        out.append("\n## Most-changed files, last 12 months\n")
        for f, k in churn.most_common(25):
            out.append(f"- `{f}` {k} commits")
    with open(os.path.join(d, "map.md"), "w") as fh:
        fh.write("\n".join(out) + "\n")
    msg = f"map.md written: {len(inv):,} files, {total:,} lines in scope"
    if roots.multi:
        msg += (f" across {len(roots.items)} repos; {len(shared['identical'])} identical and "
                f"{len(shared['drifted'])} drifted shared files")
    print(msg)


def cmd_measure(args):
    d, cfg = workspace(args)
    inv = (load_json(os.path.join(d, "inventory.json")) or {}).get("files") or die("run map first")
    roots = Roots(d, cfg)
    total_f = total_l = 0
    for p in args.paths:
        p = p.rstrip("/")
        hit = {f: n for f, n in inv.items() if roots.in_prefix(f, p)}
        total_f += len(hit)
        total_l += sum(hit.values())
        print(f"{p}: {len(hit)} files, {sum(hit.values()):,} lines")
    print(f"TOTAL: {total_f} files, {total_l:,} lines")


def cmd_status(args):
    d, cfg = workspace(args)
    plan = load_plan(d)
    for w in workspace_warnings(d, plan):
        print(f"WARNING: {w}", file=sys.stderr)
    if not plan["slices"]:
        print("plan.json has no slices yet")
        return
    print(f"{'id':<4} {'status':<11} {'type':<10} {'dir':<9} {'words':>6}  title")
    for s in plan["slices"]:
        ch = slice_file(d, "chapters", s)
        words = len(open(ch).read().split()) if os.path.exists(ch) else 0
        print(f"{s['id']:<4} {s.get('status','proposed'):<11} {s.get('type','?'):<10} "
              f"{s.get('direction','?'):<9} {words:>6}  {s['title']}")
    c = Counter(s.get("status", "proposed") for s in plan["slices"])
    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(c.items())))


# Fallback tokens per step at standard depth, used until this workspace has measurements (fg.py usage).
# Not measured: projected from one early run (research 78-108k and writing 56-69k per slice, before
# the per-depth budgets and the fixed-turn writer), minus the expected savings.
STEP_COST = {"research": 75_000, "write": 30_000, "bookends": 60_000}
DEPTH_COST = {"orientation": 0.5, "standard": 1.0, "deep": 1.8}


def read_usage(d):
    """Usage log entries, oldest first. Also folds in any usage an older fg.py stored in plan.json."""
    path = os.path.join(d, USAGE_LOG)
    out = []
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    plan = load_plan(d)
    moved = []
    for s in plan.get("slices", []):
        for step, n in (s.pop("usage", None) or {}).items():
            moved.append({"at": s.get("updated"), "id": s["id"], "step": step, "tokens": n})
    for step, ns in (plan.pop("usage", None) or {}).items():
        moved += [{"at": None, "id": "-", "step": step, "tokens": n} for n in (ns if isinstance(ns, list) else [ns])]
    if moved:
        with open(path, "a") as f:
            for e in moved:
                f.write(json.dumps(e) + "\n")
        save_json(plan_path(d), plan)
        out += moved
    return out


def step_estimates(d, cfg):
    """Per-step token estimate for this workspace: the median of logged runs once there are at least
    two, else the depth-scaled fallback. Returns {step: (tokens, basis)}."""
    scale = DEPTH_COST.get(cfg.get("depth"), 1.0)
    samples = defaultdict(list)
    for e in read_usage(d):
        if isinstance(e.get("tokens"), int):
            samples[e.get("step")].append(e["tokens"])
    out = {}
    for step, fallback in STEP_COST.items():
        ns = sorted(samples.get(step, []))
        if len(ns) >= 2:
            out[step] = (ns[len(ns) // 2], f"median of {len(ns)} measured")
        else:
            out[step] = (int(fallback * scale), "unmeasured estimate")
    return out


def cmd_usage(args):
    """`usage log ...` appends one subagent run to usage.jsonl; `usage` alone reports on the log."""
    d, cfg = workspace(args)
    if args.usage_cmd == "log":
        entry = {"at": dt.datetime.now().isoformat(timespec="seconds"), "id": args.id, "step": args.step,
                 "tokens": args.tokens}
        for k in ("model", "tool_uses", "duration_ms", "note"):
            v = getattr(args, k)
            if v is not None:
                entry[k] = v
        if args.id != "-":
            find_slice(load_plan(d), args.id)  # fail loudly on a typo rather than log an orphan
        with open(os.path.join(d, USAGE_LOG), "a") as f:
            f.write(json.dumps(entry) + "\n")
        est = step_estimates(d, cfg).get(args.step)
        print(f"logged {args.step} {args.id} {args.tokens:,} tokens"
              + (f"; estimate now {est[0]:,} ({est[1]})" if est else ""))
        return
    entries = read_usage(d)
    if args.json:
        print(json.dumps(entries, indent=2))
        return
    if not entries:
        print(f"no usage logged yet ({os.path.join(d, USAGE_LOG)})")
        return
    total = sum(e.get("tokens") or 0 for e in entries)
    dates = [e["at"] for e in entries if e.get("at")]
    print(f"{len(entries)} agent runs, {total:,} tokens" + (f", {min(dates)[:10]} to {max(dates)[:10]}" if dates else ""))
    by_step, by_model, by_slice = defaultdict(list), Counter(), defaultdict(Counter)
    for e in entries:
        n = e.get("tokens") or 0
        by_step[e.get("step")].append(n)
        by_model[e.get("model") or "unrecorded"] += n
        by_slice[e.get("id")][e.get("step")] += n
    print(f"\n{'step':<10} {'runs':>5} {'total':>11} {'median':>9} {'range':>19}")
    for step, ns in sorted(by_step.items(), key=lambda x: -sum(x[1])):
        ns = sorted(ns)
        print(f"{step or '?':<10} {len(ns):>5} {sum(ns):>11,} {ns[len(ns) // 2]:>9,} {f'{ns[0]:,}-{ns[-1]:,}':>19}")
    print(f"\n{'model':<12} {'tokens':>11}")
    for m, n in by_model.most_common():
        print(f"{m:<12} {n:>11,}")
    titles = {s["id"]: s["title"] for s in load_plan(d).get("slices", [])}
    print(f"\n{'id':<4} {'research':>9} {'write':>9} {'other':>9} {'total':>10}  title")
    for sid in sorted(by_slice, key=lambda x: (x == "-", x)):
        c = by_slice[sid]
        other = sum(v for k, v in c.items() if k not in ("research", "write"))
        print(f"{sid:<4} {c['research']:>9,} {c['write']:>9,} {other:>9,} {sum(c.values()):>10,}  "
              f"{titles.get(sid, '(guide-level)' if sid == '-' else '')}")


def slice_step_cost(d, cfg, s, est=None):
    """(step, tokens) to finish a slice: 'write' when notes already exist, else 'research'."""
    est = est or step_estimates(d, cfg)
    notes = os.path.exists(slice_file(d, "notes", s))
    step = "write" if s.get("status") in ("researched", "written") or notes else "research"
    cost = est["write"][0] + (est["research"][0] if step == "research" else 0)
    return step, cost


def cmd_remaining(args):
    """What finishing the guide takes: each unfinished slice, the step it resumes at, and a rough cost.
    A slice with notes on disk resumes at writing, so an interrupted run never pays for research twice."""
    d, cfg = workspace(args)
    plan = load_plan(d)
    wanted = {"selected", "researched", "written"} | ({"proposed"} if args.include_proposed else set())
    done = {s["id"] for s in plan["slices"] if s.get("status") == "done"}
    todo = [s for s in plan["slices"] if s.get("status", "proposed") in wanted]
    if not todo:
        print("nothing to finish" + ("" if args.include_proposed else " (proposed slices excluded; add --include-proposed)"))
        return
    total = 0
    est = step_estimates(d, cfg)
    queued = {s["id"] for s in todo}
    print(f"{'id':<4} {'status':<11} {'resume at':<10} {'est':>6}  title")
    for s in todo:
        step, cost = slice_step_cost(d, cfg, s, est)
        total += cost
        missing = [x for x in s.get("depends_on", []) if x not in done and x not in queued]
        warn = f"   needs {','.join(missing)} (not done, not queued)" if missing else ""
        print(f"{s['id']:<4} {s.get('status', 'proposed'):<11} {step:<10} {cost // 1000:>5}k  {s['title']}{warn}")
    print(f"\n{len(todo)} slices, roughly {total // 1000}k tokens (+ ~{est['bookends'][0] // 1000}k for "
          f"overview/closing). Basis: research {est['research'][1]}, writing {est['write'][1]}.")


PICKER_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "picker_template.html")


def volume_states(cfg, plan):
    """Tag each volume: 'current' when it already has slices planned, 'started' when its paths are in
    scope but nothing is planned yet, else 'open'. Every volume lives in this one workspace, so this
    reads plan state rather than scanning for sibling directories."""
    norm = lambda paths: {p.rstrip("/") for p in (paths or [])}
    here = norm(cfg.get("scope"))
    planned = Counter(slice_volume(s) for s in plan["slices"])
    out = []
    for v in volumes_of(plan):
        vp = norm(v.get("paths"))
        state = ("current" if planned.get(v["id"])
                 else "started" if v.get("status") == "started" or (vp and vp <= here)
                 else "open")
        out.append({**v, "state": state})
    return out


def cmd_pick(args):
    """Serve a one-shot browser picker for slices (or volumes), apply the choice to plan.json, print
    the result as JSON and exit. Meant to be run in the background by the orchestrator: the process
    exiting is the signal that the user has chosen.

    Bound to 127.0.0.1 with a random token in every request, so other local pages can't drive it."""
    import http.server
    import secrets
    import threading
    import webbrowser

    d, cfg = workspace(args)
    token = secrets.token_urlsafe(16)
    result_path = os.path.join(d, ".pick-result.json")
    outcome = {}
    done_evt = threading.Event()

    def snapshot():
        est = step_estimates(d, cfg)
        plan = load_plan(d)
        slices = []
        for s in plan.get("slices", []):
            step, cost = slice_step_cost(d, cfg, s, est)
            ch = slice_file(d, "chapters", s)
            slices.append({**{k: s.get(k) for k in ("id", "slug", "title", "type", "direction", "why_direction",
                                                   "layers", "entry", "question", "est_lines", "depends_on",
                                                   "foundation", "status", "volume")},
                           "step": step, "cost": cost,
                           "words": len(open(ch).read().split()) if os.path.exists(ch) else 0})
        mode = "volumes" if plan.get("volumes") and not plan.get("slices") else "slices"
        return {"mode": mode, "title": cfg.get("title") or os.path.basename(os.path.abspath(".")),
                "workspace": d, "depth": cfg.get("depth"), "models": cfg.get("models"),
                "volumes": volume_states(cfg, plan), "slices": slices,
                "not_covered": plan.get("not_covered", []), "bookends_cost": est["bookends"][0],
                "cost_basis": est["research"][1],
                "default_run": args.default_run}

    def apply(choice):
        """Only proposed/selected/skipped statuses move; researched and done slices are never demoted.
        A volume chosen from the slice view carries the slice selection too, so it's saved, not lost."""
        if choice.get("action") not in ("run", "volume") or "selected" not in choice:
            return choice
        plan = load_plan(d)
        chosen = set(choice["selected"])
        skip = set(choice.get("skipped", []))
        for s in plan["slices"]:
            st = s.get("status", "proposed")
            if st not in ("proposed", "selected", "skipped"):
                continue
            s["status"] = "selected" if s["id"] in chosen else "skipped" if s["id"] in skip else "proposed"
        save_json(plan_path(d), plan)
        order = [s["id"] for s in plan["slices"] if s["id"] in chosen and s.get("status") != "done"]
        n = {"one": 1, "three": 3, "all": len(order), "none": 0}.get(choice.get("run", "none"), 0)
        choice["run_now"] = order[:n]
        return choice

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == f"/{token}":
                with open(PICKER_TEMPLATE) as f:
                    self._send(200, f.read().replace("__TOKEN__", token), "text/html")
            elif self.path == f"/{token}/api/plan":
                self._send(200, json.dumps(snapshot()))
            else:
                self._send(404, "{}")

        def do_POST(self):
            if self.path != f"/{token}/api/submit":
                return self._send(404, "{}")
            try:
                n = int(self.headers.get("Content-Length", 0))
                choice = json.loads(self.rfile.read(n) or b"{}")
                res = apply(choice)
            except Exception as e:  # report instead of leaving the orchestrator waiting forever
                return self._send(400, json.dumps({"error": str(e)}))
            outcome.update(res)
            self._send(200, json.dumps({"ok": True}))
            done_evt.set()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}/{token}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"picker: {url}", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    got = done_evt.wait(timeout=args.timeout)
    srv.shutdown()
    if not got:
        outcome = {"action": "timeout"}
    outcome["at"] = now()
    save_json(result_path, outcome)
    print("RESULT " + json.dumps(outcome), flush=True)
    sys.exit(0 if got else 2)


# path:line or path:a-b, optionally prefixed by a repo alias (alias:path:line) in multi-repo mode
CITE_RX = re.compile(r"`?((?:[A-Za-z0-9_-]+:)?(?:[\w.@+-]+/)*[\w.@+-]+\.\w+):(\d+)(?:-(\d+))?`?")
SYM_RX = re.compile(r"`([A-Za-z_][\w.]*(?:\(\))?)`")


def cmd_check(args):
    d, cfg = workspace(args)
    roots = Roots(d, cfg)
    s = find_slice(load_plan(d), args.id)
    path = slice_file(d, "notes", s)
    if not os.path.exists(path):
        die(f"{path} does not exist")
    bad, ok, cache = [], 0, {}
    for lineno, line in enumerate(open(path), 1):
        syms = [m.rstrip("()").split(".")[-1] for m in SYM_RX.findall(line) if ":" not in m and "/" not in m]
        for m in CITE_RX.finditer(line):
            f, a, b = roots.normalize(m.group(1)), int(m.group(2)), int(m.group(3) or m.group(2))
            if f not in cache:
                fp = roots.fs(f)
                cache[f] = open(fp, errors="ignore").read().splitlines() if fp and os.path.isfile(fp) else None
            src = cache[f]
            if src is None:
                hint = " (multi-repo: cite as alias:path)" if roots.multi and roots.split(f)[0] is None else ""
                bad.append(f"notes:{lineno}: {f} does not exist{hint}")
                continue
            if a < 1 or b > len(src) or a > b:
                bad.append(f"notes:{lineno}: {f}:{a}-{b} out of range (file has {len(src)} lines)")
                continue
            window = "\n".join(src[max(0, a - 6): b + 5])
            missing = [x for x in syms if len(x) > 2 and x not in window]
            if syms and len(missing) == len(syms):
                bad.append(f"notes:{lineno}: none of {syms} found near {f}:{a}-{b}")
                continue
            ok += 1
    print(f"citations ok: {ok}, problems: {len(bad)}")
    for b in bad:
        print("  " + b)
    sys.exit(1 if bad else 0)


def relied_on_files(notes_path, roots):
    """Keys listed under the notes' '## Files relied on' heading that exist on disk."""
    files, on = [], False
    for line in open(notes_path):
        if line.startswith("## "):
            on = line.strip().lower().startswith("## files relied on")
            continue
        if on:
            m = re.search(r"`([^`]+)`", line) or re.match(r"\s*[-*]\s+(\S+)", line)
            if m:
                p = roots.normalize(m.group(1))
                fp = roots.fs(p)
                if fp and os.path.isfile(fp):
                    files.append(p)
    return sorted(set(files))


def cmd_mark(args):
    d, cfg = workspace(args)
    roots = Roots(d, cfg)
    plan = load_plan(d)
    s = find_slice(plan, args.id)
    if args.status in ("researched", "written", "done"):
        notes = slice_file(d, "notes", s)
        if os.path.exists(notes):
            files = relied_on_files(notes, roots)
            if not files:
                print("warning: notes have no '## Files relied on' list; staleness can't be tracked")
            s["files"] = {f: sha1(roots.fs(f)) for f in files}
    s["status"] = args.status
    s["updated"] = now()
    heads = {a or "repo": git_head(r) for a, r in roots.items}
    heads = {a: h for a, h in heads.items() if h}
    if heads:
        s["git_head"] = heads if roots.multi else heads.get("repo")
    save_json(plan_path(d), plan)
    print(f"{s['id']} -> {args.status} ({len(s.get('files', {}))} files tracked)")


def cmd_volume(args):
    """Volumes live in one workspace: same glossary, same chapter numbering, one built guide.
    A volume is a named slice of scope, not a separate guide."""
    d, cfg = workspace(args)
    plan = load_plan(d)
    plan.setdefault("volumes", [])
    sub = args.volume_cmd or "list"

    if sub == "add":
        if find_volume(plan, args.id):
            die(f"volume {args.id} already exists")
        paths = [p for p in (args.paths or "").split(",") if p]
        if not paths:
            die("volume add needs --paths a,b,c")
        plan["volumes"].append({"id": args.id, "title": args.title or "", "paths": paths,
                                "lines": measure_paths(d, Roots(d, cfg), paths),
                                "summary": args.summary or "", "status": "open"})
        save_json(plan_path(d), plan)
        print(f"volume {args.id} added ({len(paths)} paths)")
        return

    if sub == "start":
        v = find_volume(plan, args.id) or die(f"no volume {args.id!r}")
        # Widening the workspace scope is what makes the volume's code visible to map, measure and
        # the coverage page. Volumes share one scope so cross-volume references stay resolvable.
        ws = load_json(os.path.join(d, WS_CONFIG), {}) or {}
        scope = list(ws.get("scope") or [])
        added = [p for p in v["paths"] if p not in scope]
        ws["scope"] = scope + added
        save_json(os.path.join(d, WS_CONFIG), ws)
        v["status"] = "started"
        save_json(plan_path(d), plan)
        print(f"volume {v['id']} started; {len(added)} paths added to scope. Run `map`, then plan.")
        return

    vols = volumes_of(plan)
    if not vols:
        print("no volumes; this guide covers its whole scope in one sequence")
        return
    counts = Counter(slice_volume(s) for s in plan["slices"])
    done = Counter(slice_volume(s) for s in plan["slices"] if s.get("status") == "done")
    print(f"{'vol':<5}{'status':<10}{'chapters':<12}{'lines':>10}  title")
    for v in vols:
        vid = v["id"]
        print(f"{vid:<5}{v.get('status', 'open'):<10}"
              f"{f'{done[vid]}/{counts[vid]}':<12}{v.get('lines', 0):>10,}  {v.get('title', '')}")


def cmd_topic(args):
    """What the guide already says about a topic, before researching it again."""
    d, cfg = workspace(args)
    roots = Roots(d, cfg)
    plan = load_plan(d)
    terms = [t.lower() for t in args.terms]
    hits = []
    for s in plan["slices"]:
        text = ""
        for kind in ("chapters", "notes"):
            p = slice_file(d, kind, s)
            if os.path.exists(p):
                text += open(p, errors="ignore").read().lower()
        if not text:
            continue
        n = sum(text.count(t) for t in terms)
        if n:
            hits.append((n, s))
    if not hits:
        print("no existing chapter mentions " + " / ".join(terms))
        return
    print(f"existing coverage of {' / '.join(terms)}:")
    for n, s in sorted(hits, key=lambda x: -x[0]):
        drift, total = slice_drift(s, roots)
        state = f"{drift} of {total} files changed since" if drift else "current"
        vol = f" [vol {slice_volume(s)}]" if slice_volume(s) else ""
        print(f"    chapter {s['id']} {s['title']}{vol} - {n} mentions, {s.get('status')}, {state}")


def cmd_which(args):
    """The way in from a file you're about to touch: which chapter already explains it."""
    d, cfg = workspace(args)
    roots = Roots(d, cfg)
    plan = load_plan(d)
    idx = coverage_index(plan)
    html = os.path.join(d, "guide.html")
    for raw in args.paths:
        key = roots.normalize(raw)
        hits = list(idx.get(key, []))
        if not hits:
            hits = [s for s in plan["slices"]
                    if any(roots.in_prefix(key, p) for p in s.get("seed_paths", []))]
            if not hits:
                print(f"{key}: no chapter covers this file")
                continue
            print(f"{key}: in the scope of "
                  + ", ".join(f"chapter {s['id']} {s['title']}" for s in hits) + " (not yet written from)")
            continue
        # A file usually anchors one chapter and is read in passing by others; lead with its home.
        home = [s for s in hits if any(roots.in_prefix(key, p) for p in s.get("seed_paths", []))] or hits[:1]
        for s in home:
            state = file_state(key, (s.get("files") or {}).get(key), roots)
            note = f"  [file {state} since the chapter was written]" if state else ""
            where = f"  {html}#{anchor(s)}" if os.path.exists(html) else ""
            print(f"{key}: chapter {s['id']} {s['title']}{where}{note}")
        rest = [s for s in hits if s not in home]
        if rest:
            print("    also read by " + ", ".join(f"{s['id']} ({s['title']})" for s in rest))


def cmd_files(args):
    d, cfg = workspace(args)
    roots = Roots(d, cfg)
    s = find_slice(load_plan(d), args.id)
    files = s.get("files") or {}
    if not files:
        print(f"chapter {s['id']} has no recorded files yet (status: {s.get('status')})")
        return
    drift, total = slice_drift(s, roots)
    print(f"chapter {s['id']} {s['title']}: {total} files, {drift} changed since it was written")
    for f in sorted(files):
        state = file_state(f, files[f], roots)
        print(f"    {f}{'  ' + state if state else ''}")


def cmd_stale(args):
    d, cfg = workspace(args)
    roots = Roots(d, cfg)
    plan = load_plan(d)
    any_stale = False
    covered = set()
    for s in plan["slices"]:
        covered.update(s.get("files", {}))
        if s.get("status") != "done":
            continue
        changed = [(f, st) for f, h in s.get("files", {}).items() if (st := file_state(f, h, roots))]
        if changed:
            any_stale = True
            print(f"STALE {s['id']} {s['title']}")
            for f, st in changed:
                print(f"    {st}: {f}")
    if not any_stale:
        print("no done slice has changed files")
    inv = (load_json(os.path.join(d, "inventory.json")) or {}).get("files") or {}
    if inv:
        seeds = [p.rstrip("/") for s in plan["slices"] for p in s.get("seed_paths", [])]
        un = Counter()
        for f, n in inv.items():
            if f in covered or any(roots.in_prefix(f, p) for p in seeds):
                continue
            un["/".join(f.split("/")[:3])] += n
        if un:
            print("\nlargest areas not touched by any slice (re-run `map` first for fresh numbers):")
            for k, n in un.most_common(12):
                print(f"    {k}/  {n:,} lines")


def extract_sections(path, heading_pred):
    out, on = [], False
    for line in open(path):
        if line.startswith("## ") or line.startswith("### "):
            on = heading_pred(line)
            if on:
                out.append(line.rstrip())
            continue
        if on:
            out.append(line.rstrip())
    return "\n".join(out).strip()


def chapter_recaps(path):
    """The chapter recap: peer tone's closing 'In short', else teaching tone's
    'Say it out loud (chapter)', else the last section-level 'Say it out loud'."""
    text = open(path).read()
    body = r"\*\*:?\s*(.+(?:\n> .+)*)"
    for label in (r"In short", r"Say it out loud \(chapter\)", r"Say it out loud"):
        found = re.findall(r"^> \*\*" + label + r"[^*]*" + body, text, re.M)
        if found:
            return found
    return []


def cmd_digest(args):
    d, cfg = workspace(args)
    for s in load_plan(d)["slices"]:
        ch = slice_file(d, "chapters", s)
        if s.get("status") != "done" or not os.path.exists(ch):
            continue
        print(f"\n### {s['id']}. {s['title']}  ({s.get('type')}, {s.get('direction')})")
        rec = chapter_recaps(ch)
        if rec:
            print("Recap: " + re.sub(r"\n> ", " ", rec[-1]).strip())
        notes = slice_file(d, "notes", s)
        if args.recaps_only or not os.path.exists(notes):
            continue
        gaps = sorted(open_gaps(notes), key=lambda g: SEV_ORDER.get(g[0], 3))
        if gaps:
            print("Open gaps:")
            for sev, text in gaps:
                # citations stay in FINDINGS.md; the bookends only need the claim
                print(f"- [{sev}] " + re.sub(r"\s*\(`[^)]*\)", "", text))


SEV_RX = re.compile(r"^\s*[-*]\s+\[(high|med|medium|low)\]\s*", re.I)
SEV_ORDER = {"high": 0, "med": 1, "medium": 1, "low": 2}


def open_gaps(notes_path):
    """(severity, text) for each bullet under the notes' Open gaps heading."""
    items, on = [], False
    for line in open(notes_path):
        if line.startswith("## "):
            on = "open gap" in line.lower()
            continue
        if on and re.match(r"\s*[-*]\s+", line):
            m = SEV_RX.match(line)
            sev = m.group(1).lower() if m else "unrated"
            text = (SEV_RX.sub("", line) if m else re.sub(r"^\s*[-*]\s+", "", line)).strip()
            items.append((sev, text))
        elif on and line.startswith("  ") and items:
            items[-1] = (items[-1][0], items[-1][1] + " " + line.strip())
    return items


def write_findings(d, plan):
    """FINDINGS.md: every open gap from every researched slice, with citations, for the team.
    The guide itself keeps only a short list; this file holds the rest."""
    rows = []
    for s in plan["slices"]:
        notes = slice_file(d, "notes", s)
        if s.get("status") in ("researched", "written", "done") and os.path.exists(notes):
            rows += [(sev, s, text) for sev, text in open_gaps(notes)]
    shared = load_json(os.path.join(d, "shared.json")) or {}
    drift = shared.get("drifted", [])
    if not rows and not drift:
        return None
    rows.sort(key=lambda r: (SEV_ORDER.get(r[0], 3), r[1]["id"]))
    out = ["# Findings", "",
           f"Generated {now()} by `fg.py build` from the research notes' Open gaps. Found by reading "
           "code, not by reproducing; each line cites where to look. Severity is the researcher's call.", "",
           "| sev | chapter | finding |", "|---|---|---|"]
    for sev, s, text in rows:
        cell = text.replace("|", "\\|")
        out.append(f"| {sev} | {s['id']} {s['title']} | {cell} |")
    if drift:
        out += ["", "## Copies that have drifted between repos", "",
                "Found by `fg.py map` (same trailing path, similar but not identical content). Hand-kept "
                "copies drift silently; each pair is either an intended difference or a bug in one copy.", "",
                "| similarity | ~lines differ | files |", "|---|---|---|"]
        for g in drift:
            out.append(f"| {int(g['similarity'] * 100)}% | {g['changed_lines']} | " +
                       " vs ".join(f"`{k}`" for k in g["files"]) + " |")
    path = os.path.join(d, "FINDINGS.md")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    return path, len(rows) + len(drift)


HTML_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guide_template.html")


def strip_h1(text):
    """Chapters are h2-rooted; the build owns the document's single h1."""
    return re.sub(r"\A\s*# [^\n]*\n+", "", text.strip() + "\n").strip()


def asof_line(s, roots, html):
    """A chapter's freshness, for a reader coming back to it months later."""
    files = s.get("files") or {}
    if not files:
        return None
    drift, total = slice_drift(s, roots)
    bits = []
    if s.get("git_head"):
        bits.append(f"as of {s['git_head'][:7]}")
    if s.get("updated"):
        bits.append(str(s["updated"])[:10])
    bits.append(f"{drift} of {total} files changed since" if drift else f"{total} files, none changed since")
    text = " \u00b7 ".join(bits)
    return f'<p class="asof{" drifted" if drift else ""}">{text}</p>' if html else f"*{text}*"


def with_asof(text, s, roots, html):
    """Put the freshness line under the chapter heading, before the direction meta line."""
    line = asof_line(s, roots, html)
    if not line:
        return text
    head, _, rest = text.partition("\n")
    return f"{head}\n\n{line}\n{rest}"


def coverage_section(d, cfg, plan, roots):
    """What the guide explains, what it doesn't, and what is only planned - so a reader can tell
    "no chapter covers this" apart from "I searched badly"."""
    idx = coverage_index(plan)
    done_ids = {s["id"] for s in plan["slices"] if s.get("status") == "done"}
    out = ["## Coverage", "",
           "Which files each chapter was written from, what no chapter covers yet, and what is planned "
           "but unwritten.", ""]
    rows = []
    for f in sorted(idx):
        chs = [s for s in idx[f] if s["id"] in done_ids]
        if chs:
            state = file_state(f, (chs[0].get("files") or {}).get(f), roots)
            rows.append((f, ", ".join(f"[{s['id']}](#{anchor(s)})" for s in chs), state))
    if rows:
        drifted = sum(1 for _, _, st in rows if st)
        out += [f"### Files the guide explains ({len(rows)}"
                + (f", {drifted} changed since" if drifted else "") + ")", "",
                "| file | chapter | since |", "|---|---|---|"]
        out += [f"| `{f}` | {c} | {st} |" for f, c, st in rows] + [""]

    inv = (load_json(os.path.join(d, "inventory.json")) or {}).get("files") or {}
    if inv:
        seeds = [p.rstrip("/") for s in plan["slices"] for p in s.get("seed_paths", [])]
        un = Counter()
        for f, n in inv.items():
            if f not in idx and not any(roots.in_prefix(f, p) for p in seeds):
                un["/".join(f.split("/")[:3])] += n
        if un:
            out += ["### In scope, but no chapter covers it", "", "| area | lines |", "|---|---:|"]
            out += [f"| `{k}/` | {n:,} |" for k, n in un.most_common(20)] + [""]

    pend = [s for s in plan["slices"] if s.get("status") != "done"]
    if pend:
        out += ["### Planned, not written", "", "| # | chapter | status |", "|---|---|---|"]
        out += [f"| {s['id']} | {s['title']} | {s.get('status')} |" for s in pend] + [""]

    nc = plan.get("not_covered") or []
    if nc:
        out += ["### Deliberately out of scope", ""]
        out += [f"- `{', '.join(x.get('paths') or [])}` - {x.get('why', '')}" for x in nc] + [""]

    vols = volumes_of(plan)
    if vols:
        written = Counter(slice_volume(s) for s in plan["slices"] if s.get("status") == "done")
        planned = Counter(slice_volume(s) for s in plan["slices"])
        out += ["### Volumes", "",
                "All volumes live in this one guide; a volume with no chapters yet is planned, "
                "not missing.", "", "| volume | area | chapters | lines |", "|---|---|---|---:|"]
        out += [f"| {v.get('id')} | {v.get('title', '')} | {written[v['id']]}/{planned[v['id']]} "
                f"| {v.get('lines', 0):,} |" for v in vols] + [""]
    return "\n".join(out).rstrip() + "\n"


def cmd_build(args):
    d, cfg = workspace(args)
    roots = Roots(d, cfg)
    plan = load_plan(d)
    title = cfg.get("title") or os.path.basename(os.path.abspath(".")) + " field guide"
    done = [s for s in plan["slices"] if s.get("status") == "done" and os.path.exists(slice_file(d, "chapters", s))]
    gl = os.path.join(d, "glossary.md")
    terms = {}
    if os.path.exists(gl):
        for line in open(gl):
            m = re.match(r"\s*-\s+\*\*(.+?)\*\*\s*[-:\u2014]\s*(.+)", line)
            if m:
                terms.setdefault(m.group(1).strip().lower(), (m.group(1).strip(), m.group(2).strip()))

    def groups_for(html):
        """(label, [markdown parts]) in reading order; labels become sidebar groups in HTML."""
        groups = []
        ov = os.path.join(d, "chapters", "00-overview.md")
        if os.path.exists(ov):
            groups.append(("Overview", [strip_h1(open(ov).read())]))
        def chapter_md(s):
            return with_asof(strip_h1(open(slice_file(d, "chapters", s)).read()), s, roots, html)

        if done:
            # Volumes are sidebar groups in one document, not separate guides: one search, one
            # glossary, one chapter numbering, so a chapter can refer across volumes.
            vols = {v["id"]: v for v in volumes_of(plan)}
            keys = [slice_volume(s) for s in done]
            if len({k for k in keys if k}) > 1:
                for vid in dict.fromkeys(keys):
                    v = vols.get(vid)
                    label = volume_label(v) if v else "Chapters"
                    groups.append((label, [chapter_md(s) for s in done if slice_volume(s) == vid]))
            else:
                groups.append(("Chapters", [chapter_md(s) for s in done]))
        cl = os.path.join(d, "chapters", "99-closing.md")
        if os.path.exists(cl):
            groups.append(("Wrap-up", [strip_h1(open(cl).read())]))
        ref = [coverage_section(d, cfg, plan, roots)]
        if terms:
            ref.append("## Glossary\n\n" + "\n".join(
                f"- **{t}**: {dfn}" for t, dfn in (terms[k] for k in sorted(terms))))
        groups.append(("Reference", ref))
        return groups

    pending = [s for s in plan["slices"] if s.get("status") not in ("done", "skipped")]
    head = [f"# {title}", "",
            f"<p class=\"meta\">Built {now()} from {len(done)} of {len(done) + len(pending)} planned chapters"
            + (f"; {len(pending)} not yet written" if pending else "") + ".</p>", ""]

    def body(groups, markers):
        out = []
        for label, parts in groups:
            if markers:
                out.append(f'<div class="part" data-part="{label}"></div>')
            out += parts
        return "\n\n".join(out)

    fmt = cfg.get("format", "md")
    if fmt in ("md", "both"):
        # Markdown has no sidebar, so it gets an inline chapter list instead
        toc = [f"- [{s['id'].lstrip('0') or s['id']}. {s['title']}](#{anchor(s)})" for s in done]
        md = "\n".join(head + (["**Chapters**", ""] + toc + [""] if toc else [])) + "\n\n" + body(groups_for(False), False) + "\n"
        out_md = os.path.join(d, "GUIDE.md")
        with open(out_md, "w") as f:
            f.write(md)
        print(f"wrote {out_md} ({len(md.split()):,} words)")
    if fmt in ("html", "both"):
        md = "\n".join(head) + "\n\n" + body(groups_for(True), True) + "\n"
        with open(HTML_TEMPLATE) as f:
            template = f.read()
        html = template.replace("__TITLE__", title).replace(
            "__MARKDOWN__", md.replace("</script>", "<\\/script>"))
        out_html = os.path.join(d, "guide.html")
        with open(out_html, "w") as f:
            f.write(html)
        print(f"wrote {out_html}")
    fnd = write_findings(d, plan)
    if fnd:
        print(f"wrote {fnd[0]} ({fnd[1]} findings)")


def anchor(s):
    text = f"{int(s['id']) if s['id'].isdigit() else s['id']}. {s['title']}"
    return re.sub(r"[^a-z0-9 -]", "", text.lower()).strip().replace(" ", "-")


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def main():
    ap = argparse.ArgumentParser(prog="fg.py")
    ap.add_argument("--dir", help="workspace dir (default: auto-locate)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("locate")
    p = sub.add_parser("init")
    p.add_argument("--dir", required=True)
    p.add_argument("--format", choices=["md", "html", "both"])
    p.add_argument("--scope", help="comma-separated path prefixes")
    p.add_argument("--depth", choices=["orientation", "standard", "deep"])
    p.add_argument("--audience")
    p.add_argument("--tone", choices=["peer", "teaching"])
    p.add_argument("--repo", action="append", metavar="NAME=PATH",
                   help="multi-repo guide: repeat once per repo (opt-in; omit for a single repo)")
    p.add_argument("--force", action="store_true",
                   help="re-scope a workspace that already holds written chapters, orphaning them")
    p = sub.add_parser("config")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="e.g. models.research=sonnet, depth=deep, research_budget.max_files=20; "
                        "VALUE null removes the key from that layer")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--user", action="store_true", help="write to ~/.claude/field-guide.json")
    g.add_argument("--local", action="store_true", help=f"write to <workspace>/{LOCAL_CONFIG}")
    g.add_argument("--workspace", action="store_true", help=f"write to <workspace>/{WS_CONFIG}")
    p.add_argument("--json", action="store_true", help="print the resolved config as JSON")
    sub.add_parser("map")
    sub.add_parser("repos")
    p = sub.add_parser("measure")
    p.add_argument("paths", nargs="+")
    sub.add_parser("status")
    p = sub.add_parser("check")
    p.add_argument("id")
    p = sub.add_parser("mark")
    p.add_argument("id")
    p.add_argument("--status", default="done",
                   choices=["proposed", "selected", "researched", "written", "done", "skipped"])
    p = sub.add_parser("usage", help="log a subagent run, or report on the log")
    p.add_argument("--json", action="store_true", help="print the raw log")
    us = p.add_subparsers(dest="usage_cmd")
    q = us.add_parser("log", help="append one subagent run to usage.jsonl")
    q.add_argument("id", help="slice id, or - for guide-level steps (planner, bookends)")
    q.add_argument("step", choices=["planner", "research", "write", "bookends", "reviewer", "followup"])
    q.add_argument("tokens", type=int, help="total tokens the agent reported")
    q.add_argument("--model", help="model the agent ran on (inherit, sonnet, ...)")
    q.add_argument("--tool-uses", type=int)
    q.add_argument("--duration-ms", type=int)
    q.add_argument("--note", help="e.g. 'refresh', 'rewrite', 'citation fix'")
    p = sub.add_parser("pick")
    p.add_argument("--port", type=int, default=0, help="default: any free port")
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for a choice")
    p.add_argument("--no-open", action="store_true", help="print the URL instead of opening a browser")
    p.add_argument("--default-run", choices=["one", "three", "all", "none"], default="one",
                   help="which 'how many to run now' option starts selected")
    p = sub.add_parser("remaining")
    p.add_argument("--include-proposed", action="store_true", help="also count slices never selected")
    p = sub.add_parser("volume", help="volumes within this one workspace")
    vs = p.add_subparsers(dest="volume_cmd")
    q = vs.add_parser("add", help="register a volume (a named slice of scope)")
    q.add_argument("id")
    q.add_argument("--title", required=True)
    q.add_argument("--paths", required=True, help="comma-separated path prefixes")
    q.add_argument("--summary")
    q = vs.add_parser("start", help="bring a volume's paths into scope, ready to map and plan")
    q.add_argument("id")
    vs.add_parser("list")
    p = sub.add_parser("topic", help="what the guide already says about a topic")
    p.add_argument("terms", nargs="+")
    p = sub.add_parser("which", help="which chapter explains these files")
    p.add_argument("paths", nargs="+")
    p = sub.add_parser("files", help="the files a chapter was written from")
    p.add_argument("id")
    sub.add_parser("stale")
    p = sub.add_parser("digest")
    p.add_argument("--recaps-only", action="store_true", help="omit open gaps (for the writer)")
    sub.add_parser("build")
    args = ap.parse_args()
    {"locate": cmd_locate, "init": cmd_init, "config": cmd_config, "map": cmd_map, "measure": cmd_measure,
     "status": cmd_status, "remaining": cmd_remaining, "pick": cmd_pick, "repos": cmd_repos, "usage": cmd_usage, "check": cmd_check, "mark": cmd_mark, "stale": cmd_stale,
     "which": cmd_which, "files": cmd_files, "volume": cmd_volume, "topic": cmd_topic,
     "digest": cmd_digest, "build": cmd_build}[args.cmd](args)


if __name__ == "__main__":
    main()
