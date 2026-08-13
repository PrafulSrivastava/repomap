# repomap

**Automatically partition any codebase into the minimum effective set of knowledge graphs.**

`repomap` analyzes a repository and tells you exactly how to split it into graphs for tools like [graphify](https://github.com/graphifyy/graphify) — without redundancy, without missing anything, and without manual tuning per repo.

Tested on C++ embedded systems, Python services, TypeScript frontends, and mixed-language monorepos.

---

## The problem

Knowledge graph tools work best on focused, coherent subsets of a codebase. Feed them a 500-file repo at once and you get noise. Split it manually and you miss cross-cutting dependencies. `repomap` solves this automatically.

## How it works

1. **Walks the repo** — classifies every source file by language and domain
2. **Identifies components** — meaningful architectural units, not layout folders (`src/`, `inc/`, `test/` are transparent)
3. **Measures coupling** — Jaccard similarity on shared import/include tokens between components
4. **Merges by coupling** — Union-Find groups tightly coupled components together
5. **Groups non-code by tree** — yaml/json/toml/doc files have no imports; they're grouped by directory proximity instead
6. **Splits oversized groups** — detects star topology (keep whole) vs. domain clusters (split)
7. **Absorbs tiny groups** — components too small to stand alone merge into their nearest neighbour
8. **Computes cross-graph bridges** — identifies shared domain symbols between components in different graphs, enabling agents to traverse across graph boundaries
9. **Validates coverage** — guarantees 100% of source files appear in at least one graph

## Usage

```bash
python repomap.py <repo_root>
python repomap.py <repo_root> --validate         # coverage check only
python repomap.py <repo_root> --json             # machine-readable output
python repomap.py <repo_root> --coupling 0.10    # raise merge threshold (fewer, larger graphs)
python repomap.py <repo_root> --max-files 150    # lower graph size cap
```

## Example output

```
Repo       : /home/user/myproject
Files      : 147  (~78,477 words)
Components : 31 (meaningful dirs with source files)
Excluded   : __pycache__, node_modules, .venv, dist
Coverage   : 147/147 files (100.0%) [OK]

=> 13 graph(s) recommended

  Graph  1:  server, knowledge_extract, kb_core  +5 more
             66 files  ~16,982 words  lang=python  directed=True
  Graph  2:  ui, views
             17 files  ~2,964 words  lang=typescript  directed=False
  Graph  3:  components, hooks
             10 files  ~2,849 words  lang=typescript  directed=False

Cross-graph bridges:
  Graph 1 <-> Graph 3  (weight=0.294)  via: kb_core
  Graph 1 <-> Graph 2  (weight=0.281)  via: kb_core, resume_kb_server.settings, knowledge_extract.probe

# Graphify commands:
  # Graph 1
  /graphify /home/user/myproject/packages/knowledge-extract --directed
```

### Cross-graph bridges

When the partitioning splits coupled components into separate graphs, `repomap` reports the shared domain-specific import symbols between them. These are traversal anchors — an AI agent working in Graph 1 that encounters `kb_core` can look up the bridges and know that Graph 3 also references it, loading that graph for the other side of the interface.

Bridge symbols are filtered of stdlib noise (`pathlib`, `datetime`, `os`, `react`, `pydantic`, etc.) so only architecturally meaningful symbols survive.

**JSON output** (`--json`):
```json
{
  "bridges": [
    {
      "source": 0,
      "target": 2,
      "weight": 0.294,
      "symbols": ["kb_core", "knowledge_extract.probe"]
    }
  ]
}
```

## Supported languages

| Category | Languages |
|---|---|
| Compiled | C/C++, Go, Rust, Java, C#, Kotlin, Swift, Scala |
| Scripted | Python, JavaScript/TypeScript, Ruby, Shell |
| Config/Infra | YAML, JSON, TOML, Terraform/HCL, Dockerfile |
| Data/Schema | SQL, Protocol Buffers, XML/ARXML |
| Docs | Markdown, reStructuredText, PlantUML, DrawIO |

## Algorithm details

### Component detection

Files are grouped into **components** — the deepest ancestor directory whose name is not a generic layout name. This means `core/DataCollector/src/collector.cpp` and `core/DataCollector/inc/collector.h` both belong to the `core/DataCollector` component, not to separate `src` and `inc` components.

Layout dirs that are collapsed: `src`, `inc`, `include`, `test`, `tests`, `spec`, `api`, `doc`, `docs`, `lib`, `libs`, `assets`, `images`, `mocks`, `fixtures`, `stubs`, `bin`, `obj`, `build`.

### Coupling measurement

For code files, coupling between two components is the [Jaccard similarity](https://en.wikipedia.org/wiki/Jaccard_index) of their import token sets. A score of 0.05 (default) means "5% overlap in what they import" — enough to indicate they share a dependency context.

### Star topology detection

When one component appears in coupling pairs with ≥55% of all other components, it's a hub. Splitting such a group would duplicate the hub into every sub-graph — creating redundancy instead of reducing it. `repomap` detects this and keeps star-topology groups whole, explaining why.

### Non-code grouping

YAML, JSON, TOML, and doc files have no import statements, so their coupling is always zero. For these, `repomap` uses directory tree proximity: siblings under the same parent merge first, then parent/child pairs, then path-prefix length.

### Exclusions

Automatically skipped:
- Noise dirs: `node_modules`, `vendor`, `venv`, `.venv`, `dist`, `build`, `target`, `.git`, cache dirs, test coverage output
- Generated dirs: `generated_code`, `gen`, `__pycache__`, `*.egg-info`
- Date-prefixed dirs/files: `2024-01-15-experiment/` — these are data/output artifacts, not source architecture

## Options

| Flag | Default | Description |
|---|---|---|
| `--max-files` | 250 | Max files per graph before splitting |
| `--max-words` | 600,000 | Max estimated words per graph before splitting |
| `--coupling` | 0.05 | Jaccard threshold to merge components |
| `--json` | off | Output as JSON (useful for tooling integration) |
| `--validate` | off | Show coverage report only, no graph commands |
| `--verbose` | off | Show duplicated anchor files |

## Claude Code skill

A ready-made skill for Claude Code is included at `skills/repomap/SKILL.md`. To install it locally:

```bash
cp -r skills/repomap ~/.claude/skills/repomap
```

This enables Claude to invoke repomap automatically when you ask questions like "how should I graphify this repo" or "plan the graph for this codebase".

## Requirements

Python 3.9+, no dependencies beyond the standard library.

```bash
python repomap.py --help
```

## License

MIT
