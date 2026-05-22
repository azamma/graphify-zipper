#!/usr/bin/env python3
"""graphify-zipper: query + extract + compress graphify-out archives.

Subcommands:
  query <question>              BFS traversal from best-matching nodes
  explain <node>                node + outgoing/incoming edges (preferred)
  path <a> <b>                  shortest path between two nodes (BFS)
  providers                     list provider/* source files in graph
  find <terms...>               rank nodes by label/source match (last resort)
  extract [--dir D] [--force]   unzip archive (auto-detects .zip / .7z)
  compress [--dir D] [--method] build archive (zip=ZIP_LZMA, 7z=LZMA2)

Common flags:
  --zip <path>            archive path (default: graphify-out.zip)
  --source <zip|dir>      read from archive (default) or graphify-out/ dir
  --method <zip|7z|ppmd|zpaq>  zip=stdlib ZIP_LZMA, 7z=py7zr LZMA2, ppmd=PPMd, zpaq=zpaq -m5
  --json                  emit JSON
  --limit N               cap find results (default: 15)
  --depth N               BFS depth for query (default: 2)

Notes:
  - Query reads archive directly via zipfile / py7zr — no extract step.
  - ZIP_LZMA: stdlib, ~10% smaller than BZip2.
  - 7z LZMA2: requires py7zr, ~45% smaller than BZip2.
  - PPMd: requires py7zr, ~62% smaller than BZip2 (best for text/JSON).
  - zpaq -m5: requires zpaq binary, beats PPMd on dense backend repos
    (~15-25% smaller) but ~14x slower. Opt-in for heavy backend archives.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import deque
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

# ---------- constants ----------

DEFAULT_ZIP = "graphify-out.zip"
DEFAULT_DIR = "graphify-out"
DEFAULT_INNER = "graphify-out/graph.json"

MAX_QUERY_NODES = 50
MAX_QUERY_EDGES = 30
MAX_EXPLAIN_EDGES = 30
QUERY_START_TOP_K = 3
LABEL_TRUNC = 60
ID_TRUNC = 50


class Node(TypedDict, total=False):
    id: str
    label: str | None
    source_file: str | None
    source_location: str | None
    file_type: str | None


class Edge(TypedDict, total=False):
    source: str
    target: str
    relation: str | None
    confidence: str | None
    confidence_score: float | None
    source_file: str | None


# ---------- errors ----------

class ZipperError(Exception):
    """Domain error with user-facing message; exits 1 at boundary."""


# ---------- archive reading ----------

_PY7ZR_AUTO_INSTALL_ATTEMPTED = False


def _ensure_py7zr():
    """Import py7zr; on first ImportError, try to auto-install. Returns module or None."""
    global _PY7ZR_AUTO_INSTALL_ATTEMPTED
    try:
        import py7zr
        return py7zr
    except ImportError:
        pass
    if _PY7ZR_AUTO_INSTALL_ATTEMPTED:
        return None
    _PY7ZR_AUTO_INSTALL_ATTEMPTED = True
    print("py7zr not found, attempting auto-install...", file=sys.stderr)
    # Try install strategies in order of preference
    attempts: list[list[str]] = [
        [sys.executable, "-m", "pip", "install", "-q", "py7zr"],
        [sys.executable, "-m", "pip", "install", "-q", "--break-system-packages", "py7zr"],
    ]
    uv = shutil.which("uv")
    if uv:
        attempts.append([uv, "pip", "install", "--system", "--break-system-packages", "py7zr"])
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                try:
                    import py7zr  # type: ignore
                    print(f"py7zr installed via: {' '.join(cmd[:3])}", file=sys.stderr)
                    return py7zr
                except ImportError:
                    continue
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    print("py7zr auto-install failed. Install manually: pip install py7zr", file=sys.stderr)
    return None


def _resolve_archive(path: str | Path) -> Path:
    """Return existing archive path. If missing, try .7z / .zpaq sibling."""
    p = Path(path)
    if p.exists():
        return p
    for ext in (".7z", ".zpaq", ".zip"):
        alt = p.with_suffix(ext)
        if alt.exists():
            return alt
    raise ZipperError(f"{p} not found")


def _zpaq_bin() -> str | None:
    """Locate zpaq binary."""
    return shutil.which("zpaq")


@contextmanager
def _open_inner(archive: Path, inner: str) -> Iterator[bytes]:
    """Yield raw bytes of `inner` inside archive (zip, 7z, or zpaq)."""
    if archive.suffix == ".7z":
        py7zr = _ensure_py7zr()
        if py7zr is None:
            raise ZipperError("py7zr required to read .7z files (auto-install failed)")
        with py7zr.SevenZipFile(archive, "r") as z7, tempfile.TemporaryDirectory() as tmp:
            z7.extract(path=tmp, targets=[inner])
            target = Path(tmp) / inner
            if not target.exists():
                raise ZipperError(f"{inner} not in {archive}")
            yield target.read_bytes()
        return
    if archive.suffix == ".zpaq":
        zpaq = _zpaq_bin()
        if zpaq is None:
            raise ZipperError("zpaq binary required to read .zpaq files (install via apt)")
        with tempfile.TemporaryDirectory() as tmp:
            # zpaq stores paths relative; running from tmp cwd materializes
            # `inner` at tmp/<inner>. No reliable -to syntax for single file.
            r = subprocess.run(
                [zpaq, "x", str(archive.resolve()), inner, "-force"],
                cwd=tmp, capture_output=True, text=True, timeout=300,
            )
            if r.returncode != 0:
                raise ZipperError(f"zpaq extract failed: {r.stderr[-200:]}")
            target = Path(tmp) / inner
            if not target.exists():
                raise ZipperError(f"{inner} not in {archive}")
            yield target.read_bytes()
        return
    with zipfile.ZipFile(archive) as z:
        try:
            yield z.read(inner)
        except KeyError as e:
            raise ZipperError(f"{inner} not in {archive}") from e


def load_graph(zip_path: str, *, inner: str = DEFAULT_INNER, source: str = "zip") -> dict:
    if source == "dir":
        graph_file = Path(zip_path) / "graph.json"
        if not graph_file.exists():
            raise ZipperError(f"{graph_file} not found")
        return json.loads(graph_file.read_bytes())
    archive = _resolve_archive(zip_path)
    with _open_inner(archive, inner) as raw:
        return json.loads(raw)


def _extract_archive(archive: Path, out_dir: Path) -> None:
    if archive.suffix == ".7z":
        py7zr = _ensure_py7zr()
        if py7zr is None:
            raise ZipperError("py7zr required to extract .7z files (auto-install failed)")
        with py7zr.SevenZipFile(archive, "r") as z7:
            z7.extractall(out_dir)
        return
    if archive.suffix == ".zpaq":
        zpaq = _zpaq_bin()
        if zpaq is None:
            raise ZipperError("zpaq binary required to extract .zpaq files (install via apt)")
        out_dir.mkdir(parents=True, exist_ok=True)
        # zpaq writes paths relative to cwd; cd into out_dir for predictable layout.
        r = subprocess.run(
            [zpaq, "x", str(archive.resolve()), "-force"],
            cwd=out_dir, capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            raise ZipperError(f"zpaq extract failed: {r.stderr[-300:]}")
        return
    with zipfile.ZipFile(archive) as z:
        z.extractall(out_dir)


# ---------- graph indexing ----------

def index(data: dict) -> tuple[dict[str, Node], dict[str, list[tuple[str, Edge]]], dict[str, list[tuple[str, Edge]]], list[Edge]]:
    nodes: dict[str, Node] = {n["id"]: n for n in data.get("nodes", [])}
    edges: list[Edge] = data.get("links", data.get("edges", []))
    adj: dict[str, list[tuple[str, Edge]]] = {}
    radj: dict[str, list[tuple[str, Edge]]] = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s is None or t is None:
            continue
        adj.setdefault(s, []).append((t, e))
        radj.setdefault(t, []).append((s, e))
    return nodes, adj, radj, edges


def score(node: Node, terms: list[str]) -> int:
    label = (node.get("label") or "").lower()
    src = (node.get("source_file") or "").lower()
    return sum(3 for t in terms if t in label) + sum(1 for t in terms if t in src)


def _rank_nodes(nodes: dict[str, Node], terms: list[str]) -> list[tuple[int, str]]:
    """Return [(score, node_id), ...] sorted by score desc; only positive scores."""
    scored = [(s, nid) for nid, n in nodes.items() if (s := score(n, terms)) > 0]
    scored.sort(reverse=True)
    return scored


def _match_one(nodes: dict[str, Node], term: str) -> str | None:
    ranked = _rank_nodes(nodes, [term.lower()])
    return ranked[0][1] if ranked else None


def _neighbors(adj: dict[str, list[tuple[str, Edge]]], radj: dict[str, list[tuple[str, Edge]]], nid: str) -> list[tuple[str, Edge]]:
    return adj.get(nid, []) + radj.get(nid, [])


# ---------- formatting helpers ----------

def _safe(value: Any, fallback: str = "?") -> str:
    """Coerce None/empty to fallback for display."""
    if value is None or value == "":
        return fallback
    return str(value)


def _node_label(node: Node | None, nid: str = "") -> str:
    if node is None:
        return _safe(nid)
    return node.get("label") or nid or "?"


# ---------- query commands ----------

def cmd_find(args, data: dict) -> None:
    nodes, *_ = index(data)
    terms = [t.lower() for t in args.terms]
    ranked = _rank_nodes(nodes, terms)
    out = [
        {
            "score": s,
            "id": nid,
            "label": nodes[nid].get("label"),
            "source_file": nodes[nid].get("source_file"),
            "source_location": nodes[nid].get("source_location"),
        }
        for s, nid in ranked[: args.limit]
    ]
    emit(out, args)


def cmd_explain(args, data: dict) -> None:
    nodes, adj, radj, _ = index(data)
    nid = _match_one(nodes, args.node)
    if nid is None:
        raise ZipperError(f"no node matches {args.node!r}")
    n = nodes[nid]
    outgoing = [
        {
            "target": _node_label(nodes.get(t), t),
            "relation": e.get("relation"),
            "confidence": e.get("confidence"),
            "source_file": nodes.get(t, {}).get("source_file"),
        }
        for t, e in adj.get(nid, [])
    ]
    incoming = [
        {
            "source": _node_label(nodes.get(s), s),
            "relation": e.get("relation"),
            "confidence": e.get("confidence"),
            "source_file": nodes.get(s, {}).get("source_file"),
        }
        for s, e in radj.get(nid, [])
    ]
    emit({
        "id": nid,
        "label": n.get("label"),
        "source_file": n.get("source_file"),
        "source_location": n.get("source_location"),
        "file_type": n.get("file_type"),
        "degree": len(outgoing) + len(incoming),
        "outgoing": outgoing,
        "incoming": incoming,
    }, args)


def _bfs_collect(start_ids: Iterable[str], nodes: dict[str, Node], adj, radj, depth: int) -> tuple[list[Node], list[Edge]]:
    """BFS from each start to `depth`. Per-start visited so multiple starts
    explore independently; results deduped globally by node id / edge identity."""
    seen_nodes: set[str] = set()
    seen_edges: set[int] = set()
    result_nodes: list[Node] = []
    result_edges: list[Edge] = []
    for start in start_ids:
        visited: set[str] = {start}
        q: deque[tuple[str, int]] = deque([(start, 0)])
        while q:
            nid, d = q.popleft()
            if nid not in seen_nodes:
                seen_nodes.add(nid)
                result_nodes.append(nodes[nid])
            if d >= depth:
                continue
            for v, e in _neighbors(adj, radj, nid):
                eid = id(e)
                if eid not in seen_edges:
                    seen_edges.add(eid)
                    result_edges.append(e)
                if v not in visited:
                    visited.add(v)
                    q.append((v, d + 1))
    return result_nodes, result_edges


def cmd_query(args, data: dict) -> None:
    nodes, adj, radj, _ = index(data)
    terms = [t.lower() for t in args.question.split()]
    ranked = _rank_nodes(nodes, terms)
    if not ranked:
        raise ZipperError(f"no node matches {args.question!r}")
    start_ids = [nid for _, nid in ranked[:QUERY_START_TOP_K]]
    result_nodes, result_edges = _bfs_collect(start_ids, nodes, adj, radj, args.depth)
    emit({
        "start_nodes": [_node_label(nodes.get(s), s) for s in start_ids],
        "nodes_found": len(result_nodes),
        "edges_found": len(result_edges),
        "nodes": [
            {
                "id": n["id"],
                "label": n.get("label"),
                "source_file": n.get("source_file"),
                "source_location": n.get("source_location"),
                "community": n.get("community"),
            }
            for n in result_nodes[:MAX_QUERY_NODES]
        ],
        "edges": [
            {
                "source": e.get("source"),
                "target": e.get("target"),
                "relation": e.get("relation"),
                "confidence": e.get("confidence"),
                "confidence_score": e.get("confidence_score"),
                "source_location": e.get("source_location"),
            }
            for e in result_edges[:MAX_QUERY_EDGES]
        ],
    }, args)


def cmd_path(args, data: dict) -> None:
    nodes, adj, radj, _ = index(data)
    src = _match_one(nodes, args.a)
    tgt = _match_one(nodes, args.b)
    if not src or not tgt:
        raise ZipperError(f"could not match nodes: a={args.a!r} -> {src}, b={args.b!r} -> {tgt}")

    parents: dict[str, tuple[str, Edge] | None] = {src: None}
    q: deque[str] = deque([src])
    while q:
        u = q.popleft()
        if u == tgt:
            break
        for v, e in _neighbors(adj, radj, u):
            if v not in parents:
                parents[v] = (u, e)
                q.append(v)
    if tgt not in parents:
        raise ZipperError(f"no path between {args.a!r} and {args.b!r}")

    chain: list[str] = []
    cur: str | None = tgt
    while cur is not None:
        chain.append(cur)
        prev = parents[cur]
        cur = prev[0] if prev else None
    chain.reverse()

    hops: list[dict[str, Any]] = []
    for i, nid in enumerate(chain):
        entry: dict[str, Any] = {
            "label": _node_label(nodes.get(nid), nid),
            "source_file": nodes.get(nid, {}).get("source_file"),
        }
        if i < len(chain) - 1:
            nxt = chain[i + 1]
            edge = next((e for v, e in _neighbors(adj, radj, nid) if v == nxt), {})
            entry["edge_to_next"] = {
                "relation": edge.get("relation"),
                "confidence": edge.get("confidence"),
            }
        hops.append(entry)
    emit({"hops": len(chain) - 1, "path": hops}, args)


def cmd_providers(args, data: dict) -> None:
    nodes, *_ = index(data)
    seen: set[str] = set()
    for n in nodes.values():
        sf = n.get("source_file") or ""
        if "providers/" in sf and "/test" not in sf and "tests/" not in sf:
            seen.add(sf)
    emit(sorted(seen), args)


# ---------- lifecycle commands ----------

def cmd_extract(args, _data=None) -> None:
    archive = _resolve_archive(args.zip)
    out_dir = Path(args.dir)
    target = out_dir / DEFAULT_DIR
    if target.exists() and not args.force:
        raise ZipperError(
            f"{target} already exists. Previous rebuild not recompressed?\n"
            f"Re-run with --force to overwrite, or inspect/preserve first."
        )
    if target.exists():
        shutil.rmtree(target)
    _extract_archive(archive, out_dir)
    print(f"extracted {archive} -> {target}")


# Files derived from graph.json — safe to skip, regenerable via graphify CLI.
DERIVED_NAMES = frozenset({"graph.html", "GRAPH_REPORT.md"})
DERIVED_DIRS = frozenset({"obsidian"})
DERIVED_SUFFIXES = frozenset({".svg", ".graphml"})


def _verify_7z(archive: Path) -> bool:
    """Test archive integrity. Prefers external `7z t` (more reliable than py7zr
    readback, which has known PPMd corruption on large archives).
    Falls back to py7zr testzip() if 7z CLI not on PATH.
    """
    sevenzip = shutil.which("7z") or shutil.which("7za")
    if sevenzip:
        try:
            r = subprocess.run([sevenzip, "t", str(archive)], capture_output=True, text=True, timeout=120)
            return r.returncode == 0 and "Everything is Ok" in r.stdout
        except (subprocess.TimeoutExpired, OSError):
            return False
    py7zr = _ensure_py7zr()
    if py7zr is None:
        return False
    try:
        with py7zr.SevenZipFile(archive, "r") as z:
            bad = z.testzip()
            return bad is None
    except Exception:
        return False


def _verify_zpaq(archive: Path) -> bool:
    """Test zpaq archive integrity via `zpaq l` (lists contents; non-zero exit on corruption)."""
    zpaq = _zpaq_bin()
    if zpaq is None:
        return False
    try:
        r = subprocess.run([zpaq, "l", str(archive)], capture_output=True, text=True, timeout=300)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _prepare_files(
    files: list[Path],
    src_dir: Path,
    *,
    skip_derived: bool,
    minify_json: bool,
) -> tuple[list[tuple[str, bytes]], dict[str, int]]:
    """Apply lean preprocessing: skip derived files, minify JSON.

    Returns prepared (arcname, bytes) pairs and counters for reporting.
    """
    prepared: list[tuple[str, bytes]] = []
    stats = {"skipped": 0, "minified": 0, "bytes_saved_minify": 0}
    for p in files:
        rel = p.relative_to(src_dir)
        if skip_derived:
            if rel.name in DERIVED_NAMES or p.suffix in DERIVED_SUFFIXES:
                stats["skipped"] += 1
                continue
            if rel.parts and rel.parts[0] in DERIVED_DIRS:
                stats["skipped"] += 1
                continue
        data = p.read_bytes()
        if minify_json and p.suffix == ".json":
            try:
                obj = json.loads(data)
                minified = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                if len(minified) < len(data):
                    stats["bytes_saved_minify"] += len(data) - len(minified)
                    stats["minified"] += 1
                    data = minified
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # keep original bytes
        arcname = str(p.relative_to(src_dir.parent))
        prepared.append((arcname, data))
    return prepared, stats


def cmd_compress(args, _data=None) -> None:
    src_dir = Path(args.dir)
    zip_path = Path(args.zip)
    if not src_dir.exists():
        raise ZipperError(f"source {src_dir} not found")
    if not (src_dir / "graph.json").exists():
        raise ZipperError(f"{src_dir}/graph.json missing - refusing to zip empty/stale dir")
    if zip_path.exists():
        zip_path.unlink()

    # Sort files: workaround for py7zr PPMd corruption with unsorted file order
    # on large archives. See bug report in handoff.md.
    files = sorted(p for p in src_dir.rglob("*") if p.is_file())

    def _lean_note(stats: dict) -> str:
        if not args.lean:
            return ""
        parts = [f"skipped {stats['skipped']} derived"]
        if stats["minified"] > 0:
            parts.append(f"minified {stats['minified']} JSON (~{stats['bytes_saved_minify']/1024:.0f} KB)")
        return " [lean: " + ", ".join(parts) + "]"

    def _try_7z(label: str, filters: list[dict], *, skip_derived: bool, minify_json: bool) -> bool:
        """Prepare + compress + integrity check. Returns True on success."""
        prepared, stats = _prepare_files(files, src_dir, skip_derived=skip_derived, minify_json=minify_json)
        with tempfile.TemporaryDirectory() as staging_root:
            staging = Path(staging_root)
            staged: list[tuple[Path, str]] = []
            for arcname, data in prepared:
                dest = staging / arcname
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                staged.append((dest, arcname))
            try:
                with py7zr.SevenZipFile(zip_path, "w", filters=filters) as z7:
                    for src_path, arcname in staged:
                        z7.write(src_path, arcname)
            except Exception as e:
                print(f"{label} failed: {e}; trying next codec")
                if zip_path.exists():
                    zip_path.unlink()
                return False
        if _verify_7z(zip_path):
            size = zip_path.stat().st_size
            note = _lean_note(stats) if (skip_derived or minify_json) else ""
            print(f"compressed {src_dir} -> {zip_path} ({size:,} bytes, {label}){note}")
            return True
        print(f"WARNING: {label} archive failed integrity check; trying next codec")
        zip_path.unlink()
        return False

    def _try_zpaq(label: str, *, skip_derived: bool, minify_json: bool) -> bool:
        """Compress lean files with zpaq -m5 + integrity check."""
        zpaq = _zpaq_bin()
        if zpaq is None:
            print(f"{label} skipped: zpaq binary not found (install: apt-get install zpaq)")
            return False
        prepared, stats = _prepare_files(files, src_dir, skip_derived=skip_derived, minify_json=minify_json)
        with tempfile.TemporaryDirectory() as staging_root:
            staging = Path(staging_root)
            arcnames: list[str] = []
            for arcname, data in prepared:
                dest = staging / arcname
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                arcnames.append(arcname)
            try:
                r = subprocess.run(
                    [zpaq, "a", str(zip_path.resolve()), *sorted(arcnames), "-m5"],
                    cwd=staging, capture_output=True, text=True, timeout=1800,
                )
                if r.returncode != 0:
                    print(f"{label} failed: {r.stderr[-200:]}; trying next codec")
                    if zip_path.exists():
                        zip_path.unlink()
                    return False
            except subprocess.TimeoutExpired:
                print(f"{label} timed out; trying next codec")
                if zip_path.exists():
                    zip_path.unlink()
                return False
        if _verify_zpaq(zip_path):
            size = zip_path.stat().st_size
            note = _lean_note(stats) if (skip_derived or minify_json) else ""
            print(f"compressed {src_dir} -> {zip_path} ({size:,} bytes, {label}){note}")
            return True
        print(f"WARNING: {label} archive failed integrity check; trying next codec")
        zip_path.unlink()
        return False

    if args.method == "zpaq":
        if _try_zpaq("zpaq -m5" + (" + lean" if args.lean else ""), skip_derived=args.lean, minify_json=args.lean):
            return
        print("zpaq compression failed, falling back to PPMd")
        args.method = "ppmd"  # cascade to py7zr path

    if args.method in ("7z", "ppmd"):
        py7zr = _ensure_py7zr()
        if py7zr is None:
            print("py7zr not available, falling back to ZIP_LZMA")
        else:
            PPMD = [{"id": py7zr.FILTER_PPMD, "order": 32, "mem": 29}]
            LZMA2 = [{"id": py7zr.FILTER_LZMA2, "preset": 9}]
            # Attempt chain. Each tuple: (label, filters, skip_derived, minify_json).
            # PPMd + JSON minify can corrupt archives (content-dependent), so we
            # retry PPMd without minify before falling back to LZMA2.
            attempts: list[tuple[str, list[dict], bool, bool]] = []
            if args.method == "ppmd":
                if args.lean:
                    attempts.append(("7z PPMd + lean",            PPMD, True,  True))
                    attempts.append(("7z PPMd + skip-derived",    PPMD, True,  False))
                else:
                    attempts.append(("7z PPMd",                   PPMD, False, False))
            if args.lean:
                attempts.append(("7z LZMA2 + lean",               LZMA2, True, True))
            else:
                attempts.append(("7z LZMA2",                      LZMA2, False, False))
            for label, filters, skip_d, minify in attempts:
                if _try_7z(label, filters, skip_derived=skip_d, minify_json=minify):
                    return
            print("all py7zr methods failed/corrupted, falling back to ZIP_LZMA")

    # ZIP_LZMA fallback. Apply lean prep here too.
    prepared, stats = _prepare_files(files, src_dir, skip_derived=args.lean, minify_json=args.lean)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_LZMA, compresslevel=9) as z:
        for arcname, data in prepared:
            z.writestr(arcname, data)
    size = zip_path.stat().st_size
    print(f"compressed {src_dir} -> {zip_path} ({size:,} bytes, LZMA level 9){_lean_note(stats)}")


# ---------- output ----------

def emit(obj: Any, args) -> None:
    if args.json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
        return
    if isinstance(obj, list):
        for item in obj:
            print(_fmt_row(item) if isinstance(item, dict) else item)
        return
    if not isinstance(obj, dict):
        print(obj)
        return
    if "start_nodes" in obj:
        _print_query(obj)
    elif "path" in obj and "hops" in obj:
        _print_path(obj)
    else:
        _print_node(obj)


def _print_query(obj: dict) -> None:
    print(f"Query: {obj.get('start_nodes')}")
    print(f"  Nodes: {obj.get('nodes_found')}  Edges: {obj.get('edges_found')}")
    for n in obj.get("nodes", [])[:15]:
        label = n.get("label") or _safe(n.get("id"))[:ID_TRUNC]
        loc = n.get("source_location")
        loc_str = f":{loc}" if loc else ""
        community = n.get("community")
        comm_str = f" [c={community}]" if community is not None else ""
        print(f"  {label:50s}  {_safe(n.get('source_file'), '')}{loc_str}{comm_str}")
    edges = obj.get("edges", [])
    if edges:
        print(f"  Edges (top {min(len(edges), 15)}):")
        for e in edges[:15]:
            relation = _safe(e.get("relation"))
            conf = e.get("confidence")
            score_v = e.get("confidence_score")
            conf_str = f" [{conf}" + (f" {score_v}" if score_v is not None else "") + "]" if conf else ""
            print(f"    {_safe(e.get('source'))} --{relation}--> {_safe(e.get('target'))}{conf_str}")


def _print_path(obj: dict) -> None:
    print(f"path ({obj['hops']} hops):")
    for h in obj["path"]:
        edge = h.get("edge_to_next") or {}
        relation = edge.get("relation")
        arrow = f"  --{relation}-->" if relation else ""
        print(f"  {_safe(h.get('label')):50s}  [{_safe(h.get('source_file'), '')}]{arrow}")


def _print_node(obj: dict) -> None:
    print(f"NODE: {_safe(obj.get('label'))}")
    print(f"  id: {_safe(obj.get('id'))}")
    print(f"  source: {_safe(obj.get('source_file'), '')}:{_safe(obj.get('source_location'), '')}")
    print(f"  type: {_safe(obj.get('file_type'))}  degree: {obj.get('degree')}")
    if obj.get("outgoing"):
        print("  outgoing:")
        for o in obj["outgoing"][:MAX_EXPLAIN_EDGES]:
            print(f"    --{_safe(o.get('relation'))}--> {_safe(o.get('target'))} "
                  f"[{_safe(o.get('confidence'))}] ({_safe(o.get('source_file'), '')})")
    if obj.get("incoming"):
        print("  incoming:")
        for o in obj["incoming"][:MAX_EXPLAIN_EDGES]:
            print(f"    <--{_safe(o.get('relation'))}-- {_safe(o.get('source'))} "
                  f"[{_safe(o.get('confidence'))}] ({_safe(o.get('source_file'), '')})")


def _fmt_row(d: dict) -> str:
    label = d.get("label") or d.get("id") or ""
    sf = _safe(d.get("source_file"), "")
    score_v = d.get("score")
    prefix = f"[{score_v}] " if score_v is not None else ""
    loc = d.get("source_location")
    suffix = f":{loc}" if loc else ""
    return f"{prefix}{label[:LABEL_TRUNC]:{LABEL_TRUNC}s}  {sf}{suffix}"


# ---------- CLI ----------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="graphify-zipper",
        description="Query + extract + compress graphify-out archives.",
    )
    p.add_argument("--zip", default=DEFAULT_ZIP, help=f"archive path (default: {DEFAULT_ZIP})")
    p.add_argument("--source", default="zip", choices=["zip", "dir"],
                   help="read from archive (default) or graphify-out/ dir")
    p.add_argument("--json", action="store_true", help="JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    pq = sub.add_parser("query", help="BFS traversal from best-matching nodes")
    pq.add_argument("question")
    pq.add_argument("--depth", type=int, default=2, help="BFS depth (default: 2)")
    pq.set_defaults(func=cmd_query, needs_graph=True)

    pe = sub.add_parser("explain", help="node + outgoing/incoming edges (preferred)")
    pe.add_argument("node")
    pe.set_defaults(func=cmd_explain, needs_graph=True)

    pf = sub.add_parser("find", help="rank nodes by terms (last resort)")
    pf.add_argument("terms", nargs="+")
    pf.add_argument("--limit", type=int, default=15)
    pf.set_defaults(func=cmd_find, needs_graph=True)

    pp = sub.add_parser("path", help="shortest path between two nodes")
    pp.add_argument("a")
    pp.add_argument("b")
    pp.set_defaults(func=cmd_path, needs_graph=True)

    pv = sub.add_parser("providers", help="list provider source files")
    pv.set_defaults(func=cmd_providers, needs_graph=True)

    px = sub.add_parser("extract", help="unzip archive into a directory")
    px.add_argument("--dir", default=".", help="target dir (default: cwd)")
    px.add_argument("--force", action="store_true", help="overwrite existing graphify-out/")
    px.set_defaults(func=cmd_extract, needs_graph=False)

    pc = sub.add_parser("compress", help="build archive from graphify-out/")
    pc.add_argument("--dir", default=DEFAULT_DIR, help="source dir (default: graphify-out)")
    pc.add_argument("--method", default="zip", choices=["zip", "7z", "ppmd", "zpaq"],
                    help="zip=ZIP_LZMA (default), 7z=py7zr LZMA2, ppmd=py7zr PPMd, "
                         "zpaq=zpaq -m5 (best for dense backend repos, ~14x slower)")
    pc.add_argument("--lean", action="store_true",
                    help="skip derived files (graph.html, GRAPH_REPORT.md, obsidian/, *.svg, *.graphml) "
                         "and minify JSON before compressing; regenerate derived via `graphify export *`")
    pc.set_defaults(func=cmd_compress, needs_graph=False)
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    try:
        if args.needs_graph:
            graph_path = args.zip if args.source == "zip" else getattr(args, "dir", args.zip)
            data = load_graph(graph_path, source=args.source)
        else:
            data = None
        args.func(args, data)
    except ZipperError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
