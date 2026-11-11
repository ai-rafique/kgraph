# kgraph

Turn a folder of **code, docs, notes and papers** into a knowledge graph your LLM can query, so it reads a 3–4k-token slice instead of dozens of whole files.

Pure Python, no required dependencies, runs locally. Nothing leaves your machine unless you turn on the optional `--llm` pass.

**Quick start (works even where `pip.exe`/script launchers are blocked):**
```
python -m pip install --user .        # run inside the folder that has pyproject.toml
python -m pip install --user pypdf    # optional: PDF support
cd your-project
python -m kgraph init                 # build graph + tell Claude Code to use it
```

Other install options:
```
pip install -e .            # from this folder
pip install -e ".[all]"     # + PDF support (pypdf) and Louvain clustering (networkx)

python -m kgraph build .              # → graph-out/GRAPH_REPORT.md, graph.json, graph.html
python -m kgraph query "how are timeouts configured"
python -m kgraph explain DigestAuth
python -m kgraph path "Token pruning" "Experiments"
```

## What you get

| file | what it is | who reads it |
|---|---|---|
| `graph-out/GRAPH_REPORT.md` | 1–5k-token map: core nodes, communities, concepts, directory map | the LLM, once per session |
| `graph-out/graph.json` | full graph: nodes with `file:line-end` spans, typed edges | `python -m kgraph query/explain/path`, MCP |
| `graph-out/graph.html` | interactive offline graph (search, click to see neighbours) | you |
| `graph-out/text/*.txt` | text extracted from PDF/Word/PowerPoint/Excel/CSV, so line ranges are readable | the LLM |
| `graph-out/cache/` | per-file SHA256 cache; rebuilds only re-parse changed files | — |

## How it saves tokens

1. **Report first.** The LLM reads `GRAPH_REPORT.md` (≈40–60× smaller than the repo) instead of exploring.
2. **Query, don't grep.** `python -m kgraph query` returns the relevant subgraph inside a token budget (default 1500): ranked nodes, the edges between them, and a **“Read next” list of exact line ranges**.
3. **Read ranges, not files.** The LLM opens only `tree.go: 135-249`, not all of `tree.go` plus everything grep matched.

Measured on three real repos (9 questions). Baseline = grep the key terms and read every matching file. kgraph = query output + the suggested ranges:

| repo | question | grep + read | kgraph | saving |
|---|---|---:|---:|---:|
| httpx (Python) | how are timeouts configured | 55,200 | 2,887 | 19× |
| httpx | where is digest auth implemented | 20,804 | 3,482 | 6× |
| httpx | how do proxies get mounted per url | 35,252 | 3,343 | 11× |
| gin (Go) | how does the router tree add a route | 70,246 | 3,759 | 19× |
| gin | how is JSON rendered in a response | 80,478 | 3,310 | 24× |
| gin | binding form data to a struct | 120,014 | 4,447 | 27× |
| ky (TypeScript) | retry logic with backoff delay | 265,452 | 4,328 | 61× |
| ky | how are hooks run before a request | 226,052 | 3,900 | 58× |
| ky | timeout handling | 244,727 | 2,942 | 83× |

Tokens are estimated as characters ÷ 4. The baseline is a deliberately naive agent; a careful agent does better than grep-and-read-all, so real savings are smaller. The gains are largest on big repos and broad questions.

## What goes into the graph

| source | nodes | edges |
|---|---|---|
| Python (stdlib `ast`, exact) | file, class, function, method (+ docstring, line span) | defines, imports (resolved to files/symbols), calls, inherits |
| JS/TS, Go, Rust, Java, Kotlin, C#, C/C++, Ruby, PHP, Swift, Scala, shell, Lua, R, SQL (regex) | same, with line spans for brace languages | defines, imports (relative paths resolved), calls, inherits/implements |
| Markdown, RST, LaTeX, txt, PDF, notebooks | doc → section tree, one-line summaries | contains, links_to (`[x](y.md)`, `[[wiki]]`), mentions → code symbols named in `backticks`/snake_case/camelCase |
| Word `.docx` | headings (Title/Heading 1–6, localized styles) → sections; lists, tables | same as docs |
| PowerPoint `.pptx` | one section per slide (title, bullets, tables, **speaker notes**) | same as docs |
| Excel `.xlsx/.xlsm`, `.csv`, `.tsv` | one section per sheet/table: row × column count, each column's type and range/values, 5 sample rows (never the full data) | `shares_column:<name>` between tables with a common column (e.g. `customer_id`); docs naming a column link to its table |
| across all docs | **concepts**: phrases shared by several sections (e.g. “retrieval augmented generation”) | section ~mentions concept, concept ~about code symbol |

