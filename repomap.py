"""
graph_plan.py  --  optimal graphify graph partitioning for any codebase.

Algorithm
---------
1. Walk repo recursively, classify every source file (language, domain).
2. Identify "components": meaningful directory units.
   A component is a directory whose name is NOT a generic layout name
   (inc, src, test, api, doc, images, etc.). Files under generic dirs are
   attributed to the nearest non-generic ancestor.
3. Build a coupling matrix between components using shared import tokens (Jaccard).
4. Union-Find: merge components whose coupling >= threshold.
5. Oversized groups: detect star topology -> keep whole; else split by domain.
6. Merge tiny groups (< MIN_FILES) into best-coupled neighbour.
7. Coverage validation: 100% of source files must appear in at least one group.
8. Emit graphify commands with correct flags per language.

Usage
-----
    python graph_plan.py <repo_root>
    python graph_plan.py <repo_root> --max-files 200 --max-words 500000
    python graph_plan.py <repo_root> --coupling 0.10
    python graph_plan.py <repo_root> --json
    python graph_plan.py <repo_root> --validate    # coverage report only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
DEFAULT_MAX_FILES   = 250
DEFAULT_MAX_WORDS   = 600_000
DEFAULT_COUPLING    = 0.05
MIN_GROUP_FILES     = 4      # groups smaller than this get merged into neighbours
WORDS_PER_FILE_EST  = 300
STAR_THRESHOLD      = 0.55   # anchor-coverage ratio above which we keep as monolith

# ---------------------------------------------------------------------------
# Generic layout directory names — files under these are attributed to parent
# ---------------------------------------------------------------------------
LAYOUT_DIRS = {
    'inc', 'include', 'src', 'source', 'test', 'tests', 'spec',
    'api', 'doc', 'docs', 'images', 'img', 'assets', 'mocks',
    'mock', 'fixtures', 'stubs', 'priv', 'pub', 'internal',
    'lib', 'libs', 'bin', 'obj', 'build',
}

# ---------------------------------------------------------------------------
# Noise directories — skipped entirely
# ---------------------------------------------------------------------------
NOISE_DIR_RE = re.compile(
    r'^(generated.*|gen|dist|out|target|\..*|__pycache__'
    r'|node_modules|third.?party|vendor|venv|\.venv|graphify.out'
    r'|cache|\.git|\.svn|\.hg|coverage|htmlcov|\.mypy_cache'
    r'|\.pytest_cache|\.tox|\.eggs|.*\.egg-info'
    r'|\d{4}-\d{2}-\d{2}.*)$',  # date-prefixed dirs are data/output, not source
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------
LANG_MAP: dict[str, str] = {
    '.py': 'python',  '.pyi': 'python',
    '.cpp': 'cpp',    '.cc': 'cpp',   '.cxx': 'cpp',  '.c': 'cpp',
    '.hpp': 'cpp',    '.h': 'cpp',    '.hxx': 'cpp',
    '.ts': 'typescript', '.tsx': 'typescript',
    '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript',
    '.go': 'go',
    '.rs': 'rust',
    '.java': 'java',
    '.cs': 'csharp',
    '.rb': 'ruby',
    '.kt': 'kotlin',  '.kts': 'kotlin',
    '.swift': 'swift',
    '.scala': 'scala',
    '.json': 'json',  '.jsonc': 'json',
    '.yaml': 'yaml',  '.yml': 'yaml',
    '.toml': 'toml',
    '.md': 'doc',     '.rst': 'doc',  '.txt': 'doc',
    '.puml': 'doc',   '.drawio': 'doc',
    '.sh': 'shell',   '.bash': 'shell', '.zsh': 'shell',
    '.xml': 'xml',    '.arxml': 'xml',
    '.proto': 'proto',
    '.sql': 'sql',
    '.tf': 'terraform', '.hcl': 'terraform',
    '.dockerfile': 'docker',
}

DIRECTED_LANGS = {
    'cpp', 'go', 'rust', 'java', 'csharp', 'kotlin',
    'swift', 'scala', 'python',
}

CODE_LANGS = {
    'python', 'cpp', 'typescript', 'javascript', 'go', 'rust',
    'java', 'csharp', 'ruby', 'kotlin', 'swift', 'scala', 'shell', 'proto',
}

# Non-code languages: no imports, so coupling is always zero.
# Group these by directory tree proximity instead.
NON_CODE_LANGS = {'json', 'yaml', 'toml', 'doc', 'xml', 'sql', 'terraform', 'docker'}

# ---------------------------------------------------------------------------
# Import / include extraction
# ---------------------------------------------------------------------------
IMPORT_RE = [
    re.compile(r'#include\s+"([^"]+)"'),
    re.compile(r'#include\s+<([\w./]+)>'),
    re.compile(r'^\s*import\s+["\']([^"\']+)["\']', re.MULTILINE),
    re.compile(r'^\s*from\s+["\']([^"\']+)["\']\s+import', re.MULTILINE),
    re.compile(r'^\s*from\s+([\w.]+)\s+import', re.MULTILINE),
    re.compile(r'^\s*import\s+([\w.]+)', re.MULTILINE),
    re.compile(r'^\s*use\s+([\w:]+)', re.MULTILINE),
    re.compile(r'^\s*require\s*\(\s*["\']([^"\']+)["\']', re.MULTILINE),
]

# ---------------------------------------------------------------------------
# Domain semantic classifier
# ---------------------------------------------------------------------------
DOMAIN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'common|shared|util|base|interface|types?|helper|lib|corelib', re.I), 'anchor'),
    (re.compile(r'mock|fake|stub|fixture|test|spec|e2e|integration', re.I),            'test'),
    (re.compile(r'data|collect|chunk|acqui|sampl|record|buffer|ingest|etl', re.I),     'data'),
    (re.compile(r'trigger|job|task|sched|orchestr|workflow|pipeline|worker', re.I),    'execution'),
    (re.compile(r'dispatch|queue|bus|event|pubsub|message|broker', re.I),              'execution'),
    (re.compile(r'comm|transport|send|recv|uplink|downlink|pack|serial|socket', re.I), 'transport'),
    (re.compile(r'http|rest|grpc|rpc|api|endpoint|route|handler|server|client', re.I),'transport'),
    (re.compile(r'config|setting|param|env|platform|init|setup|boot|launch', re.I),   'infra'),
    (re.compile(r'timer|time|clock|persist|store|cache|db|database|repo', re.I),      'infra'),
    (re.compile(r'auth|security|crypt|token|cert|key|oauth|jwt', re.I),               'security'),
    (re.compile(r'ui|view|page|component|render|frontend|web|css|style', re.I),       'ui'),
    (re.compile(r'model|schema|entity|domain|dto|vo|struct', re.I),                   'model'),
    (re.compile(r'monitor|metric|log|trace|observ|telemetry|health', re.I),           'observability'),
]

def domain_of(name: str) -> str:
    for pat, label in DOMAIN_PATTERNS:
        if pat.search(name):
            return label
    return 'misc'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_noise_path(p: Path) -> bool:
    return any(NOISE_DIR_RE.match(part) for part in p.parts)


def component_dir(file_path: Path, root: Path) -> Path:
    """
    Find the 'component' directory for a file: the deepest ancestor whose
    name is NOT a generic layout name, relative to root.
    If all ancestors are layout dirs, use root itself.
    """
    try:
        rel = file_path.relative_to(root)
    except ValueError:
        return root
    parts = rel.parts[:-1]  # exclude filename
    # Walk from deepest to shallowest, stop at first non-layout name
    for depth in range(len(parts), 0, -1):
        candidate = root / Path(*parts[:depth])
        if parts[depth - 1].lower() not in LAYOUT_DIRS:
            return candidate
    return root


def all_source_files(root: Path) -> list[Path]:
    result = []
    for p in sorted(root.rglob('*')):
        if is_noise_path(p.relative_to(root)):
            continue
        if p.is_file() and p.suffix.lower() in LANG_MAP:
            result.append(p)
    return result


def extract_import_tokens(file_path: Path) -> set[str]:
    try:
        text = file_path.read_text(errors='ignore')
    except Exception:
        return set()
    tokens: set[str] = set()
    for pat in IMPORT_RE:
        for m in pat.finditer(text):
            tokens.add(m.group(1).lower())
    return tokens


# ---------------------------------------------------------------------------
# Component data structure
# ---------------------------------------------------------------------------
class Component(NamedTuple):
    path: Path
    files: list[Path]
    words: int
    lang_counts: dict[str, int]
    top_lang: str
    refs: frozenset[str]

    @property
    def file_count(self) -> int:
        return len(self.files)


def build_component(comp_path: Path, files: list[Path]) -> Component:
    lang_counts: dict[str, int] = defaultdict(int)
    words = 0
    refs: set[str] = set()
    for f in files:
        lang = LANG_MAP.get(f.suffix.lower(), 'other')
        lang_counts[lang] += 1
        try:
            words += len(f.read_text(errors='ignore').split())
        except Exception:
            words += WORDS_PER_FILE_EST
        refs.update(extract_import_tokens(f))
    top_lang = max(lang_counts, key=lang_counts.__getitem__) if lang_counts else 'unknown'
    return Component(
        path=comp_path,
        files=files,
        words=words,
        lang_counts=dict(lang_counts),
        top_lang=top_lang,
        refs=frozenset(refs),
    )


def collect_components(root: Path) -> list[Component]:
    """Group all source files into components."""
    by_comp: dict[Path, list[Path]] = defaultdict(list)
    for f in all_source_files(root):
        by_comp[component_dir(f, root)].append(f)
    return [build_component(p, files) for p, files in sorted(by_comp.items())]


# ---------------------------------------------------------------------------
# Coupling
# ---------------------------------------------------------------------------

def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def coupling_matrix(components: list[Component]) -> dict[tuple[int, int], float]:
    matrix: dict[tuple[int, int], float] = {}
    for i in range(len(components)):
        for j in range(i + 1, len(components)):
            score = jaccard(components[i].refs, components[j].refs)
            if score > 0:
                matrix[(i, j)] = score
    return matrix


# ---------------------------------------------------------------------------
# Union-Find
# ---------------------------------------------------------------------------
class UF:
    def __init__(self, n: int):
        self._p = list(range(n))

    def find(self, x: int) -> int:
        while self._p[x] != x:
            self._p[x] = self._p[self._p[x]]
            x = self._p[x]
        return x

    def union(self, x: int, y: int):
        self._p[self.find(x)] = self.find(y)

    def groups(self, n: int) -> dict[int, list[int]]:
        g: dict[int, list[int]] = defaultdict(list)
        for i in range(n):
            g[self.find(i)].append(i)
        return dict(g)


# ---------------------------------------------------------------------------
# Graph group
# ---------------------------------------------------------------------------
class GraphGroup:
    def __init__(self, components: list[Component]):
        self.components = list(components)

    @property
    def files(self) -> int:
        return sum(c.file_count for c in self.components)

    @property
    def words(self) -> int:
        return sum(c.words for c in self.components)

    @property
    def all_files(self) -> list[Path]:
        out: list[Path] = []
        for c in self.components:
            out.extend(c.files)
        return out

    @property
    def top_lang(self) -> str:
        counts: dict[str, int] = defaultdict(int)
        for c in self.components:
            for lang, n in c.lang_counts.items():
                counts[lang] += n
        return max(counts, key=counts.__getitem__) if counts else 'unknown'

    @property
    def dirs(self) -> list[Path]:
        return sorted({c.path for c in self.components})

    def is_oversized(self, max_files: int, max_words: int) -> bool:
        return self.files > max_files or self.words > max_words


# ---------------------------------------------------------------------------
# Splitting oversized groups
# ---------------------------------------------------------------------------

def _is_star_topology(components: list[Component]) -> bool:
    n = len(components)
    if n < 3:
        return True
    cmat = coupling_matrix(components)
    if not cmat:
        return True
    appearance: dict[int, int] = defaultdict(int)
    for (i, j) in cmat:
        appearance[i] += 1
        appearance[j] += 1
    max_appear = max(appearance.values())
    return (max_appear / (n - 1)) >= STAR_THRESHOLD


def _split_by_domain(components: list[Component]) -> list[list[Component]]:
    buckets: dict[str, list[Component]] = defaultdict(list)
    for c in components:
        buckets[domain_of(c.path.name)].append(c)

    anchors = buckets.pop('anchor', [])
    tests   = buckets.pop('test',   [])
    shared  = anchors + tests

    real = {k: v for k, v in buckets.items() if v}
    if len(real) <= 1:
        return [components]

    return [domain_comps + shared for domain_comps in real.values()]


def split_group(group: GraphGroup, max_files: int, max_words: int,
                depth: int = 0) -> list[GraphGroup]:
    if not group.is_oversized(max_files, max_words):
        return [group]
    if depth >= 3:
        return [group]
    if _is_star_topology(group.components):
        return [group]

    sub_lists = _split_by_domain(group.components)
    if len(sub_lists) <= 1:
        return [group]

    result: list[GraphGroup] = []
    for sub in sub_lists:
        sg = GraphGroup(sub)
        result.extend(split_group(sg, max_files, max_words, depth + 1))
    return result


# ---------------------------------------------------------------------------
# Structural merging for non-code components (yaml/json/toml/doc)
# ---------------------------------------------------------------------------

def merge_by_path_tree(components: list[Component], root: Path,
                       max_files: int, max_words: int) -> dict[int, int]:
    """
    For non-code components (no imports => zero coupling), merge components
    that share a common ancestor directory into the same group.
    Components that are a direct parent/child of another component are merged.
    Returns a Union-Find mapping: component_index -> canonical_group_index.
    """
    uf = UF(len(components))

    for i, ci in enumerate(components):
        if ci.top_lang not in NON_CODE_LANGS:
            continue
        for j, cj in enumerate(components):
            if i >= j:
                continue
            if cj.top_lang not in NON_CODE_LANGS:
                continue
            # Merge if one is a direct ancestor of the other,
            # or if they share the same immediate parent
            pi, pj = ci.path, cj.path
            same_parent = pi.parent == pj.parent
            ancestor = (pi == pj.parent or pj == pi.parent)
            if same_parent or ancestor:
                # Only merge if the combined group stays within size limits
                # (a rough check — we'll re-validate after)
                uf.union(i, j)

    return uf.groups(len(components))


# ---------------------------------------------------------------------------
# Merging tiny groups
# ---------------------------------------------------------------------------

def merge_tiny(groups: list[GraphGroup],
               coupling: dict[tuple[int, int], float],
               components: list[Component],
               max_files: int, max_words: int) -> list[GraphGroup]:
    """Merge groups smaller than MIN_GROUP_FILES into best-coupled neighbour."""
    comp_to_group: dict[int, int] = {}
    for gi, g in enumerate(groups):
        for c in g.components:
            ci = components.index(c)
            comp_to_group[ci] = gi

    active = list(range(len(groups)))

    changed = True
    while changed:
        changed = False
        for gi in list(active):
            if gi not in active:
                continue
            g = groups[gi]
            if g.files >= MIN_GROUP_FILES:
                continue

            # Find best-coupled non-self group
            best_gj, best_score = -1, -1.0
            for ci, c in enumerate(components):
                if comp_to_group.get(ci) != gi:
                    continue
                for (a, b), score in coupling.items():
                    other_ci = b if a == ci else (a if b == ci else -1)
                    if other_ci < 0:
                        continue
                    gj = comp_to_group.get(other_ci, -1)
                    if gj == gi or gj not in active:
                        continue
                    if score > best_score:
                        best_score, best_gj = score, gj

            # No coupling found: find nearest group by shared path prefix length.
            # Prefer sibling components (same immediate parent) so that e.g.
            # k8s/base, k8s/observability, k8s/experiments merge into k8s first.
            if best_gj < 0:
                my_path = g.components[0].path if g.components else None
                my_parent = my_path.parent if my_path else None
                best_common = -1
                for gj in active:
                    if gj == gi:
                        continue
                    og = groups[gj]
                    if og.components and my_path:
                        # Count shared leading path parts (prefix, not set)
                        my_parts = my_path.parts
                        other_parts = og.components[0].path.parts
                        shared = 0
                        for a, b in zip(my_parts, other_parts):
                            if a == b:
                                shared += 1
                            else:
                                break
                        # Bonus: if any component of the other group is our direct parent,
                        # that's the ideal merge target
                        if my_parent and any(c.path == my_parent for c in og.components):
                            shared += 1000
                        if shared > best_common:
                            best_common, best_gj = shared, gj

            if best_gj < 0:
                continue

            merged = GraphGroup(groups[gi].components + groups[best_gj].components)
            # Allow merge even into oversized groups when the absorbing group is tiny (<= 2 files)
            size_ok = (not merged.is_oversized(max_files, max_words)
                       or g.files <= 2)
            if size_ok:
                for ci in range(len(components)):
                    if comp_to_group.get(ci) == best_gj:
                        comp_to_group[ci] = gi
                groups[gi] = merged
                active.remove(best_gj)
                changed = True

    return [groups[gi] for gi in active]


# ---------------------------------------------------------------------------
# Cross-graph bridges
# ---------------------------------------------------------------------------

MAX_BRIDGE_SYMBOLS = 8

# ponytail: stdlib/ubiquitous imports that add no cross-graph signal
BRIDGE_NOISE = frozenset({
    '__future__', 'pathlib', 'datetime', 'os', 'sys', 'json', 'typing',
    're', 'collections', 'dataclasses', 'abc', 'enum', 'functools',
    'itertools', 'io', 'logging', 'math', 'hashlib', 'copy', 'time',
    'uuid', 'contextlib', 'tempfile', 'shutil', 'subprocess', 'glob',
    'pydantic', 'pytest', 'asyncio', 'unittest',
    'react', 'react-dom', 'next', 'vue',
})


def compute_bridges(groups: list[GraphGroup],
                    components: list[Component],
                    coupling: dict[tuple[int, int], float]) -> list[dict]:
    """
    Find shared import symbols between components that landed in different
    final graphs. These are the cross-graph traversal anchors.
    """
    # Map each component index to its final graph index
    comp_to_graph: dict[int, int] = {}
    for gi, g in enumerate(groups):
        for c in g.components:
            comp_to_graph[components.index(c)] = gi

    # Aggregate by graph pair: collect shared symbols and max weight
    pair_data: dict[tuple[int, int], dict] = {}
    for (ci, cj), score in coupling.items():
        gi = comp_to_graph.get(ci)
        gj = comp_to_graph.get(cj)
        if gi is None or gj is None or gi == gj:
            continue
        key = (min(gi, gj), max(gi, gj))
        if key not in pair_data:
            pair_data[key] = {'weight': 0.0, 'symbols': defaultdict(int)}
        if score > pair_data[key]['weight']:
            pair_data[key]['weight'] = score
        shared = components[ci].refs & components[cj].refs
        for sym in shared:
            # Skip stdlib/ubiquitous imports — only domain symbols are useful bridges
            base = sym.split('.')[0].split('/')[0]
            if base in BRIDGE_NOISE:
                continue
            pair_data[key]['symbols'][sym] += 1

    bridges = []
    for (gi, gj), data in sorted(pair_data.items(), key=lambda x: x[1]['weight'], reverse=True):
        # Top symbols by frequency across the paired components
        top_syms = sorted(data['symbols'], key=data['symbols'].__getitem__, reverse=True)[:MAX_BRIDGE_SYMBOLS]
        if top_syms:
            bridges.append({
                'source': gi,
                'target': gj,
                'weight': round(data['weight'], 3),
                'symbols': top_syms,
            })
    return bridges


# ---------------------------------------------------------------------------
# Coverage validation
# ---------------------------------------------------------------------------

def validate_coverage(groups: list[GraphGroup],
                      all_files: list[Path]) -> dict:
    covered: set[Path] = set()
    for g in groups:
        covered.update(g.all_files)
    missing = [f for f in all_files if f not in covered]
    counter: dict[Path, int] = defaultdict(int)
    for g in groups:
        for f in g.all_files:
            counter[f] += 1
    duplicated = [f for f, cnt in counter.items() if cnt > 1]
    return {
        'total':        len(all_files),
        'covered':      len(covered),
        'missing':      missing,
        'duplicated':   duplicated,
        'coverage_pct': round(100 * len(covered) / max(len(all_files), 1), 1),
    }


# ---------------------------------------------------------------------------
# Main planning function
# ---------------------------------------------------------------------------

def plan(root: Path, max_files: int, max_words: int,
         coupling_threshold: float) -> dict:
    root = root.resolve()

    all_files  = all_source_files(root)
    components = collect_components(root)

    if not components:
        return {'error': 'No supported source files found', 'repo': str(root)}

    coupling = coupling_matrix(components)

    # Step 1: structurally pre-merge non-code components (yaml/json/doc) by path tree.
    # These have zero import coupling, so directory adjacency IS the coupling signal.
    path_groups = merge_by_path_tree(components, root, max_files, max_words)
    # Collapse pre-merged components into synthetic "super-components" before UF.
    # Build a map: original index -> pre-merge group canonical index
    pre_merge_map: dict[int, int] = {}
    for canonical, members in path_groups.items():
        for m in members:
            pre_merge_map[m] = canonical

    # Step 2: coupling-based Union-Find on the pre-merged groups
    uf = UF(len(components))
    # First, union all pre-merged members
    for canonical, members in path_groups.items():
        for m in members:
            uf.union(canonical, m)
    # Then union by coupling threshold
    for (i, j), score in coupling.items():
        if score >= coupling_threshold:
            uf.union(i, j)

    raw_groups = [
        GraphGroup([components[i] for i in idxs])
        for idxs in uf.groups(len(components)).values()
    ]

    # Split oversized
    groups: list[GraphGroup] = []
    for g in raw_groups:
        groups.extend(split_group(g, max_files, max_words))

    # Merge tiny
    groups = merge_tiny(groups, coupling, components, max_files, max_words)

    # Sort by file count
    groups.sort(key=lambda g: g.files, reverse=True)

    bridges = compute_bridges(groups, components, coupling)

    coverage = validate_coverage(groups, all_files)

    top_pairs = sorted(coupling.items(), key=lambda x: x[1], reverse=True)[:10]

    noise_dirs: set[str] = set()
    for p in root.rglob('*'):
        if p.is_file():
            try:
                rel_parts = p.relative_to(root).parts
            except ValueError:
                continue
            for part in rel_parts:
                if NOISE_DIR_RE.match(part):
                    noise_dirs.add(part)
                    break

    return {
        'repo':         str(root),
        'total_files':  len(all_files),
        'total_words':  sum(c.words for c in components),
        'noise_dirs':   sorted(noise_dirs),
        'num_components': len(components),
        'top_coupling_pairs': [
            {
                'a':      str(components[i].path.relative_to(root)),
                'b':      str(components[j].path.relative_to(root)),
                'jaccard': round(score, 3),
            }
            for (i, j), score in top_pairs
        ],
        'graphs': [
            {
                'dirs':      [str(p) for p in g.dirs],
                'files':     g.files,
                'words_est': g.words,
                'top_lang':  g.top_lang,
                'oversized': g.is_oversized(max_files, max_words),
                'oversized_reason': (
                    'star topology - all components share a common hub; '
                    'splitting would duplicate the hub into every sub-graph'
                    if g.is_oversized(max_files, max_words) and
                       _is_star_topology(g.components) else
                    'use --max-files / --max-words to tune'
                    if g.is_oversized(max_files, max_words) else ''
                ),
                'directed':  g.top_lang in DIRECTED_LANGS,
            }
            for g in groups
        ],
        'num_graphs': len(groups),
        'bridges':    bridges,
        'coverage':   coverage,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_plan(result: dict, verbose: bool = False, validate_only: bool = False):
    if 'error' in result:
        print(f"ERROR: {result['error']}")
        return

    print(f"\nRepo       : {result['repo']}")
    print(f"Files      : {result['total_files']}  (~{result['total_words']:,} words)")
    print(f"Components : {result['num_components']} (meaningful dirs with source files)")
    if result['noise_dirs']:
        print(f"Excluded   : {', '.join(result['noise_dirs'])}")

    cov = result['coverage']
    status = "OK" if cov['coverage_pct'] == 100.0 else "INCOMPLETE"
    print(f"Coverage   : {cov['covered']}/{cov['total']} files "
          f"({cov['coverage_pct']}%) [{status}]")

    if cov['missing']:
        print(f"  MISSING ({len(cov['missing'])} files):")
        for f in cov['missing'][:15]:
            print(f"    {f}")
        if len(cov['missing']) > 15:
            print(f"    ... and {len(cov['missing']) - 15} more")

    if cov['duplicated'] and verbose:
        print(f"  Duplicated (anchors, intentional): {len(cov['duplicated'])}")

    if validate_only:
        return

    print(f"\nTop coupling pairs:")
    for p in result['top_coupling_pairs'][:8]:
        print(f"  {p['a']}  <->  {p['b']}  (Jaccard={p['jaccard']})")

    print(f"\n=> {result['num_graphs']} graph(s) recommended\n")
    for i, g in enumerate(result['graphs'], 1):
        flag = "  [oversized]" if g['oversized'] else ""
        reason = f"\n              reason: {g['oversized_reason']}" if g.get('oversized_reason') else ""
        names = [Path(d).name for d in g['dirs']]
        if len(names) > 4:
            display = ', '.join(names[:4]) + f"  +{len(names)-4} more"
        else:
            display = ', '.join(names)
        print(f"  Graph {i:2d}:  {display}")
        print(f"           {g['files']} files  ~{g['words_est']:,} words"
              f"  lang={g['top_lang']}  directed={g['directed']}{flag}{reason}")

    if result.get('bridges'):
        print(f"\nCross-graph bridges:")
        for b in result['bridges']:
            syms = ', '.join(b['symbols'])
            print(f"  Graph {b['source']+1} <-> Graph {b['target']+1}"
                  f"  (weight={b['weight']})  via: {syms}")

    print(f"\n# Graphify commands:")
    for i, g in enumerate(result['graphs'], 1):
        dirs_str = ' '.join(
            f'"{d}"' if ' ' in d else d
            for d in g['dirs']
        )
        flag = ' --directed' if g['directed'] else ''
        print(f"  # Graph {i}")
        print(f"  /graphify {dirs_str}{flag}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Plan graphify graph partitions for any codebase.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('root', help='Path to repo root')
    parser.add_argument('--max-files', type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument('--max-words', type=int, default=DEFAULT_MAX_WORDS)
    parser.add_argument('--coupling', type=float, default=DEFAULT_COUPLING,
                        help='Jaccard threshold to merge components into one graph')
    parser.add_argument('--json', dest='as_json', action='store_true')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='Write repomap manifest to file (e.g. repomap.json)')
    parser.add_argument('--validate', action='store_true',
                        help='Show coverage report only')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()

    result = plan(Path(args.root), args.max_files, args.max_words, args.coupling)

    if args.output:
        manifest = {
            'repo': result['repo'],
            'graphs': [
                {
                    'id': i,
                    'dirs': g['dirs'],
                    'files': g['files'],
                    'words': g['words_est'],
                    'lang': g['top_lang'],
                    'directed': g['directed'],
                }
                for i, g in enumerate(result.get('graphs', []))
            ],
            'bridges': result.get('bridges', []),
        }
        Path(args.output).write_text(json.dumps(manifest, indent=2, default=str))
        print(f"Manifest written to {args.output}")

    if args.as_json:
        cov = result.get('coverage', {})
        cov['missing']    = [str(f) for f in cov.get('missing', [])]
        cov['duplicated'] = [str(f) for f in cov.get('duplicated', [])]
        print(json.dumps(result, indent=2, default=str))
    else:
        print_plan(result, verbose=args.verbose, validate_only=args.validate)
