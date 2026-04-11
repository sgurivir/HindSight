#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict, deque
import re, json, argparse, os

class ReasoningOrderGenerator:
    INCLUDE_PATTERNS = [
        (".c",     re.compile(r'#include\s+"([^"]+)"')),
        (".cc",    re.compile(r'#include\s+"([^"]+)"')),
        (".cpp",   re.compile(r'#include\s+"([^"]+)"')),
        (".m",     re.compile(r'#import\s+"([^"]+)"')),
        (".mm",    re.compile(r'#import\s+"([^"]+)"')),
        (".swift", re.compile(r'\bimport\s+([A-Za-z0-9_]+)')),
        (".py",    re.compile(r'^\s*from\s+([\w\.]+)|^\s*import\s+([\w\.]+)', re.M)),
        (".ts",    re.compile(r'import.*from\s+[\'"]([^\'"]+)[\'"]')),
        (".js",    re.compile(r'import.*from\s+[\'"]([^\'"]+)[\'"]')),
    ]
    DEFAULT_EXTS = {".c",".cc",".cpp",".m",".mm",".swift"}

    @staticmethod
    def _is_ignored(p: Path, ignore_dirs: set[str]) -> bool:
        return any(part in ignore_dirs for part in p.parts)

    @staticmethod
    def _iter_files(root: Path, exts: set[str], ignore_dirs: set[str]):
        """Walk root, pruning ignored directories."""
        for dirpath, dirnames, filenames in os.walk(root):
            # prune dirs in-place to avoid descending into them
            dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
            for fname in filenames:
                p = Path(dirpath) / fname
                if p.suffix.lower() in exts:
                    yield p.resolve()

    @staticmethod
    def deps_for_file(p: Path, ignore_dirs: set[str]) -> set[Path]:
        text = p.read_text(errors="ignore")
        found = set()
        for ext, pat in ReasoningOrderGenerator.INCLUDE_PATTERNS:
            if p.suffix.lower() == ext:
                for m in pat.finditer(text):
                    mod = (m.group(1) or m.group(2) or "").strip()
                    if not mod:
                        continue
                    cand = p.parent / mod
                    if not cand.suffix and p.suffix:
                        cand = cand.with_suffix(p.suffix)
                    if cand.exists():
                        cand = cand.resolve()
                        if not ReasoningOrderGenerator._is_ignored(cand, ignore_dirs):
                            found.add(cand)
        return found

    @staticmethod
    def build_graph(root: Path, exts: set[str] = None, ignore_dirs: set[str] = None):
        if exts is None:
            exts = ReasoningOrderGenerator.DEFAULT_EXTS
        if ignore_dirs is None:
            ignore_dirs = set()
        files = list(ReasoningOrderGenerator._iter_files(root, exts, ignore_dirs))
        G = {f: set() for f in files}
        for f in files:
            for d in ReasoningOrderGenerator.deps_for_file(f, ignore_dirs):
                if d in G:
                    G[f].add(d)
        return G

    @staticmethod
    def scc_tarjan(G):
        index = 0; stack = []; onstk = set(); idx = {}; low = {}; comps = []
        def strong(v):
            nonlocal index
            idx[v] = low[v] = index; index += 1; stack.append(v); onstk.add(v)
            for w in G[v]:
                if w not in idx:
                    strong(w); low[v] = min(low[v], low[w])
                elif w in onstk:
                    low[v] = min(low[v], idx[w])
            if low[v] == idx[v]:
                comp = []
                while True:
                    w = stack.pop(); onstk.remove(w); comp.append(w)
                    if w == v: break
                comps.append(comp)
        for v in G:
            if v not in idx: strong(v)
        return comps

    @staticmethod
    def topo_of_components(G, comps):
        comp_id = {n:i for i,comp in enumerate(comps) for n in comp}
        dag = defaultdict(set); indeg = defaultdict(int)
        for u, outs in G.items():
            cu = comp_id[u]
            for v in outs:
                cv = comp_id[v]
                if cu != cv and cv not in dag[cu]:
                    dag[cu].add(cv); indeg[cv] += 1
        q = deque([i for i in range(len(comps)) if indeg[i] == 0])
        order = []
        while q:
            c = q.popleft(); order.append(c)
            for v in dag[c]:
                indeg[v] -= 1
                if indeg[v] == 0: q.append(v)
        return [comps[i] for i in order]

    @staticmethod
    def ordered_files(root: Path, ignore_dirs: set[str] = None):
        G = ReasoningOrderGenerator.build_graph(root, ignore_dirs=ignore_dirs or set())
        comps = ReasoningOrderGenerator.scc_tarjan(G)
        ordered_groups = ReasoningOrderGenerator.topo_of_components(G, comps)
        return [f for group in ordered_groups for f in sorted(group)]

    @staticmethod
    def ordered_dir_from_files_list(files: list[Path], absolute: bool, repo: Path):
        seen = set()
        dir_list = []
        for f in files:
            d = f.parent
            key = d if absolute else d.relative_to(repo)
            if key not in seen:
                seen.add(key)
                dir_list.append(str(key))

        return dir_list

def main():
    ap = argparse.ArgumentParser(description="Produce dependency-aware ordered list of files (JSON).")
    ap.add_argument("repo", type=Path, help="Path to repository root")
    ap.add_argument("-o","--out", type=Path, default=Path("ordered_files.json"),
                    help="Output JSON file (default: ordered_files.json)")
    ap.add_argument("--absolute", action="store_true",
                    help="Emit absolute paths instead of repo-relative")
    args = ap.parse_args()

    repo = args.repo.resolve()
    ignore_set = {'Tools', 'Tests', 'Test', 'External', 'protobufs', 'bin', 'scripts', 'ProtocolBuffers', 'ProtobufDefs',
                  'NewUIKitTests' 'CarPlayArtwork', 'UIKitMacHelper', 'Artwork', 'Documents'}
    files = ReasoningOrderGenerator.ordered_files(repo, ignore_dirs=ignore_set)

    # Write out order of files
    out_list = [str(p if args.absolute else p.relative_to(repo)) for p in files]

    # Directory order (unique, in order of first file appearance)
    dir_list = ReasoningOrderGenerator.ordered_dir_from_files_list(files, args.absolute, args.repo)

    payload = {
        "reasoning_order_files": out_list,
        "reasoning_order_dirs": dir_list
    }

    args.out.write_text(json.dumps(payload, indent=2))
    print(f"[+] Wrote {args.out} ({len(out_list)} files)")

if __name__ == "__main__":
    main()