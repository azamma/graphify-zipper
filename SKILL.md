---
name: graphify-zipper
description: Compress, decompress, and query the committed `graphify-out.zip` knowledge graph artifact without losing incremental state. Use this skill whenever the user wants to zip/unzip the graphify output, query it (find/explain/path/providers), or is about to invoke any `/graphify` rebuild subcommand (`/graphify <path>`, `/graphify --update`, `/graphify --cluster-only`, `/graphify add`, `/graphify --wiki`). Also trigger when the user says "comprimi el grafo", "descomprimi graphify", "zip graphify", "compress graph output", "tirar query al grafo", "query the graphify zip", or any variation of working with `graphify-out.zip` / `graphify-out/`. Repos that commit `graphify-out.zip` and gitignore `graphify-out/` rely on this lifecycle — skipping extract loses prior cache and incremental state; skipping recompress drops the rebuild from the next commit.
---

# graphify-zipper

Wraps the `graphify-out.zip` ↔ `graphify-out/` lifecycle for repos that
commit the knowledge graph as a BZip2 zip and gitignore the raw directory
(e.g. `agent-charly`).

Three responsibilities:

1. **Read-only queries** — `find`, `explain`, `path`, `providers` against
   the zip directly. No extract, no recompress, no risk.
2. **Pre-rebuild extract** — unzip into `graphify-out/` so the next
   `/graphify` run sees prior cache + incremental manifest.
3. **Post-rebuild recompress** — rebuild the zip from `graphify-out/`
   with BZip2 compression so the commit picks up the new state.

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
$ZIPPER explain <node>                     # node + outgoing/incoming edges (preferred)
$ZIPPER find <english terms> [--limit N]   # top label/source matches
$ZIPPER path <A> <B>                       # shortest path (BFS, undirected)
$ZIPPER providers                          # list provider source files
```

**Use this path when:**
- User asks "where is X / what calls Y / how does Z reach W".
- Any structural/locating question before a Read.
- User explicitly says "tirá una query al grafo" / "query the graph".

**Query strategy — use both together:**
1. Run `explain <term>` first. It gives the richest view (node + all edges + sources).
2. If the term is not found, fall back to `find <term>` to locate close matches, then `explain` the best match.
3. For path-tracing questions, use `path <A> <B>` directly.

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
$ZIPPER compress               # ./graphify-out/ -> graphify-out.zip
$ZIPPER compress --dir /some/path --zip /some/out.zip
```

The wrapper uses `zipfile.ZIP_BZIP2` at level 9 — the same shape the
committed zip already has (BZip2 method, max compression). It refuses
to zip a directory missing `graph.json` (catches the "empty rebuild"
trap) and pre-deletes the target zip so partial output doesn't linger.

After recompress, `graphify-out/` stays on disk (gitignored). You can
leave it or delete it; subsequent queries should use Path 1 (zip
directly) so the working directory stays clean.

## Decision flow

When invoked, classify the user's intent and pick one path:

| User intent | Path |
|---|---|
| "find / where / explain / path / providers" question | Path 1 (query, no extract) |
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
rm -f graphify-out.zip && 7z a -tzip -mx=9 -mm=BZip2 graphify-out.zip graphify-out   # compress
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
