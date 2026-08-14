---
name: repomap
description: Plan optimal graph partitions for any codebase before running graphify. Use when the user wants to analyze a repository's structure for graphing, asks how to split a large codebase into graphs, mentions "repomap", wants to understand coupling between components, or needs to decide how many graphs to build. Also triggers on "plan the graph", "how should I graphify this repo", "what's the best way to graph this codebase", "show me the repo structure", or any codebase analysis that precedes graph construction.
---

# Repomap

Partition any codebase into optimal graph groups with cross-graph bridge metadata. Runs *before* graphify to plan the strategy — how many graphs, what goes in each, and how agents traverse between them.

## When to Use

- Before running `/graphify` on a repo with >50 files or multiple languages
- When the user wants to understand architectural coupling between components
- When deciding whether a repo needs 1 graph or N graphs
- When planning how AI agents should navigate a multi-graph codebase
- When the user wants a full end-to-end pipeline (partition + AST extraction + manifest)

## Decision: Which Script to Run

| User intent | Script | Command |
|---|---|---|
| **Planning only** — understand structure, see coupling, get graphify commands | `repomap.py` | See "Plan Only" below |
| **Full pipeline** — partition + build AST graphs + write manifest | `run_all.py` | See "End-to-End" below |

## Plan Only (`repomap.py`)

Use this when the user just wants to see the partition plan, coupling analysis, or bridge metadata — no graph files are written.

```bash
# Default analysis
python ${CLAUDE_PLUGIN_ROOT}/repomap.py <repo_path>

# JSON output (for programmatic consumption)
python ${CLAUDE_PLUGIN_ROOT}/repomap.py <repo_path> --json

# Coverage validation only
python ${CLAUDE_PLUGIN_ROOT}/repomap.py <repo_path> --validate

# Custom thresholds
python ${CLAUDE_PLUGIN_ROOT}/repomap.py <repo_path> --coupling 0.20 --max-files 100 --max-words 200000
```

### Flag Heuristics

| Flag | Default | When to change |
|---|---|---|
| `--coupling` | 0.05 | Raise to 0.15–0.25 for monorepos to see layer boundaries. Raise to 0.30+ for maximum cross-graph bridge signal. |
| `--max-files` | 250 | Lower for smaller context windows or if the user wants more granular graphs. |
| `--max-words` | 600000 | Lower if targeting a model with a small context window. |
| `--json` | off | Use when output feeds into another tool or agent workflow. |
| `--validate` | off | Use when the user only wants a coverage report. |

## End-to-End Pipeline (`run_all.py`)

Use this when the user wants actual graph files built — requires `graphify` to be installed.

```bash
# Default (output to <repo>/repomap-out/)
python ${CLAUDE_PLUGIN_ROOT}/run_all.py <repo_path>

# Custom output directory
python ${CLAUDE_PLUGIN_ROOT}/run_all.py <repo_path> --target ./output-dir

# With custom coupling threshold
python ${CLAUDE_PLUGIN_ROOT}/run_all.py <repo_path> --target ~/graphs/project --coupling 0.20
```

### Output Structure

```
<target>/
  repomap.json          # manifest: all graphs, metadata, bridges
  graph-0/graph.json    # AST graph for partition 0
  graph-0/extraction.json
  graph-1/graph.json
  ...
```

The `repomap.json` manifest contains:
- `repo`: source repo path
- `graphs[]`: per-graph metadata (nodes, edges, communities, dirs, lang, directed)
- `bridges[]`: cross-graph shared symbols for agent traversal

## Reading the Output

### Coupling Pairs
High Jaccard = components share many import symbols and will merge at default threshold.

### Graph Recommendations
- `directed=True` — language supports directional imports (Python, Go, Rust) — graphify should use `--directed`
- `lang=` — dominant language, determines AST extraction strategy

### Cross-Graph Bridges
Symbols an agent can use to jump between graphs. When traversing Graph A and encountering a bridge symbol, load the linked graph for the other side of the interface.

## Tuning the Coupling Threshold

| Threshold | Effect | Use When |
|---|---|---|
| 0.05 (default) | Aggressive merge — fewer, larger graphs | Small repos, single-language |
| 0.15–0.25 | Balanced — reveals layer boundaries | Multi-package monorepos |
| 0.30+ | Conservative — many small graphs, rich bridges | Maximum cross-graph signal |

**Rule of thumb:** if the default produces one mega-graph with 0 bridges, raise the threshold until architectural layers split apart.

## Exclusions (automatic)

- `node_modules`, `vendor`, `.venv`, `dist`, `target`, `__pycache__`
- `.git`, `.svn`, hidden directories
- `generated*/`, `coverage/`, `htmlcov/`
- Date-prefixed directories (e.g., `2026-07-17-*`) — output/data, not source

## Answering Questions Against a Repomap

When a question asks "how is X implemented" or "how does X work":

1. **Find the domain entry point** for X (e.g. for DLT: `DLTAgent`, not the generic chunker). Look for class/module names that match the query term directly.
2. **Select all relevant graphs** — use `repomap.json` `feature_tags` and `dirs` to identify every graph the flow touches. Match query keywords against tags first, then directory names.
3. **Trace the call chain forward** from the entry point through graph edges. Follow function calls, not just structural relationships.
4. **Note source-specific branches** — enum forks (e.g. `kDlt` vs `kNdas`), feature flags, or type dispatches that specialize the generic pipeline for X.
5. **Follow bridges across graph boundaries** — if the flow exits one graph, use bridge symbols in `repomap.json` to find the continuation in the linked graph. Do not stop at one graph.
6. **The generic pipeline is context; the source-specific path is the answer.** Describe what's unique to X, referencing the shared infrastructure only as framing.

## Multi-Graph Query Routing

When answering a cross-cutting question that spans multiple architectural layers:

1. Read `repomap.json` — check `feature_tags` and `keywords` fields for query-term matches
2. Rank graphs by relevance: exact tag match > keyword match > directory-name substring match
3. Load communities from the top 2–4 matching graphs (not all graphs)
4. Synthesize the answer in **entry-point-first order**: start with the domain-specific initiator, trace through middleware/infrastructure, end at the output/sink
5. If no feature tags match, fall back to scanning `dirs` for path segments containing query terms

## Prerequisites

- Python 3.9+
- `repomap.py`: no dependencies (stdlib only)
- `run_all.py`: requires `graphify` — install with `pip install graphifyy`
