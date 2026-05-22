---
name: graphify-zipper
description: Compress, decompress, and query the committed `graphify-out.zip` knowledge graph artifact without losing incremental state. Use this skill whenever the user wants to zip/unzip the graphify output, query it (find/explain/path/providers), or is about to invoke any `/graphify` rebuild subcommand (`/graphify <path>`, `/graphify --update`, `/graphify --cluster-only`, `/graphify add`, `/graphify --wiki`). Also trigger when the user says "comprimi el grafo", "descomprimi graphify", "zip graphify", "compress graph output", "tirar query al grafo", "query the graphify zip", or any variation of working with `graphify-out.zip` / `graphify-out/`. Repos that commit `graphify-out.zip` and gitignore `graphify-out/` rely on this lifecycle — skipping extract loses prior cache and incremental state; skipping recompress drops the rebuild from the next commit.
---

# graphify-zipper

Wraps the `graphify-out.zip` ↔ `graphify-out/` lifecycle for repos that
commit the knowledge graph as a LZMA zip and gitignore the raw directory
(e.g. `agent-charly`).

Three responsibilities:

1. **Read-only queries** — `find`, `explain`, `path`, `providers` against
   the zip directly. No extract, no recompress, no risk.
2. **Pre-rebuild extract** — unzip into `graphify-out/` so the next
   `/graphify` run sees prior cache + incremental manifest.
3. **Post-rebuild recompress** — rebuild the zip from `graphify-out/`
   with LZMA compression so the commit picks up the new state.

## How to invoke the bundled wrapper

The skill ships `_zipper.py` (stdlib-only) + `pyrun.sh` (cross-platform
Python launcher that auto-detects and caches a 3.10+ interpreter).
**Always invoke through `pyrun.sh`** — never call `python3 _zipper.py`
directly. The launcher handles WSL/macOS/Windows interpreter differences
and caches the resolved path in `.python_bin` next to the script.

```bash
bash ~/.claude/skills/graphify-zipper/pyrun.sh _zipper.py <subcommand> [args]
```

For brevity below, alias once per session:

```bash
ZIPPER="bash ~/.claude/skills/graphify-zipper/pyrun.sh _zipper.py"
```

All subcommands accept `--zip <path>` (default `graphify-out.zip` in cwd)
and query subcommands accept `--json` for machine-readable output.

## Path 1 — Query without extracting (default for read-only work)

The wrapper reads the zip directly via Python `zipfile`. Stdlib only, no
extraction needed. **Phrase all queries in English** — node labels and
source paths are English; Spanish terms will not match.

```bash
$ZIPPER explain <node>                     # PREFERRED: node + outgoing/incoming edges
$ZIPPER query <question> [--depth N]       # BFS traversal from top 3 matches (broad context)
$ZIPPER path <A> <B>                       # shortest path (BFS, undirected)
$ZIPPER providers                          # list provider source files
$ZIPPER find <english terms> [--limit N]   # LAST RESORT: ranked label/source matches
```

Query output includes `source_location` (`:Lxx`), `community` (`[c=NN]`), and edge `confidence` + `confidence_score`. BFS uses per-start visited sets with global node/edge dedupe — matches native `graphify query` node counts.

**Use this path when:**
- User asks "where is X / what calls Y / how does Z reach W".
- Any structural/locating question before a Read.
- User explicitly says "tirá una query al grafo" / "query the graph".

**Query strategy — `explain` first, `find` only as fallback:**
1. **Always start with `explain <term>`.** Richest view: node + all edges + sources. Internally picks top scoring match by same algorithm `find` uses, then expands neighborhood.
2. Only if `explain` exits with `no node matches`, run `find <term>` to discover near matches — then re-run `explain` on the best hit. Never stop at `find` output; it has no edges.
3. For path-tracing ("how does A reach B"), use `path <A> <B>` directly. Skip `find`/`explain` warmup.

`find` is a discovery tool of last resort. Default to `explain` even when unsure — it fails loud if the term misses, which is your signal to fall back.

Do NOT extract just to query — wastes I/O and risks accidental
recompress mismatch.

## Path 2 — Extract before a rebuild

Run this immediately BEFORE any `/graphify` subcommand that writes the
graph: `/graphify <path>`, `/graphify --update`, `/graphify --cluster-only`,
`/graphify add <url>`, `/graphify --wiki`.

```bash
$ZIPPER extract                # graphify-out.zip -> ./graphify-out/
$ZIPPER extract --dir /some/path
$ZIPPER extract --force        # overwrite existing graphify-out/
```

Without `--force`, the wrapper refuses to clobber an existing
`graphify-out/` — that usually means a previous run was not
recompressed and local rebuild state might be worth preserving. Surface
this to the user before re-running with `--force`.

Skipping this step makes the next rebuild start from scratch and
discards the incremental cache.

## Path 3 — Recompress after a rebuild

Run immediately AFTER the `/graphify` subcommand finishes successfully:

```bash
$ZIPPER compress                       # ./graphify-out/ -> graphify-out.zip (ZIP_LZMA)
$ZIPPER compress --method 7z           # ./graphify-out/ -> graphify-out.7z (7z LZMA2)
$ZIPPER compress --method ppmd         # ./graphify-out/ -> graphify-out.7z (7z PPMd — smallest single-codec for JSON/text)
$ZIPPER compress --method ppmd --lean  # PPMd + drop derived files + minify JSON
$ZIPPER compress --method zpaq --lean  # zpaq -m5 + lean (smallest for dense backend repos; ~14x slower)
$ZIPPER compress --dir /some/path --zip /some/out.zip
```

Four compression methods:

