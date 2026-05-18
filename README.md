# graphify-zipper

Claude Code skill that wraps the `graphify-out.zip` ↔ `graphify-out/`
lifecycle for repos that commit the [graphify](https://github.com/safishamsi/graphify)
knowledge graph as a BZip2 zip and gitignore the raw directory.

## Why this exists

The skill grew out of a context-mesh repo holding **50+ microservice
graphify outputs** in one tree (one `graphify-out/` per service, names
redacted). Raw,
each microservice graph is tens of MB; multiplied across the fleet the
checkout balloons into multi-GB territory and every fetch/clone pays
that cost. Committing the BZip2 zip instead — and querying it
in-place via `tools/graph_query.py` without ever extracting — keeps
the on-disk and on-the-wire footprint roughly an order of magnitude
smaller while preserving the incremental cache for rebuilds.

### Benchmark — 8 microservices from a real fleet

Measured with `7z a -tzip -mx=9 -mm=BZip2` against the raw
`graphify-out/` directory each service ships:

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

Extrapolated to a 50-repo mesh at the same mix: ~2.3 GB raw →
~130 MB zipped.

### Query latency — zip vs raw graph.json

`tools/graph_query.py find` reads the zip directly via Python `zipfile`
(BZip2 decompress in-memory). Compared against a raw `graph.json` on
disk with the same find logic, 3 runs each, WSL2 over NTFS:

| service   | raw graph.json | zip    | raw find    | zip find    |
|-----------|----------------|--------|-------------|-------------|
| service-G | 292 KB         | 136 KB | 5.5–6.6 ms  | 5.1–5.5 ms  |
| service-B | 22 MB          | 2.6 MB | 154–166 ms  | 306–321 ms  |
| service-A | 43 MB          | 5.4 MB | 269–388 ms  | 219–247 ms  |

Takeaways:

- **Small graphs:** tie — JSON parse dominates, BZip2 over a few hundred
  KB is free.
- **Medium graphs:** raw wins ~2× — payload fits in page-cache, BZip2
  CPU cost has no I/O savings to offset.
- **Large graphs on slow filesystems:** zip wins ~25% — reading 43 MB
  raw over NTFS/WSL is slower than decompressing 5.4 MB and parsing.
  First raw run (cold cache) was 388 ms vs 247 ms for zip; warm cache
  narrows but doesn't close the gap.

Net: query latency stays in the same order of magnitude either way. The
durable win is **disk + transfer footprint** — ~94% smaller commits,
~10× faster clone/fetch on a multi-service mesh.

Three responsibilities:

1. **Read-only queries** via `tools/graph_query.py` against the zip
   directly (no extract).
2. **Pre-rebuild extract** with `7z x -y graphify-out.zip` so the next
   `/graphify` run sees prior cache + incremental manifest.
3. **Post-rebuild recompress** with
   `7z a -tzip -mx=9 -mm=BZip2 graphify-out.zip graphify-out` so the
   commit picks up the new state.

## Install

```bash
# Direct clone:
git clone https://github.com/azamma/graphify-zipper ~/.claude/skills/graphify-zipper

# Or via Vercel skills CLI:
npx skills add azamma/graphify-zipper
```

## Bundled wrapper

Ships `_zipper.py` (stdlib only) + `pyrun.sh` (cross-platform Python
launcher that caches a 3.10+ interpreter in `.python_bin`). The skill
prefers the wrapper over external `7z` so there is no system
dependency:

```bash
ZIPPER="bash ~/.claude/skills/graphify-zipper/pyrun.sh _zipper.py"
$ZIPPER find <english terms>     # top-scoring nodes
$ZIPPER explain <node>           # node + neighbors
$ZIPPER path <A> <B>             # shortest path
$ZIPPER providers                # list provider source files
$ZIPPER extract [--force]        # graphify-out.zip -> graphify-out/
$ZIPPER compress                 # graphify-out/ -> graphify-out.zip (BZip2 lvl 9)
```

Query commands accept `--json`. The wrapper refuses to compress a
directory missing `graph.json` and refuses to clobber an existing
`graphify-out/` without `--force` — both catch real footguns from the
manual `7z` flow.

## Trigger phrases

- ES: "comprimí el grafo", "descomprimí graphify", "tirá una query al grafo"
- EN: "zip graphify", "compress graph output", "query the graphify zip"
- Auto before/after any `/graphify` rebuild subcommand
  (`/graphify <path>`, `--update`, `--cluster-only`, `add`, `--wiki`).

## System dependency

Requires `7z` (Linux: `p7zip-full`, macOS: `brew install p7zip`,
Windows: `winget install 7zip.7zip`). Do not fall back to stock
`zip`/`unzip` — the committed archive uses BZip2 method; deflate
changes the archive byte-for-byte.

See [SKILL.md](SKILL.md) for full flow.
