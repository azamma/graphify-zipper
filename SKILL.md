---
name: graphify-zipper
description: Compress, decompress, and query the committed `graphify-out.zip` knowledge graph artifact without losing incremental state. Use this skill whenever the user wants to zip/unzip the graphify output, query it (find/explain/path/providers), or is about to invoke any `/graphify` rebuild subcommand (`/graphify <path>`, `/graphify --update`, `/graphify --cluster-only`, `/graphify add`, `/graphify --wiki`). Also trigger when the user says "comprimi el grafo", "descomprimi graphify", "zip graphify", "compress graph output", "tirar query al grafo", "query the graphify zip", or any variation of working with `graphify-out.zip` / `graphify-out/`. Repos that commit `graphify-out.zip` and gitignore `graphify-out/` rely on this lifecycle — skipping extract loses prior cache and incremental state; skipping recompress drops the rebuild from the next commit.
---

# graphify-zipper

Wraps the `graphify-out.zip` ↔ `graphify-out/` lifecycle for repos that
commit the knowledge graph as a BZip2 zip and gitignore the raw directory
(e.g. `agent-charly`).

Three responsibilities:

1. **Read-only queries** — use `tools/graph_query.py` against the zip
   directly. No extract, no recompress, no risk.
2. **Pre-rebuild extract** — unzip into `graphify-out/` so the next
   `/graphify` run sees prior cache + incremental manifest.
3. **Post-rebuild recompress** — rebuild the zip from `graphify-out/`
   with BZip2 compression so the commit picks up the new state.

## System dependency: 7z

Required for compress/extract. Install once:

- Linux: `sudo apt install p7zip-full` (or `pacman -S p7zip`)
- macOS: `brew install p7zip`
- Windows: `winget install 7zip.7zip`

If `7z` is missing, refuse the compress/extract path and tell the user
to install it. Do NOT fall back to `unzip` / `zip` — the committed zip
uses BZip2 method and stock `zip` defaults to deflate, which changes
the archive byte-for-byte and bloats diffs.

## Path 1 — Query without extracting (default for read-only work)

`tools/graph_query.py` reads the zip directly via Python `zipfile`.
Stdlib only, no extraction needed. **Phrase all queries in English** —
node labels and source paths are English; Spanish terms will not match.

```bash
python3 tools/graph_query.py find <english terms>          # top label/source matches
python3 tools/graph_query.py explain <node>                # node + outgoing/incoming edges
python3 tools/graph_query.py path <A> <B>                  # shortest path
python3 tools/graph_query.py providers                     # list provider source files
```

Add `--limit N` to `find` to widen results. Output is JSON-ish; pipe
through `jq` if the user wants filtering.

**Use this path when:**
- User asks "where is X / what calls Y / how does Z reach W".
- Any structural/locating question before a Read.
- User explicitly says "tirá una query al grafo" / "query the graph".

Do NOT extract just to query — wastes I/O and risks accidental
recompress mismatch.

## Path 2 — Extract before a rebuild

Run this immediately BEFORE any `/graphify` subcommand that writes the
graph: `/graphify <path>`, `/graphify --update`, `/graphify --cluster-only`,
`/graphify add <url>`, `/graphify --wiki`.

```bash
7z x -y graphify-out.zip
```

`-y` auto-confirms overwrite. Result: `graphify-out/` directory in cwd
with prior `graph.json`, semantic cache, manifest, etc. Skipping this
step makes the next rebuild start from scratch and discards the
incremental cache.

If `graphify-out/` already exists when extracting, that means a previous
run was not recompressed. Surface this to the user before clobbering —
they may have local rebuild state worth preserving.

## Path 3 — Recompress after a rebuild

Run immediately AFTER the `/graphify` subcommand finishes successfully:

```bash
rm -f graphify-out.zip
7z a -tzip -mx=9 -mm=BZip2 graphify-out.zip graphify-out
```

Flags explained (do not change them — the committed zip must stay
byte-stable so diffs are meaningful):

- `-tzip` → zip container format
- `-mx=9` → maximum compression level
- `-mm=BZip2` → BZip2 compression method (NOT deflate)

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

Never run Path 3 without Path 2 having happened first in the same session
(or `graphify-out/` already existing) — you would zip an empty/stale dir.

Never run Path 2 without intent to rebuild — orphan directories cause
the "already exists" trap above.

## Sanity check after recompress

After Path 3, verify the new zip is non-empty and contains the expected
inner path:

```bash
7z l graphify-out.zip | grep 'graphify-out/graph.json' || echo "WARN: graph.json missing in archive"
```

If the warning fires, do NOT commit — investigate why the rebuild
produced no graph.

## What this skill does NOT do

- Does not invoke `/graphify` itself. That is the user's call (or the
  graphify skill at `~/.claude/skills/graphify/SKILL.md`).
- Does not commit the resulting zip. Commit policy is per-repo (in
  agent-charly, see CLAUDE.md §7 — Conventional Commits, scoped stage).
- Does not extract on read-only queries. Path 1 reads the zip directly.

## Repo-specific notes

**agent-charly** (`/mnt/c/repos/agent-charly`): the extract/recompress
pair is mandated by `CLAUDE.md` §5. `graphify-out/` is gitignored;
`graphify-out.zip` is the committed artifact. `tools/graph_query.py`
lives at repo root. Other repos that adopt the same pattern follow the
same flow — only the location of `graph_query.py` may vary.
