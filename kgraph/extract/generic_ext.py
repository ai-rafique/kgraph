"""Regex-based extractor for non-Python languages.

Not a real parser, but good enough to find definitions, imports, approximate
call edges and (for brace languages) the line span of each definition.
"""
import os
import re

from ..model import EXTRACTED

_ID = r"[A-Za-z_$][\w$]*"

DEFS = {
    "js": [
        (rf"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>{_ID})(?:\s+extends\s+(?P<base>[\w.$]+))?", "class"),
        (rf"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>{_ID})\s*\(", "function"),
        (rf"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>{_ID})\s*(?::[^=]+)?=\s*(?:async\s+)?(?:\([^)]*\)|{_ID})\s*(?::\s*[^=]+)?=>", "function"),
        (rf"^\s*(?:export\s+)?interface\s+(?P<name>{_ID})(?:\s+extends\s+(?P<base>[\w.$]+))?", "interface"),
        (rf"^\s*(?:export\s+)?type\s+(?P<name>{_ID})\s*(?:<[^>]*>)?\s*=", "type"),
        (rf"^\s+(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set)\s+)*\*?(?P<name>#?(?!(?:if|for|while|switch|catch|return|function|else)\b){_ID})\s*(?:<[^()]*>)?\s*\([^;]*\)\s*(?::\s*[^{{;]+)?\{{\s*$", "method"),
    ],
    "go": [
        (rf"^func\s+\((?P<recv>[^)]*)\)\s*(?P<name>{_ID})\s*\(", "method"),
        (rf"^func\s+(?P<name>{_ID})\s*[\[(]", "function"),
        (rf"^type\s+(?P<name>{_ID})\s+(?:struct|interface)", "class"),
    ],
    "rust": [
        (rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(?P<name>{_ID})", "function"),
        (rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|union)\s+(?P<name>{_ID})", "class"),
        (rf"^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+(?P<name>{_ID})", "interface"),
        (rf"^\s*impl(?:<[^>]*>)?\s+(?P<base>{_ID})(?:<[^>]*>)?\s+for\s+(?P<name>{_ID})", "impl"),
    ],
    "java": [
        (rf"^\s*(?:(?:public|private|protected|abstract|final|static|sealed)\s+)*(?:class|interface|enum|record)\s+(?P<name>{_ID})(?:<[^>]*>)?(?:\s+extends\s+(?P<base>[\w.]+))?", "class"),
        (rf"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|override|async|virtual|internal)\s+)+[\w<>\[\],.? ]+\s+(?P<name>{_ID})\s*\([^;]*$", "method"),
    ],
    "kotlin": [
        (rf"^\s*(?:(?:data|sealed|open|abstract|private|internal|public)\s+)*(?:class|interface|object)\s+(?P<name>{_ID})", "class"),
        (rf"^\s*(?:(?:private|public|internal|override|suspend|inline|open)\s+)*fun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?(?P<name>{_ID})\s*\(", "function"),
    ],
    "c": [
        (rf"^\s*(?:typedef\s+)?struct\s+(?P<name>{_ID})\s*\{{", "class"),
        (rf"^(?!\s*(?:if|for|while|switch|return|else)\b)[\w\s\*&:<>,~]*?\b(?P<name>{_ID})\s*\([^;]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{{?\s*$", "function"),
    ],
    "ruby": [
        (rf"^\s*class\s+(?P<name>[\w:]+)(?:\s*<\s*(?P<base>[\w:]+))?", "class"),
        (rf"^\s*module\s+(?P<name>[\w:]+)", "class"),
        (rf"^\s*def\s+(?:self\.)?(?P<name>[\w?!=]+)", "function"),
    ],
    "php": [
        (rf"^\s*(?:abstract\s+|final\s+)?class\s+(?P<name>{_ID})(?:\s+extends\s+(?P<base>[\w\\]+))?", "class"),
        (rf"^\s*(?:(?:public|private|protected|static)\s+)*function\s+(?P<name>{_ID})\s*\(", "function"),
    ],
    "swift": [
        (rf"^\s*(?:(?:public|private|internal|open|final)\s+)*(?:class|struct|protocol|enum|extension)\s+(?P<name>{_ID})", "class"),
        (rf"^\s*(?:(?:public|private|internal|open|static|override|mutating)\s+)*func\s+(?P<name>{_ID})", "function"),
    ],
    "scala": [
        (rf"^\s*(?:case\s+)?(?:class|object|trait)\s+(?P<name>{_ID})", "class"),
        (rf"^\s*(?:override\s+)?def\s+(?P<name>{_ID})", "function"),
    ],
    "shell": [(rf"^\s*(?:function\s+)?(?P<name>[\w-]+)\s*\(\)\s*\{{?", "function")],
    "lua": [(rf"^\s*(?:local\s+)?function\s+(?P<name>[\w.:]+)\s*\(", "function")],
    "r": [(rf"^\s*(?P<name>[\w.]+)\s*(?:<-|=)\s*function\s*\(", "function")],
    "sql": [
        (r"(?i)^\s*create\s+(?:or\s+replace\s+)?(?:table|view|materialized\s+view)\s+(?:if\s+not\s+exists\s+)?(?P<name>[\w.\"`]+)", "table"),
        (r"(?i)^\s*create\s+(?:or\s+replace\s+)?(?:function|procedure)\s+(?P<name>[\w.\"`]+)", "function"),
    ],
}
DEFS["ts"] = DEFS["js"]
DEFS["csharp"] = DEFS["java"]
DEFS["cpp"] = DEFS["c"] + [(rf"^\s*(?:class|struct)\s+(?P<name>{_ID})(?:\s*:\s*(?:public|private|protected)?\s*(?P<base>[\w:]+))?", "class")]

IMPORTS = {
    "js": [r"""^\s*import\s+(?:[^'"]*?\s+from\s+)?['"](?P<t>[^'"]+)['"]""", r"""require\(\s*['"](?P<t>[^'"]+)['"]\s*\)""",
           r"""^\s*export\s+[^'"]*\s+from\s+['"](?P<t>[^'"]+)['"]"""],
    "go": [r'^\s*(?:import\s+)?(?:\w+\s+)?"(?P<t>[\w./-]+)"\s*$'],
    "rust": [r"^\s*(?:pub\s+)?use\s+(?P<t>[\w:]+)", r"^\s*(?:pub\s+)?mod\s+(?P<t>\w+)\s*;"],
    "java": [r"^\s*import\s+(?:static\s+)?(?P<t>[\w.]+)"],
    "kotlin": [r"^\s*import\s+(?P<t>[\w.]+)"],
    "csharp": [r"^\s*using\s+(?:static\s+)?(?P<t>[\w.]+)\s*;"],
    "c": [r'^\s*#\s*include\s+[<"](?P<t>[^>"]+)[>"]'],
    "ruby": [r"""^\s*require(?:_relative)?\s+['"](?P<t>[^'"]+)['"]"""],
    "php": [r"^\s*use\s+(?P<t>[\w\\]+)", r"""^\s*(?:require|include)(?:_once)?\s*\(?\s*['"](?P<t>[^'"]+)['"]"""],
    "swift": [r"^\s*import\s+(?P<t>\w+)"],
    "scala": [r"^\s*import\s+(?P<t>[\w.]+)"],
    "shell": [r"^\s*(?:source|\.)\s+(?P<t>\S+)"],
    "lua": [r"""require\s*\(?\s*['"](?P<t>[^'"]+)['"]"""],
    "r": [r"""^\s*(?:library|require|source)\s*\(\s*['"]?(?P<t>[^'")]+)['"]?\s*\)"""],
    "sql": [],
}
IMPORTS["ts"] = IMPORTS["js"]
IMPORTS["cpp"] = IMPORTS["c"]

BRACE_LANGS = {"js", "ts", "go", "rust", "java", "kotlin", "csharp", "c", "cpp", "php", "swift", "scala", "shell"}
KEYWORDS = set("""if for while switch catch return function new typeof sizeof await yield else do try
throw case delete void in of let var const class super this self print println printf fmt len make
append panic require import defined not and or elif match fn func def end""".split())
CALL_RE = re.compile(rf"\b({_ID})\s*\(")
STR_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`[^`]*`')
LINE_COMMENT = re.compile(r"(//|#(?!\s*include)).*$")


def _strip(line, lang):
    line = STR_RE.sub('""', line)
    if lang in ("ruby", "shell", "r", "python"):
        return re.sub(r"#.*$", "", line)
    if lang == "sql":
        return re.sub(r"--.*$", "", line)
    return re.sub(r"//.*$", "", line)


def extract(rel: str, text: str, lang: str):
    out = {"nodes": [], "edges": [], "pending": [], "text": {}}
    fid = f"file:{rel}"
    lines = text.splitlines()
    out["nodes"].append({"id": fid, "label": rel, "kind": "file", "file": rel, "line": 1,
                         "end_line": len(lines), "summary": _leading_comment(lines)})
    defs = [(re.compile(p), k) for p, k in DEFS.get(lang, [])]
    imps = [re.compile(p) for p in IMPORTS.get(lang, [])]
    brace = lang in BRACE_LANGS

    depth = 0
    open_syms = []            # stack of (node_dict, depth_at_open)
    current = fid             # symbol that receives call edges
    current_class = None
    seen_calls = {}
    in_block_comment = False

    for i, raw in enumerate(lines, 1):
        line = raw
        if brace:
            if in_block_comment:
                if "*/" in line:
                    line = line.split("*/", 1)[1]
                    in_block_comment = False
                else:
                    continue
            if "/*" in line and "*/" not in line.split("/*", 1)[1]:
                line = line.split("/*", 1)[0]
                in_block_comment = True
        code = _strip(line, lang)

        for rx in imps:
            m = rx.search(raw)
            if m:
                out["pending"].append({"src": fid, "rel": "imports", "target": m.group("t"),
                                       "how": "path", "lang": lang, "file": rel})

        for rx, kind in defs:
            m = rx.search(code)
            if not m:
                continue
            name = m.group("name").strip('"`')
            if name in KEYWORDS:
                continue
            if kind == "impl":       # rust: impl Trait for Type
                out["pending"].append({"src": f"{rel}::{name}", "rel": "implements",
                                       "target": m.group("base"), "how": "name", "file": rel})
                break
            parent = fid
            label = name
            if kind == "method" and lang == "go":
                recv = re.findall(_ID, m.group("recv") or "")
                if recv:
                    label = f"{recv[-1]}.{name}"
            elif kind in ("method", "function") and current_class and open_syms and \
                    any(s[0]["id"] == current_class for s in open_syms):
                parent = current_class
                label = f"{current_class.split('::', 1)[1]}.{name}"
                kind = "method"
            elif kind == "method":
                kind = "function"   # a "method" pattern outside a class -> function
            nid = f"{rel}::{label}"
            nd = {"id": nid, "label": label, "kind": kind, "file": rel, "line": i,
                  "summary": _comment_above(lines, i - 1)}
            out["nodes"].append(nd)
            out["edges"].append([parent, nid, "defines", EXTRACTED])
            gb = m.groupdict()
            if gb.get("base"):
                out["pending"].append({"src": nid, "rel": "inherits", "target": gb["base"].split(".")[-1],
                                       "how": "name", "file": rel})
            if brace:
                open_syms.append((nd, depth))
            if kind in ("class", "interface"):
                current_class = nid
            current = nid
            break

        # call edges for the current symbol
        if current != fid or not brace:
            for cm in CALL_RE.finditer(code):
                name = cm.group(1)
                if name in KEYWORDS or len(name) < 2:
                    continue
                key = (current, name)
                if key not in seen_calls and not current.endswith(f"::{name}") and not current.endswith(f".{name}"):
                    seen_calls[key] = 1
                    out["pending"].append({"src": current, "rel": "calls", "target": name,
                                           "how": "name", "file": rel})

        if brace:
            depth += code.count("{") - code.count("}")
            while open_syms and depth <= open_syms[-1][1] and ("}" in code):
                nd, _ = open_syms.pop()
                nd["end_line"] = i
                if nd["id"] == current_class:
                    current_class = None
                current = open_syms[-1][0]["id"] if open_syms else fid
    return out


def _leading_comment(lines):
    buf = []
    for l in lines[:25]:
        s = l.strip()
        if not s and not buf:
            continue
        if s.startswith(("//", "#", "/*", "*", "--", '"""')) and not s.startswith(("#include", "#!", "#pragma")):
            t = s.lstrip("/#*-\" ").strip()
            if re.search(r"(?i)copyright|licen[cs]e|spdx|all rights reserved|governed by", t):
                buf = []
                continue
            if t:
                buf.append(t)
            if len(buf) >= 2:
                break
        elif buf or s:
            break
    return " ".join(buf)[:160]


def _comment_above(lines, idx):
    buf = []
    j = idx - 1
    while j >= 0 and len(buf) < 3:
        s = lines[j].strip()
        if s.startswith(("//", "#", "*", "/*", "///", "--")) and not s.startswith("#include"):
            t = s.lstrip("/#*-! ").strip()
            if t and not t.startswith("@"):
                buf.insert(0, t)
            j -= 1
        else:
            break
    return " ".join(buf)[:160]
