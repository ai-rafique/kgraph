---
name: kgraph
description: Build and query a knowledge graph of the current project (code, docs, papers) to answer questions while reading far fewer files. Use when the user types /kgraph, asks about the structure of a codebase or a folder of documents, or whenever graph-out/GRAPH_REPORT.md exists and you need to find where something lives.
---

# kgraph

kgraph turns a folder into `graph-out/` containing:
- `GRAPH_REPORT.md`: a compact overview (core nodes, communities, concepts, directory map)
- `graph.json`: the full graph (queried by the CLI)
- `graph.html`: an interactive view for the human

## Build or refresh
`/kgraph` or `/kgraph <path>` → run `python -m kgraph build <path or .>`. Re-runs are incremental (SHA256 cache), so run it again after large edits.
Add `--llm anthropic|openai|ollama` only if the user asks for deeper doc/paper extraction (it costs API calls).

## Answering questions: graph first, files last
1. If `graph-out/GRAPH_REPORT.md` exists, read it once per session.
2. For any "where / how / what calls / what is" question, run
   `python -m kgraph query "<question>"` (add `--budget 3000` for broad questions).
3. Use `python -m kgraph explain <name>` for one symbol's neighbours and `python -m kgraph path <a> <b>` to see how two things connect.
4. Open only the `file:start-end` ranges the output lists, using Read with offset/limit. Do not read whole files when a range is given.
5. Fall back to grep only if the graph has no match. Edges marked `~` are inferred and need checking in the source.
