# graphify-zipper

A [Claude Code](https://claude.com/claude-code) skill that manages the `graphify-out.zip` ↔ `graphify-out/` lifecycle for repositories that commit a [graphify](https://github.com/safishamsi/graphify) knowledge graph as a BZip2 archive and gitignore the raw directory.

Built for context-mesh repos that hold many service graphs side by side: query the archive in-place, extract before a rebuild, recompress after — without ever losing the incremental cache.

## Features

- **Zero-extract queries** — `explain`, `path`, `providers`, `find` read straight from the zip via Python `zipfile`.
- **Safe extract / recompress** — refuses to clobber an existing `graphify-out/` and refuses to zip a directory missing `graph.json`.
- **Stdlib only** — bundled `_zipper.py` needs no external dependencies; `pyrun.sh` auto-detects a 3.10+ interpreter on WSL, macOS, and Windows.
- **Byte-compatible with `7z`** — uses `ZIP_BZIP2` at level 9, matching `7z a -tzip -mx=9 -mm=BZip2` output.
- **JSON output** on every query subcommand for machine-readable workflows.

## Install

```bash
git clone https://github.com/azamma/graphify-zipper ~/.claude/skills/graphify-zipper
```

Or via the Vercel skills CLI:

```bash
npx skills add azamma/graphify-zipper
```

Claude Code auto-discovers skills under `~/.claude/skills/`. No further setup required.

## Quick start

```bash
ZIPPER="bash ~/.claude/skills/graphify-zipper/pyrun.sh _zipper.py"

$ZIPPER explain UserService        # PREFERRED: node + neighbors
$ZIPPER path Controller Repository # shortest path (BFS, undirected)
$ZIPPER providers                  # provider source files
$ZIPPER find auth middleware       # LAST RESORT: ranked matches when explain misses
$ZIPPER extract [--force]          # graphify-out.zip -> ./graphify-out/
$ZIPPER compress                   # ./graphify-out/ -> graphify-out.zip
```

All subcommands accept `--zip <path>` (default `graphify-out.zip`). Query subcommands accept `--json` and `--limit N`.

> [!TIP]
> Always start with `explain`. It picks the top-scoring node by the same algorithm `find` uses, then expands the neighborhood (edges, relations, sources). Drop to `find` only when `explain` exits with `no node matches` — then re-run `explain` on the best hit. `find` alone has no edges.

> [!NOTE]
> Node labels and source paths in graphify output are English. Phrase queries in English; Spanish terms will not match.

## How it works

The skill exposes three execution paths and picks one based on intent:

| Intent | Path |
|---|---|
| "where / explain / path / providers / find" question | Query (no extract — `explain` first, `find` only on miss) |
| About to run `/graphify <path>`, `--update`, `--cluster-only`, `add`, `--wiki` | Extract → rebuild → recompress |
| "comprimí" / "zip" alone, with `graphify-out/` present | Recompress only |
| "descomprimí" / "unzip" alone | Extract only |

> [!IMPORTANT]
> Never recompress without extract first (or `graphify-out/` already on disk). The wrapper refuses the operation, but the underlying invariant is: the zip must reflect the latest rebuild state, not a stale partial.

See [SKILL.md](SKILL.md) for the full decision flow and edge cases.

## Why this exists

The skill grew out of a context-mesh repo holding **50+ microservice graphify outputs** in one tree. Raw, each service graph is tens of MB; multiplied across the fleet the checkout balloons into multi-GB territory, and every clone or fetch pays that cost. Committing the BZip2 zip and querying it in place keeps the disk + transfer footprint roughly an order of magnitude smaller while preserving the incremental cache for rebuilds.

### Disk footprint — 8 services from a real fleet

| service   | raw    | zip    | saved  |
|-----------|--------|--------|--------|
| frontend  | 100 MB | 7.5 MB | 91.6%  |
| service-A | 105 MB | 5.4 MB | 94.6%  |
| service-B | 57 MB  | 2.6 MB | 95.2%  |
| service-C | 37 MB  | 1.7 MB | 95.2%  |
| service-D | 27 MB  | 1.1 MB | 96.1%  |
| service-E | 23 MB  | 1.2 MB | 94.3%  |
| service-F | 19 MB  | 1.0 MB | 94.1%  |
| service-G | 1.1 MB | 136 KB | 82.2%  |
| **total** | **369 MB** | **20.6 MB** | **~94%** |

Extrapolated to a 50-repo mesh at the same mix: ~2.3 GB raw → ~130 MB zipped.

### Query latency — zip vs raw `graph.json`

3 runs each on WSL2 over NTFS, using `find` against the in-memory parsed graph:

| service   | raw graph.json | zip    | raw find    | zip find    |
|-----------|----------------|--------|-------------|-------------|
| service-G | 292 KB         | 136 KB | 5.5–6.6 ms  | 5.1–5.5 ms  |
| service-B | 22 MB          | 2.6 MB | 154–166 ms  | 306–321 ms  |
| service-A | 43 MB          | 5.4 MB | 269–388 ms  | 219–247 ms  |

- **Small graphs:** tie — JSON parse dominates.
- **Medium graphs:** raw wins ~2× — payload sits in page cache, BZip2 CPU has no I/O savings to offset.
- **Large graphs on slow filesystems:** zip wins ~25% — decompressing 5.4 MB beats reading 43 MB over NTFS/WSL.

Query latency stays in the same order of magnitude either way. The durable win is **footprint**: ~94% smaller commits, ~10× faster clone/fetch across a multi-service mesh.

## Trigger phrases

The skill auto-activates on:

- **ES:** "comprimí el grafo", "descomprimí graphify", "tirá una query al grafo"
- **EN:** "zip graphify", "compress graph output", "query the graphify zip"
- Any `/graphify` rebuild subcommand: `/graphify <path>`, `--update`, `--cluster-only`, `add`, `--wiki`

## Requirements

- Python 3.10+ (auto-detected by `pyrun.sh`)
- A repo that commits `graphify-out.zip` and gitignores `graphify-out/`

> [!TIP]
> External `7z` is **not** required — the bundled wrapper uses stdlib `zipfile` with `ZIP_BZIP2`. If `7z` is already installed and preferred, `7z x -y graphify-out.zip` and `7z a -tzip -mx=9 -mm=BZip2 graphify-out.zip graphify-out` produce byte-compatible archives.

> [!WARNING]
> Do not fall back to stock `zip`/`unzip`. The committed archive uses the BZip2 method; deflate changes the archive byte-for-byte and will surface as a noisy diff on every commit.

## Related

- [graphify](https://github.com/safishamsi/graphify) — the knowledge graph builder this skill wraps around.
- [SKILL.md](SKILL.md) — full skill spec consumed by Claude Code.
