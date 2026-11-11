"""Wire kgraph into AI coding assistants."""
import os
from importlib import resources

MARK_START = "<!-- kgraph:start -->"
MARK_END = "<!-- kgraph:end -->"
SECTION = f"""{MARK_START}
## Knowledge graph (kgraph)
This project has a knowledge graph in `graph-out/`. To save tokens:
1. Read `graph-out/GRAPH_REPORT.md` before searching raw files.
2. Run `python -m kgraph query "<question>"` to find relevant code/docs with exact `file:start-end` ranges; open only those ranges.
3. `python -m kgraph explain <name>` shows a symbol's connections; `python -m kgraph path <a> <b>` shows how two things relate.
4. Use grep/glob only when the graph has no answer. After large changes run `python -m kgraph build .` (incremental).
{MARK_END}
"""


def _skill_text():
    return resources.files("kgraph").joinpath("templates/SKILL.md").read_text(encoding="utf-8")


def install_skill(home=None):
    home = home or os.path.expanduser("~")
    d = os.path.join(home, ".claude", "skills", "kgraph")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "SKILL.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(_skill_text())
    return p


def upsert_section(path):
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    if MARK_START in text and MARK_END in text:
        a = text.index(MARK_START)
        b = text.index(MARK_END) + len(MARK_END)
        text = text[:a] + SECTION.strip() + text[b:]
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + SECTION
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def remove_section(path):
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if MARK_START not in text:
        return False
    a = text.index(MARK_START)
    b = text.index(MARK_END) + len(MARK_END)
    with open(path, "w", encoding="utf-8") as f:
        f.write((text[:a] + text[b:]).strip() + "\n")
    return True


TARGETS = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "agents": "AGENTS.md",
    "cursor": os.path.join(".cursor", "rules", "kgraph.mdc"),
    "gemini": "GEMINI.md",
    "copilot": os.path.join(".github", "copilot-instructions.md"),
}


def install_project(platform, root="."):
    rel = TARGETS[platform]
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    if platform == "cursor" and not os.path.exists(p):
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\ndescription: Use the kgraph knowledge graph before searching files\nalwaysApply: true\n---\n")
    return upsert_section(p)
