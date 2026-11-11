"""Docs / research extractor: markdown, rst, latex, text, pdf, notebooks.

Builds a section tree (file -> section -> subsection) with one-line summaries,
records links between docs, and keeps per-section term counts and code-like
identifiers so the global pass can mine shared concepts and link docs to code.
"""
import json
import re
from collections import Counter

from ..model import EXTRACTED
from ..textutil import tokens, STOP

HEAD_MD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
LINK_MD = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
WIKI = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
TICK = re.compile(r"`([^`\n]{2,60})`")
IDENT = re.compile(r"\b([a-z]+_[a-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]+|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]+)\b")
SENT = re.compile(r"(?<=[.!?])\s+")
NUM_HEAD = re.compile(r"^(\d+(?:\.\d+){0,2})\.?\s+([A-Z][A-Za-z0-9 ,:&()\-/]{2,80})$")
MAX_CHUNK = 2500


def extract(abs_path, rel, data: bytes, lang):
    raw = None
    if lang in ("docx", "pptx", "xlsx", "csv", "tsv"):
        from . import office_ext
        text, raw = office_ext.to_markdown(rel, data, lang)
        out = _markdown(rel, text, lang)
        out["fulltext"], out["raw_chars"] = text, raw
        if lang in ("xlsx", "csv", "tsv"):   # column names per table, used to link tables & docs
            lines = text.splitlines()
            out["columns"] = {
                n["id"]: re.findall(r"(?m)^- `([^`]+)`:", "\n".join(lines[n["line"] - 1:n.get("end_line") or len(lines)]))
                for n in out["nodes"] if n["kind"] == "section"}
        return out
    if lang == "pdf":
        text = _pdf_text(abs_path)
    elif lang == "notebook":
        return _notebook(rel, data)
    else:
        text = data.decode("utf-8", errors="replace")
    if lang == "rst":
        text = _rst_to_md(text)
    elif lang == "latex":
        text = _latex_to_md(text)
    elif lang in ("text", "pdf"):
        text = _numbered_to_md(text)
    out = _markdown(rel, text, lang)
    if lang == "pdf":
        out["fulltext"] = text   # written to graph-out/text/ so line ranges are readable
        out["raw_chars"] = len(text)
    return out


# ----------------------------------------------------------------------------
def _markdown(rel, text, lang, out=None):
    out = out or {"nodes": [], "edges": [], "pending": [], "text": {}}
    fid = f"file:{rel}"
    lines = text.splitlines()
    sections = []  # (level, title, start_line, [body lines])
    cur = [0, None, 1, []]
    in_code = False
    for i, l in enumerate(lines, 1):
        if l.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEAD_MD.match(l)
        if m:
            sections.append(cur)
            cur = [len(m.group(1)), m.group(2).strip(), i, []]
        else:
            cur[3].append(l)
    sections.append(cur)

    title = next((s[1] for s in sections if s[1]), None)
    intro = "\n".join(sections[0][3])
    out["nodes"].append({"id": fid, "label": rel, "kind": "doc", "file": rel, "line": 1,
                         "end_line": len(lines),
                         "summary": (title + " — " if title else "") + _summary(intro)})

    # split huge untitled bodies (plain text / pdf without headings) into chunks
    expanded = []
    for s in sections:
        body = "\n".join(s[3])
        if len(body) > MAX_CHUNK * 1.5:
            parts = _chunks(s[3])
            for k, (start_off, chunk) in enumerate(parts):
                t = s[1] if s[1] and k == 0 else f"{s[1] or 'part'} ({k + 1}/{len(parts)})"
                expanded.append([s[0] if s[1] else 1, t, s[2] + start_off, chunk])
        else:
            expanded.append(s)

    stack = [(0, fid)]
    used = Counter()
    for idx, (level, head, start, body_lines) in enumerate(expanded):
        body = "\n".join(body_lines)
        if head is None:
            # preamble: attach its text to the file node itself
            _text_features(out, fid, body, rel)
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", head.lower()).strip("-")[:50] or "section"
        used[slug] += 1
        if used[slug] > 1:
            slug = f"{slug}-{used[slug]}"
        sid = f"{rel}#{slug}"
        end = expanded[idx + 1][2] - 1 if idx + 1 < len(expanded) else len(lines)
        out["nodes"].append({"id": sid, "label": head[:100], "kind": "section", "file": rel,
                             "line": start, "end_line": end, "summary": _summary(body)})
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else fid
        out["edges"].append([parent, sid, "contains", EXTRACTED])
        stack.append((level, sid))
        _text_features(out, sid, head + "\n" + body, rel)
    return out


