"""PDF / Word / other documents -> Markdown.

Uses `docling` when installed (best layout + table fidelity). Without it we fall back to
`pypdf` / `python-docx`, which keeps the image light and still works for text-heavy lore.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

TEXT_EXT = {".md", ".markdown", ".txt"}
DOC_EXT = {".pdf", ".docx", ".doc", ".pptx", ".html", ".htm", ".odt", ".rtf"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm", ".mp4"}


def docling_available() -> bool:
    try:
        import docling  # noqa: F401
        return True
    except ImportError:
        return False


def to_markdown(path: str | Path) -> tuple[str, str]:
    """Return (markdown, converter_name)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in TEXT_EXT:
        return p.read_text(encoding="utf-8", errors="replace"), "text"
    if docling_available():
        from docling.document_converter import DocumentConverter

        result = DocumentConverter().convert(str(p))
        return result.document.export_to_markdown(), "docling"
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        return "\n\n".join(f"## Page {i + 1}\n\n{t}" for i, t in enumerate(pages) if t), "pypdf"
    if ext == ".docx":
        import docx

        d = docx.Document(str(p))
        lines = []
        for para in d.paragraphs:
            style = (para.style.name or "").lower() if para.style is not None else ""
            text = para.text.strip()
            if not text:
                continue
            if style.startswith("heading"):
                level = "".join(ch for ch in style if ch.isdigit()) or "2"
                lines.append("#" * min(int(level), 4) + " " + text)
            else:
                lines.append(text)
        return "\n\n".join(lines), "python-docx"
    if ext in (".html", ".htm"):
        import re

        html = p.read_text(encoding="utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", html), "html-strip"
    raise ValueError(f"Cannot convert {p.name}: install docling for {ext} support "
                     "(set INSTALL_DOCLING=true and rebuild)")
