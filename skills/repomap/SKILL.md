---
name: repomap
description: Plan optimal graph partitions for any codebase before running graphify. Use this skill whenever the user wants to analyze a repository's structure for graphing, asks how to split a large codebase into graphs, mentions "repomap", wants to understand coupling between components, or needs to decide how many graphs to build for a project. Also use when the user says things like "plan the graph", "how should I graphify this repo", "what's the best way to graph this codebase", or "show me the repo structure". Triggers on any codebase analysis that precedes graph construction.
---

# Repomap

Partition any codebase into optimal graph groups with cross-graph bridge metadata. This runs *before* graphify to plan the strategy — how many graphs, what goes in each, and how agents traverse between them.

## When to Use

- Before running `/graphify` on a repo with >50 files or multiple languages
- When the user wants to understand architectural coupling between components
- When deciding whether a repo needs 1 graph or N graphs
- When planning how AI agents should navigate a multi-graph codebase

## The Script

Location: `repomap.py` at the root of this repository.

## Quick Start

```bash
python repomap.py <repo_path>
```

## Commands

```bash
# Default analysis (coupling threshold 0.05)
python repomap.py <repo_path>

# Higher threshold = more graphs, more bridges (useful for seeing cross-layer connections)
python repomap.py <repo_path> --coupling 0.20

# Constrain graph size
python repomap.py <repo_path> --max-files 100 --max-words 200000

# JSON output (for programmatic consumption by agents)
python repomap.py <repo_path> --json

# Coverage validation only
python repomap.py <repo_path> --validate
```

## How It Works

1. **Walk** the repo, classify every source file by language
2. **Group** files into "components" — meaningful directory units (not generic layout dirs like `src/`, `lib/`)
3. **Compute coupling** between components using shared import tokens (Jaccard similarity)
4. **Union-Find** merge components whose coupling >= threshold
5. **Split** oversized groups (unless star topology — hub-and-spoke stays together)
6. **Merge** tiny groups (<4 files) into best-coupled neighbour
7. **Compute bridges** — for component pairs that landed in *different* final graphs, extract their shared domain-specific import symbols as traversal anchors
8. **Validate** 100% file coverage
9. **Emit** graph recommendations with `/graphify` commands

## Reading the Output

### Coupling Pairs
```
Top coupling pairs:
  packages/doc-ingest  <->  packages/profile-ingest  (Jaccard=0.357)
```
High Jaccard = these components share many import symbols. They'll likely merge into one graph at the default threshold.

### Graph Recommendations
```
Graph  1:  server, knowledge_extract, kb_core  +5 more
         66 files  ~16,982 words  lang=python  directed=True
```
- `directed=True` means the language supports directional imports (Python, Go, Rust, etc.) — graphify should use `--directed`
- `lang=` is the dominant language — determines AST extraction strategy

### Cross-Graph Bridges
```
Cross-graph bridges:
  Graph 9 <-> Graph 12  (weight=0.294)  via: kb_core
  Graph 2 <-> Graph 8   (weight=0.281)  via: kb_core, resume_kb_server.settings, knowledge_extract.probe
```
These are the symbols an agent can use to jump between graphs. When traversing Graph 9 and encountering `kb_core`, the bridge tells you Graph 12 also references it — load that graph for the other side of the interface.

### JSON Structure (for agents)
```json
{
  "bridges": [
    {
      "source": 8,
      "target": 11,
      "weight": 0.294,
      "symbols": ["kb_core", "knowledge_extract.probe"]
    }
  ]
}
```

## Tuning the Coupling Threshold

The `--coupling` flag controls how aggressively components merge:

| Threshold | Effect | Use When |
|-----------|--------|----------|
| 0.05 (default) | Aggressive merge — fewer, larger graphs | Small repos, single-language |
| 0.15–0.25 | Balanced — reveals layer boundaries | Multi-package monorepos |
| 0.30+ | Conservative — many small graphs, rich bridges | When you want maximum cross-graph signal |

**Rule of thumb:** if the default produces one mega-graph with 0 bridges, raise the threshold until you see the architectural layers split apart. The bridges that appear are the real interface points.

## Workflow: Repomap → Graphify

1. Run repomap to get the partition plan
2. Review the bridges — do they match your mental model of the architecture?
3. If a graph includes dead/orphaned code (like a removed feature still on disk), exclude it
4. Run the `/graphify` commands from the output
5. Use bridges as the lookup table when an agent needs cross-graph context

## What Gets Excluded Automatically

- `node_modules`, `vendor`, `.venv`, `dist`, `target`, `__pycache__`
- `.git`, `.svn`, hidden directories
- `generated*/`, `coverage/`, `htmlcov/`
- Date-prefixed directories (e.g., `2026-07-17-*`) — treated as output/data, not source

## Symbol Noise Filtering

Bridge symbols automatically filter out stdlib/ubiquitous imports that add no architectural signal:
- Python: `pathlib`, `datetime`, `os`, `sys`, `json`, `typing`, `pydantic`, `pytest`
- JS/TS: `react`, `react-dom`, `next`, `vue`
- General: `asyncio`, `unittest`, `collections`, `functools`

Only domain-specific symbols survive as bridge anchors.
