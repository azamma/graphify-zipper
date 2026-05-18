#!/usr/bin/env python3
"""graphify-zipper: query + extract + compress graphify-out.zip. Stdlib only.

Subcommands:
  find <terms...>         top-scoring nodes by label/source match
  explain <node>          node + outgoing/incoming edges
  path <a> <b>            shortest path between two nodes (BFS undirected)
  providers               list provider/* source files seen in graph
  extract [--zip Z] [--dir D]   unzip Z into D (default: graphify-out.zip -> .)
  compress [--dir D] [--zip Z]  build BZip2 zip from graphify-out/

Common flags:
  --zip <path>            archive path (default: graphify-out.zip)
  --json                  emit JSON instead of human-friendly text
  --limit N               cap result count (find only)

Query paths read the zip directly via zipfile — no extract step.
Compress always uses ZIP_BZIP2 + level 9 to match the byte-shape of
7z-produced archives (BZip2 method, max compression).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from collections import deque
from pathlib import Path

DEFAULT_ZIP = "graphify-out.zip"
DEFAULT_DIR = "graphify-out"
DEFAULT_INNER = "graphify-out/graph.json"


# ---------- graph loading ----------

def load_graph(zip_path: str, inner: str = DEFAULT_INNER) -> dict:
    p = Path(zip_path)
    if not p.exists():
        sys.exit(f"error: {zip_path} not found")
    with zipfile.ZipFile(p) as z:
        try:
            raw = z.read(inner)
        except KeyError:
            sys.exit(f"error: {inner} not in {zip_path}")
    return json.loads(raw)


def index(data: dict):
    nodes = {n["id"]: n for n in data.get("nodes", [])}
    edges = data.get("links", data.get("edges", []))
    adj: dict[str, list[tuple[str, dict]]] = {}
    radj: dict[str, list[tuple[str, dict]]] = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s is None or t is None:
            continue
        adj.setdefault(s, []).append((t, e))
        radj.setdefault(t, []).append((s, e))
    return nodes, adj, radj, edges


def score(node: dict, terms: list[str]) -> int:
    label = (node.get("label") or "").lower()
    src = (node.get("source_file") or "").lower()
    return sum(3 for t in terms if t in label) + sum(1 for t in terms if t in src)


# ---------- query commands ----------

def cmd_find(args, data):
    nodes, _, _, _ = index(data)
    terms = [t.lower() for t in args.terms]
    scored = [(score(n, terms), nid) for nid, n in nodes.items()]
    scored = [x for x in scored if x[0] > 0]
    scored.sort(reverse=True)
    out = []
    for s, nid in scored[: args.limit]:
        n = nodes[nid]
        out.append({
            "score": s, "id": nid, "label": n.get("label"),
            "source_file": n.get("source_file"),
            "source_location": n.get("source_location"),
        })
    emit(out, args)


def cmd_explain(args, data):
    nodes, adj, radj, _ = index(data)
    terms = [args.node.lower()]
    candidates = [(score(n, terms), nid) for nid, n in nodes.items()]
    candidates = [x for x in candidates if x[0] > 0]
    if not candidates:
        sys.exit(f"no node matches {args.node!r}")
    candidates.sort(reverse=True)
    nid = candidates[0][1]
    n = nodes[nid]
    out_n = [{
        "target": nodes[t].get("label", t), "relation": e.get("relation"),
        "confidence": e.get("confidence"), "source_file": nodes[t].get("source_file"),
    } for t, e in adj.get(nid, [])]
    in_n = [{
        "source": nodes[s].get("label", s), "relation": e.get("relation"),
        "confidence": e.get("confidence"), "source_file": nodes[s].get("source_file"),
    } for s, e in radj.get(nid, [])]
    out = {
        "id": nid, "label": n.get("label"),
        "source_file": n.get("source_file"),
        "source_location": n.get("source_location"),
        "file_type": n.get("file_type"),
        "degree": len(out_n) + len(in_n),
        "outgoing": out_n, "incoming": in_n,
    }
    emit(out, args)


def cmd_path(args, data):
    nodes, adj, radj, _ = index(data)

    def find_node(term: str) -> str | None:
        terms = [term.lower()]
        scored = [(score(n, terms), nid) for nid, n in nodes.items()]
        scored = [x for x in scored if x[0] > 0]
        scored.sort(reverse=True)
        return scored[0][1] if scored else None

    src = find_node(args.a)
    tgt = find_node(args.b)
    if not src or not tgt:
        sys.exit(f"could not match nodes: a={args.a!r} -> {src}, b={args.b!r} -> {tgt}")

    parents: dict[str, tuple[str, dict] | None] = {src: None}
    q = deque([src])
    while q:
        u = q.popleft()
        if u == tgt:
            break
        for v, e in adj.get(u, []) + radj.get(u, []):
            if v not in parents:
                parents[v] = (u, e)
                q.append(v)
    if tgt not in parents:
        sys.exit(f"no path between {args.a!r} and {args.b!r}")

    chain = []
    cur = tgt
    while cur is not None:
        chain.append(cur)
        prev = parents[cur]
        cur = prev[0] if prev else None
    chain.reverse()

    hops = []
    for i, nid in enumerate(chain):
        entry = {
            "label": nodes[nid].get("label", nid),
            "source_file": nodes[nid].get("source_file"),
        }
        if i < len(chain) - 1:
            nxt = chain[i + 1]
            edge = next(
                (e for v, e in adj.get(nid, []) + radj.get(nid, []) if v == nxt),
                {},
            )
            entry["edge_to_next"] = {
                "relation": edge.get("relation"),
                "confidence": edge.get("confidence"),
            }
        hops.append(entry)
    emit({"hops": len(chain) - 1, "path": hops}, args)


def cmd_providers(args, data):
    nodes, _, _, _ = index(data)
    seen_files = {}
    for n in nodes.values():
        sf = n.get("source_file") or ""
        if "providers/" in sf and "/test" not in sf and "tests/" not in sf:
            seen_files.setdefault(sf, n)
    rows = sorted(seen_files.keys())
    emit(rows, args)


# ---------- lifecycle commands ----------

def cmd_extract(args, _data=None):
    zip_path = Path(args.zip)
    out_dir = Path(args.dir)
    if not zip_path.exists():
        sys.exit(f"error: {zip_path} not found")
    target = out_dir / DEFAULT_DIR
    if target.exists() and not args.force:
        sys.exit(
            f"error: {target} already exists. Previous rebuild not recompressed?\n"
            f"Re-run with --force to overwrite, or inspect/preserve first."
        )
    if target.exists():
        shutil.rmtree(target)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(out_dir)
    print(f"extracted {zip_path} -> {target}")


def cmd_compress(args, _data=None):
    src_dir = Path(args.dir)
    zip_path = Path(args.zip)
    if not src_dir.exists():
        sys.exit(f"error: source {src_dir} not found")
    inner_check = src_dir / "graph.json"
    if not inner_check.exists():
        sys.exit(f"error: {inner_check} missing - refusing to zip empty/stale dir")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(
        zip_path, "w",
        compression=zipfile.ZIP_BZIP2,
        compresslevel=9,
    ) as z:
        for p in src_dir.rglob("*"):
            if p.is_file():
                arcname = p.relative_to(src_dir.parent)
                z.write(p, arcname)
    size = zip_path.stat().st_size
    print(f"compressed {src_dir} -> {zip_path} ({size:,} bytes, BZip2 level 9)")


# ---------- output helpers ----------

def emit(obj, args):
    if args.json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
        return
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                print(_fmt_row(item))
            else:
                print(item)
    elif isinstance(obj, dict):
        if "path" in obj and "hops" in obj:
            print(f"path ({obj['hops']} hops):")
            for h in obj["path"]:
                edge = h.get("edge_to_next")
                arrow = (
                    f"  --{edge['relation']}-->"
                    if edge and edge.get("relation") else ""
                )
                print(f"  {h['label']:50s}  [{h.get('source_file','')}]{arrow}")
        else:
            print(f"NODE: {obj.get('label')}")
            print(f"  id: {obj.get('id')}")
            print(f"  source: {obj.get('source_file')}:{obj.get('source_location') or ''}")
            print(f"  type: {obj.get('file_type')}  degree: {obj.get('degree')}")
            if obj.get("outgoing"):
                print("  outgoing:")
                for o in obj["outgoing"][:30]:
                    print(f"    --{o.get('relation')}--> {o['target']} [{o.get('confidence')}] ({o.get('source_file','')})")
            if obj.get("incoming"):
                print("  incoming:")
                for o in obj["incoming"][:30]:
                    print(f"    <--{o.get('relation')}-- {o['source']} [{o.get('confidence')}] ({o.get('source_file','')})")


def _fmt_row(d: dict) -> str:
    label = d.get("label") or d.get("id") or ""
    sf = d.get("source_file") or ""
    score_v = d.get("score")
    prefix = f"[{score_v}] " if score_v is not None else ""
    loc = d.get("source_location")
    suffix = f":{loc}" if loc else ""
    return f"{prefix}{label[:60]:60s}  {sf}{suffix}"


# ---------- CLI ----------

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="graphify-zipper",
        description="Query + extract + compress graphify-out.zip. Stdlib only.",
    )
    p.add_argument("--zip", default=DEFAULT_ZIP, help=f"archive path (default: {DEFAULT_ZIP})")
    p.add_argument("--json", action="store_true", help="JSON output (query cmds)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("find", help="find nodes by English terms")
    pf.add_argument("terms", nargs="+")
    pf.add_argument("--limit", type=int, default=15)
    pf.set_defaults(func=cmd_find, needs_graph=True)

    pe = sub.add_parser("explain", help="explain a node and its neighbors")
    pe.add_argument("node")
    pe.set_defaults(func=cmd_explain, needs_graph=True)

    pp = sub.add_parser("path", help="shortest path between two nodes")
    pp.add_argument("a")
    pp.add_argument("b")
    pp.set_defaults(func=cmd_path, needs_graph=True)

    pv = sub.add_parser("providers", help="list provider source files")
    pv.set_defaults(func=cmd_providers, needs_graph=True)

    px = sub.add_parser("extract", help="unzip graphify-out.zip into a directory")
    px.add_argument("--dir", default=".", help="target dir (default: cwd)")
    px.add_argument("--force", action="store_true", help="overwrite existing graphify-out/")
    px.set_defaults(func=cmd_extract, needs_graph=False)

    pc = sub.add_parser("compress", help="build graphify-out.zip from graphify-out/")
    pc.add_argument("--dir", default=DEFAULT_DIR, help="source dir (default: graphify-out)")
    pc.set_defaults(func=cmd_compress, needs_graph=False)

    args = p.parse_args(argv)
    data = load_graph(args.zip) if args.needs_graph else None
    args.func(args, data)


if __name__ == "__main__":
    main()
