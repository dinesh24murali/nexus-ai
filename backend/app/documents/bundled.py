"""The files the renderer may load, and their names. No weasyprint import: the API process reads
this too (the page links the stylesheet by its bundled name)."""

from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
STYLESHEET_URL = "nexus-bundled:document.css"

# The complete allowlist: name -> (file, media type). Adding a font or a logo means adding it here.
BUNDLED: dict[str, tuple[str, str]] = {
    STYLESHEET_URL: ("document.css", "text/css"),
}
