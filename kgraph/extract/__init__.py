"""Per-file extractors.

Every extractor returns a plain dict (cacheable as JSON):

    {
      "nodes":   [node-dict, ...],
      "edges":   [[src, dst, rel, conf], ...],          # edges fully resolved within the file
      "pending": [{"src", "rel", "target", "how", ...}], # cross-file refs resolved later
      "text":    {node_id: {"terms": {...}, "idents": [...]}}  # for concept mining / doc<->code links
    }
"""
import os

from . import python_ext, generic_ext, docs_ext

CODE_EXT = {
    ".py": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "ts", ".tsx": "ts",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".cs": "csharp", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".scala": "scala", ".sh": "shell", ".bash": "shell", ".lua": "lua", ".r": "r", ".R": "r",
    ".sql": "sql",
}
DOC_EXT = {".md": "markdown", ".markdown": "markdown", ".mdx": "markdown", ".rst": "rst",
           ".txt": "text", ".pdf": "pdf", ".ipynb": "notebook", ".tex": "latex", ".org": "text",
           ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".csv": "csv", ".tsv": "tsv"}
# documents converted to text and saved under graph-out/text/ (line ranges point there)
SIDECAR_EXT = (".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".csv", ".tsv")
# old binary Office formats we can't read without extra software
LEGACY_EXT = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}


def language_of(path: str):
    ext = os.path.splitext(path)[1]
    if ext in CODE_EXT:
        return "code", CODE_EXT[ext]
    if ext.lower() in DOC_EXT:
        return "doc", DOC_EXT[ext.lower()]
    return None, None


def extract_file(abs_path: str, rel: str, data: bytes):
    kind, lang = language_of(rel)
    if kind == "code":
        text = data.decode("utf-8", errors="replace")
        if lang == "python":
            return python_ext.extract(rel, text)
        return generic_ext.extract(rel, text, lang)
    if kind == "doc":
        return docs_ext.extract(abs_path, rel, data, lang)
    return None
