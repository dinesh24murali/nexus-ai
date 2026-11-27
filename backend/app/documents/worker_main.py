"""The PDF renderer's child process: `python -m app.documents.worker_main <out.pdf> <max_pages>`.

HTML arrives on stdin, the PDF is written to the path in `argv[1]`, and one JSON line goes to
stdout: `{"pages": n, "denied": k, "served": m}`. It is a separate process so the parent can kill
it at the time limit (a thread cannot be killed) and so it can run with an environment that holds
no secrets (`render.py`). Exit codes: 0 done, 3 over the page limit (nothing written), 1 any
other failure. The fetcher is `DenyAllFetcher`: only bundled files load.
"""

import json
import sys
from pathlib import Path

from app.documents.fetcher import DenyAllFetcher

EXIT_TOO_LONG = 3


def main(argv: list[str]) -> int:
    from weasyprint import HTML  # imported here: loading Pango is the slow part

    out, max_pages = Path(argv[1]), int(argv[2])
    page = sys.stdin.buffer.read().decode("utf-8")
    fetcher = DenyAllFetcher()
    document = HTML(string=page, url_fetcher=fetcher).render()
    pages = len(document.pages)
    if pages > max_pages:
        return EXIT_TOO_LONG
    document.write_pdf(out)
    sys.stdout.write(
        json.dumps({"pages": pages, "denied": fetcher.denied_count, "served": fetcher.served})
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
