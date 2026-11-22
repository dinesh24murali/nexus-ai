"""Generated PDF documents (TASK-041, docs/02_outputs/08 sections 2.5 and 5, doc 10 LLM-T18).

`draft.py` is the structured document and what is gathered for it, `markup.py` turns untrusted
Markdown into sanitised HTML, `render.py` runs WeasyPrint in a child process that can be killed,
and `fetcher.py` is the URL fetcher that serves nothing but bundled files. Everything here is pure
or file-only: no database, no model.
"""
