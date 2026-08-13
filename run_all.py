"""
run_all.py  --  End-to-end repo analysis: partition + AST graphs + manifest.

No AI/LLM needed. Runs entirely on static analysis (imports, AST, clustering).

Usage:
    python run_all.py <repo_path>
    python run_all.py <repo_path> --target ./my-graphs
    python run_all.py <repo_path> --target ~/graphs/project-name --coupling 0.20
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from repomap import (
    plan, DEFAULT_MAX_FILES, DEFAULT_MAX_WORDS, DEFAULT_COUPLING,
    LANG_MAP, is_noise_path,
)

CODE_EXTS = {
    '.py', '.pyi', '.ts', '.tsx', '.js', '.jsx', '.mjs',
    '.go', '.rs', '.java', '.cpp', '.cc', '.cxx', '.c',
    '.hpp', '.h', '.hxx', '.cs', '.rb', '.kt', '.kts',
    '.swift', '.scala', '.sh', '.bash', '.zsh', '.proto',
}


def collect_code_files(dirs: list[str], root: Path) -> list[Path]:
    """Collect code files from listed directories, respecting noise exclusions."""
    files = []
    seen = set()
    for d in dirs:
        p = Path(d)
        if p.is_file() and p.suffix.lower() in CODE_EXTS:
            if p not in seen:
                files.append(p)
                seen.add(p)
        elif p.is_dir():
            for f in sorted(p.rglob('*')):
                if not f.is_file() or f.suffix.lower() not in CODE_EXTS:
                    continue
                try:
                    rel = f.relative_to(root)
                except ValueError:
                    rel = f.relative_to(p)
                if is_noise_path(rel):
                    continue
                if f not in seen:
                    files.append(f)
                    seen.add(f)
    return files


def build_graph_for_group(group: dict, group_id: int, target_dir: Path,
                          repo_root: Path, directed: bool) -> dict | None:
    """Run AST extraction + clustering on a single graph group. Returns metadata or None."""
    try:
        from graphify.extract import extract, collect_files
        from graphify.build import build_from_json
        from graphify.cluster import cluster
        from graphify.export import to_json
    except ImportError:
        print("ERROR: graphify not installed. Install with: pip install graphifyy", file=sys.stderr)
        sys.exit(1)

    code_files = collect_code_files(group['dirs'], repo_root)
    if not code_files:
        return None

    graph_dir = target_dir / f"graph-{group_id}"
    graph_dir.mkdir(parents=True, exist_ok=True)

    # AST extraction
    extraction = extract(code_files, cache_root=graph_dir)
    if not extraction.get('nodes'):
        return None

    # Build networkx graph
    G = build_from_json(extraction, directed=directed)
    if G.number_of_nodes() == 0:
        return None

    # Cluster
    communities = cluster(G)

    # Export
    graph_path = str(graph_dir / "graph.json")
    to_json(G, communities, graph_path, force=True)

    # Save extraction for reference
    (graph_dir / "extraction.json").write_text(
        json.dumps(extraction, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8"
    )

    return {
        'id': group_id,
        'graph_path': graph_path,
        'nodes': G.number_of_nodes(),
        'edges': G.number_of_edges(),
        'communities': len(communities),
        'dirs': group['dirs'],
        'lang': group.get('top_lang', group.get('lang', 'unknown')),
        'directed': directed,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Run repomap partitioning + AST graph generation. No AI needed.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('root', help='Path to repo root')
    parser.add_argument('--target', '-t', type=str, default=None,
                        help='Output directory for graphs (default: <root>/repomap-out)')
    parser.add_argument('--max-files', type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument('--max-words', type=int, default=DEFAULT_MAX_WORDS)
    parser.add_argument('--coupling', type=float, default=DEFAULT_COUPLING,
                        help='Jaccard threshold to merge components into one graph')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    target_dir = Path(args.target).resolve() if args.target else root / 'repomap-out'
    target_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Plan
    print(f"[1/3] Analyzing {root} ...")
    result = plan(root, args.max_files, args.max_words, args.coupling)

    if 'error' in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)

    graphs = result.get('graphs', [])
    bridges = result.get('bridges', [])
    print(f"      {result['total_files']} files, {result['num_components']} components "
          f"-> {len(graphs)} graph(s), {len(bridges)} bridge(s)")

    # Step 2: Build AST graphs
    print(f"[2/3] Building AST graphs in {target_dir} ...")
    built = []
    for i, g in enumerate(graphs):
        directed = g.get('directed', False)
        meta = build_graph_for_group(g, i, target_dir, root, directed)
        if meta:
            built.append(meta)
            print(f"      graph-{i}: {meta['nodes']} nodes, {meta['edges']} edges, "
                  f"{meta['communities']} communities ({meta['lang']})")
        else:
            print(f"      graph-{i}: skipped (no code files or empty extraction)")

    # Step 3: Write manifest
    print(f"[3/3] Writing manifest ...")
    manifest = {
        'repo': str(root),
        'target': str(target_dir),
        'graphs': [
            {
                'id': m['id'],
                'graph_path': m['graph_path'],
                'nodes': m['nodes'],
                'edges': m['edges'],
                'communities': m['communities'],
                'dirs': m['dirs'],
                'lang': m['lang'],
                'directed': m['directed'],
            }
            for m in built
        ],
        'bridges': bridges,
    }

    manifest_path = target_dir / "repomap.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(f"\nDone. {len(built)} graph(s) built.")
    print(f"  Manifest : {manifest_path}")
    print(f"  Graphs   : {target_dir}/graph-*/graph.json")
    if bridges:
        print(f"  Bridges  : {len(bridges)} cross-graph links in manifest")


if __name__ == '__main__':
    main()
