"""The file types Nexus accepts and how they are named (FS-004)."""

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT = "text/plain"
MARKDOWN = "text/markdown"

# Extension (lower case, no dot) -> MIME type the content must sniff as.
EXTENSIONS: dict[str, str] = {"pdf": PDF, "docx": DOCX, "txt": TEXT, "md": MARKDOWN}