def _text_features(out, nid, body, rel):
    for m in LINK_MD.finditer(body):
        target = m.group(2).split("#")[0]
        if target and not re.match(r"^[a-z]+:", target):
            out["pending"].append({"src": nid, "rel": "links_to", "target": target,
                                   "how": "relpath", "file": rel})
    for m in WIKI.finditer(body):
        out["pending"].append({"src": nid, "rel": "links_to", "target": m.group(1).strip(),
                               "how": "wiki", "file": rel})
    idents = set()
    for m in TICK.finditer(body):
        t = m.group(1).strip().rstrip("()")
        if " " not in t:
            idents.add(t.rsplit("/", 1)[-1])
        for part in re.split(r"[.\s:/]+", t):
            if len(part) >= 3:
                idents.add(part)
    for m in IDENT.finditer(body):
        idents.add(m.group(1))
    prose = re.sub(r"(?m)^\s*\|.*\|\s*$", " ", body)   # markdown table rows are data, not prose
    prose = re.sub(r"(?m)^- `[^`]+`: (?:number|date|category|text|empty)\b.*$", " ", prose)  # column profiles
    words = tokens(re.sub(r"`[^`]*`|\[[^\]]*\]\([^)]*\)|https?://\S+", " ", prose))
    terms = Counter(w for w in words if len(w) >= 4 and w not in STOP)
    # bigrams of adjacent content words: "knowledge graph", "token budget"
    for a, b in zip(words, words[1:]):
        if a not in STOP and b not in STOP and len(a) >= 3 and len(b) >= 3:
            terms[f"{a} {b}"] += 1
    if terms or idents:
        out["text"][nid] = {"terms": dict(terms.most_common(30)), "idents": sorted(idents)[:80]}


def _summary(body):
    body = re.sub(r"```.*?```", " ", body, flags=re.S)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)
    body = LINK_MD.sub(r"\1", body)
    body = re.sub(r"(?m)^\s*\|?[\s:|-]*-{3,}[\s:|-]*$", " ", body)      # table separators
    body = re.sub(r"(?m)^\s*(?:[-*+]|\d+\.)\s+", "", body)               # list markers
    body = re.sub(r"<[^>]+>|[*>|#`]|(?<!\w)_|_(?!\w)", " ", body)          # keep snake_case intact
    body = re.sub(r"\s+", " ", body).strip()
    if not body:
        return ""
    first = SENT.split(body, 1)[0]
    return first[:200]


def _chunks(body_lines):
    parts, buf, size, start = [], [], 0, 0
    for i, l in enumerate(body_lines):
        buf.append(l)
        size += len(l) + 1
        if size >= MAX_CHUNK and not l.strip():
            parts.append((start, buf))
            buf, size, start = [], 0, i + 1
    if buf:
        parts.append((start, buf))
    return parts


# ----------------------------------------------------------------------------
def _rst_to_md(text):
    lines = text.splitlines()
    out, levels = [], []
    i = 0
    while i < len(lines):
        l = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if l.strip() and re.match(r"^([=\-~^\"'`#*+])\1{2,}\s*$", nxt) and len(nxt.strip()) >= len(l.strip()) * 0.6:
            ch = nxt.strip()[0]
            if ch not in levels:
                levels.append(ch)
            out.append("#" * min(6, levels.index(ch) + 1) + " " + l.strip())
            i += 2
            continue
        out.append(l)
        i += 1
    return "\n".join(out)


def _latex_to_md(text):
    rep = [(r"\\(?:chapter)\*?\{([^}]*)\}", "# \\1"), (r"\\section\*?\{([^}]*)\}", "## \\1"),
           (r"\\subsection\*?\{([^}]*)\}", "### \\1"), (r"\\subsubsection\*?\{([^}]*)\}", "#### \\1"),
           (r"\\title\{([^}]*)\}", "# \\1")]
    for a, b in rep:
        text = re.sub(a, b, text)
    text = re.sub(r"(?m)^%.*$", "", text)
    return text


def _numbered_to_md(text):
    """Turn '2.1 Method' style lines (papers, reports) into markdown headings."""
    out, hits = [], 0
    for l in text.splitlines():
        s = l.strip()
        m = NUM_HEAD.match(s)
        if m and len(s) < 90:
            depth = m.group(1).count(".") + 1
            out.append("#" * min(6, depth + 1) + " " + s)
            hits += 1
        elif s.upper() in ("ABSTRACT", "INTRODUCTION", "REFERENCES", "CONCLUSION", "RELATED WORK",
                           "ACKNOWLEDGMENTS", "ACKNOWLEDGEMENTS", "APPENDIX"):
            out.append("## " + s.title())
            hits += 1
        else:
            out.append(l)
    return "\n".join(out)


def _pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        return "(pdf skipped: pip install pypdf to index PDFs)"
    try:
        reader = PdfReader(path)
        pages = []
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                pages.append("")
        text = "\n\n".join(pages)
        # de-hyphenate line breaks
        return re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    except Exception as e:  # corrupt / encrypted pdf
        return f"(pdf unreadable: {e})"


def _notebook(rel, data):
    from . import python_ext
    try:
        nb = json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None
    md, code = [], []
    for c in nb.get("cells", []):
        src = c.get("source", "")
        src = "".join(src) if isinstance(src, list) else src
        if c.get("cell_type") == "markdown":
            md.append(src)
        elif c.get("cell_type") == "code":
            code.append("\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!"))))
    out = python_ext.extract(rel, "\n\n".join(code))
    doc = _markdown(rel, "\n\n".join(md), "markdown")
    file_ids = {n["id"] for n in out["nodes"]}
    for n in doc["nodes"]:
        if n["id"] not in file_ids:
            out["nodes"].append(n)
    for k in ("edges", "pending"):
        out[k].extend(doc[k])
    out["text"].update(doc["text"])
    return out
