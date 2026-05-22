# graphify-zipper

A [Claude Code](https://claude.com/claude-code) skill that manages the `graphify-out.zip` ↔ `graphify-out/` lifecycle for repositories that commit a [graphify](https://github.com/safishamsi/graphify) knowledge graph as a compressed archive and gitignore the raw directory.

Built for context-mesh repos that hold many service graphs side by side: query the archive in-place, extract before a rebuild, recompress after — without ever losing the incremental cache.

## Features

- **Zero-extract queries** — `query`, `explain`, `path`, `providers`, `find` read straight from the zip/7z archive.
- **Rich query output** — node `source_location`, `community`, edge `confidence` + `confidence_score`; BFS with per-start visited sets matches native `graphify query` node counts.
- **Safe extract / recompress** — refuses to clobber an existing `graphify-out/` and refuses to zip a directory missing `graph.json`.
- **Stdlib by default** — bundled `_zipper.py` needs no external dependencies; `pyrun.sh` auto-detects a 3.10+ interpreter.
- **7z LZMA2 / PPMd / zpaq support** — `--method 7z` uses `py7zr` LZMA2 (~45% smaller than BZip2); `--method ppmd` uses PPMd order=32 mem=29 (~62% smaller, best single-codec for text/JSON); `--method zpaq` uses zpaq -m5 (~20% smaller than PPMd on dense backend repos >50 KB, ~14x slower).
- **`--lean` preprocessing** — drops regeneratable files (`graph.html`, `GRAPH_REPORT.md`, `obsidian/`, `*.svg`, `*.graphml`) and minifies JSON. Stacks with any codec for an extra ~5-15% reduction.
- **Integrity-checked output** — every archive verified post-write via `7z t` (or `py7zr.testzip()` fallback) and auto-retries with next codec on corruption.
- **Auto-detect format** — reads `.zip` (ZIP_LZMA), `.7z` (LZMA2 / PPMd), and `.zpaq` archives transparently.
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
$ZIPPER query "auth flow" [--depth N] # BFS from top 3 matches (broad context)
$ZIPPER path Controller Repository # shortest path (BFS, undirected)
$ZIPPER providers                  # provider source files
$ZIPPER find auth middleware       # LAST RESORT: ranked matches when explain misses
$ZIPPER extract [--force]              # graphify-out.zip -> ./graphify-out/
$ZIPPER compress                       # ./graphify-out/ -> graphify-out.zip (ZIP_LZMA)
$ZIPPER compress --method 7z           # ./graphify-out/ -> graphify-out.7z (7z LZMA2, ~45% smaller)
$ZIPPER compress --method ppmd         # ./graphify-out/ -> graphify-out.7z (7z PPMd, ~62% smaller, best for text/JSON)
$ZIPPER compress --method ppmd --lean  # PPMd + drop derived files + minify JSON (smallest single-codec)
$ZIPPER compress --method zpaq --lean  # zpaq -m5 + lean (smallest for dense backend repos >50 KB)
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

### Compression benchmark — ZIP_LZMA vs 7z LZMA2

10 services from a real fleet (original = prior BZip2 zip, zip = ZIP_LZMA level 9, 7z = py7zr LZMA2 level 9):

| service   | orig KB | zip KB | 7z KB | zip vs orig | 7z vs orig |
|-----------|---------|--------|-------|-------------|------------|
| service-H | 760     | 721    | 498   | 5.3%        | 34.5%      |
| service-I | 7,583   | 6,579  | 2,650 | 13.2%       | 65.0%      |
| service-J | 2,621   | 2,588  | 1,812 | 1.3%        | 30.9%      |
| service-K | 1,028   | 1,071  | 808   | -4.2%       | 21.4%      |
| service-L | 1,219   | 1,183  | 731   | 2.9%        | 40.0%      |
| service-M | 1,674   | 1,615  | 1,105 | 3.5%        | 34.0%      |
| service-N | 132     | 125    | 48    | 5.1%        | 63.6%      |
| service-O | 4,933   | 4,791  | 3,272 | 2.9%        | 33.7%      |
| service-P | 946     | 904    | 562   | 4.5%        | 40.6%      |
| service-Q | 1,320   | 1,237  | 716   | 6.3%        | 45.7%      |
| **total** | **22,254**| **20,814**| **12,102**| **6.5%**    | **45.6%**  |

ZIP_LZMA saves ~6% over prior BZip2. 7z LZMA2 saves **45.6%** over BZip2 — at the cost of requiring `py7zr` (`pip install py7zr`).

### PPMd — best for text/JSON

Knowledge graphs are JSON-heavy. PPMd (context-mixing predictor) beats LZ-family algorithms on natural-language and structured text. Benchmark on a real `graphify-out/` (1,640 files, 30.8 MB raw, 100% verified byte-identical decompress):

| Method | Filter | Size | vs LZMA2 baseline |
|---|---|---|---|
| ZIP_LZMA preset=9 | stdlib | 1,860 KB | +97% |
| **7z LZMA2 preset=9** (`--method 7z`) | py7zr | 969 KB | baseline |
| 7z LZMA2 EXTREME | py7zr | 786 KB | -19% |
| 7z BZIP2 | py7zr | 886 KB | -9% |
| 7z ZSTD level=22 | py7zr | 865 KB | -11% |
| 7z BROTLI level=11 | py7zr | 863 KB | -11% |
| 7z PPMd order=16 mem=27 | py7zr | 655 KB | -32% |
| **7z PPMd order=32 mem=29** (`--method ppmd`) | py7zr | **605 KB** | **-38%** |
| **7z PPMd + `--lean`** | py7zr | **275 KB** | **-72%** |
| **zpaq -m5 + `--lean`** | zpaq | **226 KB** | **-77%** |

PPMd wins big on JSON/text — context modeling beats Lempel-Ziv variants. All decompress to byte-identical content (sha256 verified). Compression time and decompression time are within the same order of magnitude as LZMA2 (~12 s comp, ~1.4 s decomp on the benchmark corpus).

### Fleet benchmark — 8 microservices, total impact

Running `--method ppmd --lean` across a real 8-repo fleet:

| Repo | Files | Orig `.7z` (LZMA2) | PPMd + lean | Savings |
|---|---:|---:|---:|---:|
| service-A (frontend) | 7351 | 2.59 MB | 1.45 MB | -44% |
| service-B | 2885 | 3.20 MB | 1.87 MB | -42% |
| service-C | 1564 | 1.77 MB | 1.02 MB | -42% |
| service-D | 967 | 1.08 MB | 631 KB | -43% |
| service-E | 867 | 731 KB | 328 KB | -55% |
| service-F | 636 | 562 KB | 266 KB | -53% |
| service-G | 480 | 808 KB | 368 KB | -54% |
| service-H | 152 | 48 KB | 22.7 KB | -53% |
| **TOTAL** | **14902** | **10.74 MB** | **5.92 MB** | **-44.9%** |

Repos with denser cache content (more files, more repetition) gain more from PPMd context modeling. Extrapolated to a 50-repo mesh at this ratio: ~50 MB committed → drops to ~28 MB.

### zpaq -m5 vs PPMd on backend microservices

`--method zpaq` is opt-in for repos where the extra compression is worth the slower compress time. Bench on 7 backend microservices:

| Repo | PPMd + lean | zpaq -m5 + lean | Δ | Compress time |
|---|---:|---:|---:|---:|
| service-C | 1.01 MB | 763 KB | **-26.5%** | 64s |
| service-G | 367 KB | 274 KB | **-25.3%** | 26s |
| service-E | 326 KB | 286 KB | -12.3% | 19s |
| service-D | 629 KB | 488 KB | -22.4% | 41s |
| service-H (tiny) | 22 KB | 29 KB | **+28%** ❌ | 1s |
| service-B | 1.86 MB | 1.44 MB | -22.7% | 95s |
| service-F | 264 KB | 220 KB | -16.6% | 17s |
| **TOTAL (6/7 win)** | **4.45 MB** | **3.45 MB** | **-22.4%** | 262s |

**Decision rule**: use `zpaq` when the PPMd archive is ≥50 KB. For smaller archives, zpaq's fixed overhead exceeds its compression gain.

Compress time: zpaq is ~5-15x slower than PPMd. For CI auto-rebuild this is acceptable (one-shot per commit). For interactive workflows, stick with PPMd.

### Functional parity — `--lean` archive vs raw `graphify-out/`

`--lean` strips derived files and minifies JSON. Critical question: does an extracted `--lean` archive behave identically when graphify reads it? Verified on a real `graphify-out/` (service-F, 636 files):

| Operation | Raw `graphify-out/` | Extracted `--method ppmd --lean` | Match |
|---|---|---|---|
| `graph.json` canonical SHA256 | `7e2e9c38...` | `7e2e9c38...` | ✅ identical |
| `manifest.json` canonical SHA256 | `d5d1b569...` | `d5d1b569...` | ✅ identical |
| `graphify query "X"` | 12 nodes, 9 edges | 12 nodes, 9 edges | ✅ semantically identical (3 lines differ only in BFS visit order — graphify does not guarantee deterministic order) |
| `graphify path "A" "B"` | 3 hops, exact path | 3 hops, exact path | ✅ byte-identical |
| `graphify.detect.detect_incremental(src)` | 5 new, 554 deleted, 951 total | 5 new, 554 deleted, 951 total | ✅ identical |
| `graphify.cache.check_semantic_cache(files)` | 0/50 hit | 0/50 hit | ✅ same semantics |

Why this works:

- **JSON minification is content-preserving**: graphify reads every `*.json` via `json.loads`, which is whitespace-agnostic.
- **`file_hash` hashes source files** (the `.java`/`.py`/etc. being analyzed), not graphify's own JSON artifacts. So minifying `manifest.json` doesn't invalidate cache entries — the hashes stored *inside* are string values, not computed over the JSON itself.
- **Skipped files are pure outputs**: `graph.html`, `GRAPH_REPORT.md`, `obsidian/`, `*.svg`, `*.graphml` are generated *from* `graph.json`. graphify never reads them as input — only writes them via `graphify export *`. Regenerable on demand from a `--lean` extract.

`--lean` is safe for any workflow that uses `graphify query / path / explain / --update / --cluster-only`. Re-running `graphify export html` (or `wiki`, `obsidian`, `svg`, `graphml`) after extract restores the dropped artifacts when needed.

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
- A repo that commits `graphify-out.zip` / `graphify-out.7z` and gitignores `graphify-out/`
- `py7zr` (optional) — only needed for `--method 7z` / `--method ppmd` compression and `.7z` read.
  Install with `pip install py7zr`. On externally-managed Python systems
  use `uv pip install --system --break-system-packages py7zr` or a venv.

> [!TIP]
> External `7z` binary is **not** required. Default uses stdlib `zipfile` with `ZIP_LZMA`. With `py7zr` installed, `--method 7z` produces `.7z` archives ~45% smaller, and `--method ppmd` produces ~62% smaller archives on text/JSON-heavy corpora.

> [!WARNING]
> Do not fall back to stock `zip`/`unzip`. The committed archive uses LZMA method; deflate changes the archive byte-for-byte and will surface as a noisy diff on every commit.

## Related

- [graphify](https://github.com/safishamsi/graphify) — the knowledge graph builder this skill wraps around.
- [SKILL.md](SKILL.md) — full skill spec consumed by Claude Code.