Edges are tagged **EXTRACTED** (read from source) or **INFERRED** (name/text matching or LLM, shown as `~`) so the model knows what to double-check. Communities come from Louvain (if networkx is installed) or a built-in label propagation.

Ignored by default: `.git`, `node_modules`, venvs, build output, minified/generated files, lockfiles, CHANGELOG/LICENSE. Add your own patterns in `.kgraphignore` (gitignore syntax). `.gitignore` is respected too.

## Use it with your AI assistant

**Claude Code skill** (`/kgraph`):
```
python -m kgraph install                 # writes ~/.claude/skills/kgraph/SKILL.md
```
**Always check the graph first:** writes a short marked section into the project's instruction file. Run it again to update it, or add `uninstall` to remove it.
```
python -m kgraph hook claude      # CLAUDE.md
python -m kgraph hook codex       # AGENTS.md
python -m kgraph hook cursor      # .cursor/rules/kgraph.mdc
python -m kgraph hook gemini      # GEMINI.md
python -m kgraph hook copilot     # .github/copilot-instructions.md
```
**MCP server** (tools: `kgraph_query`, `kgraph_explain`, `kgraph_path`, `kgraph_report`; reloads automatically after a rebuild):
```
claude mcp add kgraph -- python -m kgraph serve
# any other MCP client: command "kgraph", args ["serve", "--graph", "/abs/path/graph-out/graph.json"]
```
**Plain chat / local LLMs:** paste `GRAPH_REPORT.md` plus the output of `python -m kgraph query "..."` instead of whole files.

## Optional: LLM extraction for research folders

Adds entities and relations from doc/paper sections (code is never sent). Results are cached by content hash, so re-runs are free.
```
ANTHROPIC_API_KEY=... python -m kgraph build . --llm anthropic        # default model claude-haiku-5-5
OPENAI_API_KEY=...    python -m kgraph build . --llm openai           # or any OpenAI-compatible server:
OPENAI_BASE_URL=http://localhost:1234/v1 python -m kgraph build . --llm openai   # LM Studio, llama.cpp, vLLM…
python -m kgraph build . --llm ollama                                 # local Ollama, default llama3.1
```
Set `KGRAPH_MODEL` to choose the model.

## Commands

```
python -m kgraph build [path] [--out DIR] [--llm PROVIDER] [--no-html]
python -m kgraph query "<question>" [--budget 1500] [--depth 2]
python -m kgraph explain <name>          # id, label, file path, or fuzzy
python -m kgraph path <a> <b>
python -m kgraph stats
python -m kgraph serve [--graph PATH]
python -m kgraph install
python -m kgraph hook <claude|codex|agents|cursor|gemini|copilot> [install|uninstall]
```
`query`, `explain`, `path`, `stats` and `serve` find `graph-out/graph.json` in the current folder or any parent.

## Limits

- Non-Python languages use regex, not a real parser. Nested or multi-line declarations can be missed, and call edges are matched by name (marked `~`).
- Search is lexical (stemmed keywords + graph expansion), not embeddings. Use the words the code or docs use. If a query misses, try `explain` on a name from the report.
- Old binary Office formats (`.doc`, `.xls`, `.ppt`) are skipped with a warning; re-save them as `.docx/.xlsx/.pptx`. Images inside documents, charts, formulas and comments are not read.
- Spreadsheet column types come from the first 500 rows.
- Scanned PDFs need OCR first. PDF headings are detected from numbered lines (“2.1 Method”) and common section names.

## Layout

```
kgraph/
  cli.py          commands
  builder.py      scan, cache, cross-file resolution, concept mining, clustering
  extract/        python_ext (ast), generic_ext (regex langs), docs_ext (md/rst/tex/txt/pdf/ipynb)
  query.py        token-budgeted query / explain / path
  report.py       GRAPH_REPORT.md
  html.py         graph.html
  mcp_server.py   stdio MCP server
  llm.py          optional entity/relation extraction
  install.py      skill + instruction-file hooks
tests/test_smoke.py
```