| Method | Algo | Notes |
|---|---|---|
| `zip` (default) | ZIP_LZMA level 9 | Stdlib only, no deps |
| `7z` | py7zr LZMA2 level 9 | ~45% smaller than BZip2; widely compatible |
| `ppmd` | py7zr PPMd order=32 mem=29 | ~62% smaller than BZip2 — best single-codec for text/JSON |
| `zpaq` | zpaq -m5 (context mixing) | **~20% smaller than PPMd on dense backend repos** (>50 KB); requires `zpaq` binary (`apt-get install zpaq`); ~14x slower than PPMd; loses on tiny repos (<50 KB) due to fixed overhead |

All produce byte-identical content on decompress (verified by sha256 + external `7z t` / `zpaq l`).

**Method selection guidance:**
- Default `zip`: maximum portability, no dependencies.
- `ppmd`: best ratio for most repos; works on any corpus size.
- `zpaq`: opt-in for heavy backend repos (Java/Spring etc.) where the extra ~20% saving over PPMd is worth the slower compress time. Skip for repos under ~50 KB.

### `--lean` preprocessing flag

Drops files derived from `graph.json` and minifies the remaining JSON before
compression. Stacks with any method (`zip`/`7z`/`ppmd`). Reduces archive size
by an additional ~5-15% on top of the chosen codec.

Skipped automatically (regeneratable via `graphify export *`):

- `graph.html` (regen via `graphify export html`)
- `GRAPH_REPORT.md` (regen via report.generate)
- `obsidian/` directory (regen via `graphify export obsidian`)
- `*.svg`, `*.graphml` exports

JSON minification: `graph.json`, `manifest.json`, `cache/*.json` and any other
`.json` get re-serialized with `separators=(',',':')`. graphify code reads
them via `json.loads` — formatting-agnostic, no behavior change.

Keep `--lean` OFF if you need to commit a `graph.html` viewer alongside the
archive (e.g. for non-graphify viewers / GitHub web previews).

### Integrity check + fallback chain

After compression, the wrapper runs `7z t` (or `py7zr.testzip()` fallback) to
verify the archive. If the chosen codec produces an invalid archive, it
automatically retries with the next codec in chain:

```
zpaq → ppmd → 7z LZMA2 → ZIP_LZMA (stdlib)
ppmd → 7z LZMA2 → ZIP_LZMA
7z   → ZIP_LZMA
```

Each fallback prints a `WARNING:` line so you know what happened.

> **Requires `py7zr`** for `7z`/`ppmd`: `pip install py7zr` (not bundled).
> On systems with externally-managed Python, use
> `uv pip install --system --break-system-packages py7zr` or a venv.
> Without it, both methods fall back to ZIP_LZMA with a warning.

It refuses to zip a directory missing `graph.json` (catches the "empty
rebuild" trap) and pre-deletes the target so partial output doesn't
linger.

After recompress, `graphify-out/` stays on disk (gitignored). You can
leave it or delete it; subsequent queries should use Path 1 (zip
directly) so the working directory stays clean.

## Decision flow

When invoked, classify the user's intent and pick one path:

| User intent | Path |
|---|---|
| "where / explain / path / providers / find" question | Path 1 (query, no extract — `explain` first, `find` only if miss) |
| About to run `/graphify <path>` etc. | Path 2 → run `/graphify …` → Path 3 |
| "comprimí" / "zip" alone, with `graphify-out/` present | Path 3 only |
| "descomprimí" / "unzip" alone | Path 2 only |
| `graphify-out/` missing AND zip missing | Stop — nothing to do; ask user |

Never run Path 3 without Path 2 having happened first in the same
session (or `graphify-out/` already existing) — the wrapper refuses
anyway, but surface why.

Never run Path 2 without intent to rebuild — orphan directories cause
the "already exists" guardrail above.

## Sanity check after recompress

The wrapper prints the new archive size. To verify the inner path:

```bash
python3 -c "import zipfile; z=zipfile.ZipFile('graphify-out.zip'); print('graphify-out/graph.json' in z.namelist())"
```

If it prints `False`, do NOT commit — investigate why the rebuild
produced no graph.

## Optional fallback: 7z

The wrapper covers extract + compress with stdlib only, so `7z` is no
longer required. If a user already has it installed and prefers it,
the equivalent commands are:

```bash
7z x -y graphify-out.zip                                   # extract
rm -f graphify-out.zip && 7z a -tzip -mx=9 -mm=LZMA graphify-out.zip graphify-out   # compress (ZIP_LZMA)

# or 7z native (smaller, produces .7z)
rm -f graphify-out.7z && 7z a -t7z -mx=9 -m0=lzma2 graphify-out.7z graphify-out

# or 7z PPMd (smallest for text/JSON)
rm -f graphify-out.7z && 7z a -t7z -m0=PPMd:o=32:mem=29 graphify-out.7z graphify-out
```

Both produce byte-compatible archives with the wrapper output.

## What this skill does NOT do

- Does not invoke `/graphify` itself. That is the user's call (or the
  graphify skill at `~/.claude/skills/graphify/SKILL.md`).
- Does not commit the resulting zip. Commit policy is per-repo (in
  agent-charly, see CLAUDE.md §7 — Conventional Commits, scoped stage).
- Does not extract on read-only queries. Path 1 reads the zip directly.

## Repo-specific notes

**agent-charly** (`/mnt/c/repos/agent-charly`): the extract/recompress
pair is mandated by `CLAUDE.md` §5. `graphify-out/` is gitignored;
`graphify-out.zip` is the committed artifact. The repo also ships its
own `tools/graph_query.py`; either that or this skill's wrapper works —
prefer the wrapper for consistency across repos that adopt the same
pattern.
