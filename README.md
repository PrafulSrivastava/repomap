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
8. **Validates coverage** — guarantees 100% of source files appear in at least one graph

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
Files      : 549  (~358,900 words)
Components : 78 (meaningful dirs with source files)
Excluded   : __pycache__, generated_code, third_party
Coverage   : 549/549 files (100.0%) [OK]

=> 3 graph(s) recommended

  Graph  1:  AmaAgent, AppAgent, CollectDAgent  +66 more
             532 files  ~345,542 words  lang=cpp  directed=True  [oversized]
             reason: star topology - splitting would duplicate the hub into every sub-graph
  Graph  2:  tools, LogAnalyzer, Analyzer
             9 files  ~7,169 words  lang=python  directed=True
  Graph  3:  CommunicationManager, TriggerManager
             8 files  ~6,189 words  lang=doc  directed=False

# Graphify commands:
  # Graph 1
  /graphify /home/user/myproject /home/user/myproject/agents/AmaAgent ... --directed
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

## Requirements

Python 3.9+, no dependencies beyond the standard library.

```bash
python repomap.py --help
```

## License

MIT
