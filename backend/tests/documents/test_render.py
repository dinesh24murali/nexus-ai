"""The real renderer: a PDF comes out, and nothing outside the bundled files is ever loaded.

These use real WeasyPrint in its child process, no mocks. rt-23 (doc 09): hostile content in the
material must produce a PDF with no file contents, no external request and no active links."""

import http.server
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pypdf
import pytest
from app.documents import render
from app.documents.draft import DocumentDraft, Section, Source, clean_draft
from app.documents.markup import document_html
from app.documents.render import RenderError, render_pdf

CANARY = "CANARY-7d1f4a-file-contents"


def pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() for page in pypdf.PdfReader(str(path)).pages)


def link_targets(path: Path) -> list[str]:
    targets: list[str] = []
    for page in pypdf.PdfReader(str(path)).pages:
        for annotation in page.get("/Annots", []):
            action = annotation.get_object().get("/A")
            if action is not None:
                targets.append(str(action.get_object().get("/URI", action.get_object())))
    return targets


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "secret.txt"
    path.write_text(CANARY, encoding="utf-8")
    return path


class _Counter(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []  # noqa: RUF012 - one class per fixture, reset below

    def do_GET(self) -> None:
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/css")
        self.end_headers()
        self.wfile.write(b"body{color:red}")

    def log_message(self, *args: object) -> None:  # keep the test output clean
        pass


@pytest.fixture
def listener() -> Iterator[tuple[str, list[str]]]:
    handler = type("Counter", (_Counter,), {"hits": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", handler.hits  # type: ignore[attr-defined]
    server.shutdown()
    server.server_close()


def good_draft() -> DocumentDraft:
    return clean_draft(
        DocumentDraft(
            title="Quarterly review",
            sections=[
                Section(heading="Findings", body_md="Churn fell to **3.1%** [1].\n\n- one\n- two"),
                Section(heading="Next steps", body_md="Hire two engineers."),
            ],
            sources=[Source(index=1, title="Q3 report", url="https://example.com/q3")],
        )
    )


async def test_a_draft_becomes_a_readable_pdf(tmp_path: Path) -> None:
    out = tmp_path / "doc.pdf"
    result = await render_pdf(document_html(good_draft(), created_on=date(2026, 11, 22)), out)

    assert result.pages == 1
    assert result.denied == 0
    assert result.size_bytes == out.stat().st_size > 1000
    text = pdf_text(out)
    assert "Quarterly review" in text
    assert "Churn fell to 3.1% [1]." in text
    assert "https://example.com/q3" in text  # a web source is printed in full
    assert not (tmp_path / "doc.pdf.part").exists()


async def test_the_stylesheet_is_applied(tmp_path: Path) -> None:
    # The page links the bundled stylesheet by name: if the fetcher did not serve it, the title
    # would be a plain 2em heading and the file smaller. Compare with a page without it.
    page = document_html(good_draft(), created_on=date(2026, 11, 22))
    styled, bare = tmp_path / "styled.pdf", tmp_path / "bare.pdf"
    await render_pdf(page, styled)
    await render_pdf(
        page.replace('<link rel="stylesheet" href="nexus-bundled:document.css">', ""), bare
    )
    assert styled.read_bytes() != bare.read_bytes()


HOSTILE_BODIES = [
    "![x](file://{secret})",
    '<img src="file://{secret}">',
    "![x]({net}/probe.png)",
    '<img src="{net}/probe.png">',
    '<link rel="stylesheet" href="{net}/x.css">',
    '<style>@import url("{net}/x.css"); body{{background:url({net}/bg.png)}}</style>',
    '<div style="background:url({net}/bg.png)">x</div>',
    '<iframe src="file://{secret}"></iframe>',
    "[click](javascript:alert(1)) and [data](data:text/html;base64,PHNjcmlwdD4=)",
    '<a href="javascript:alert(1)">js</a>',
]


@pytest.mark.parametrize("body", HOSTILE_BODIES)
async def test_hostile_material_fetches_nothing_and_reads_no_file(
    body: str, tmp_path: Path, secret_file: Path, listener: tuple[str, list[str]]
) -> None:
    """rt-23: the sanitiser strips it, so the renderer never even tries to load anything."""
    net, hits = listener
    text = body.format(secret=secret_file, net=net)
    draft = clean_draft(
        DocumentDraft(title="Hostile", sections=[Section(heading="S", body_md=text)], sources=[])
    )
    out = tmp_path / "doc.pdf"
    result = await render_pdf(document_html(draft, created_on=date(2026, 11, 22)), out)

    assert result.denied == 0  # documents.external_fetches
    assert hits == []  # nothing reached the network
    assert CANARY not in pdf_text(out)
    assert not any(t.lower().startswith(("javascript", "file", "data")) for t in link_targets(out))


@pytest.mark.parametrize(
    "markup",
    [
        '<img src="file://{secret}">',
        '<img src="{net}/probe.png">',
        '<link rel="stylesheet" href="{net}/x.css">',
        '<style>@import url("{net}/x.css");</style>',
        '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">',
    ],
)
async def test_even_if_markup_got_through_the_fetcher_refuses_everything(
    markup: str, tmp_path: Path, secret_file: Path, listener: tuple[str, list[str]]
) -> None:
    """The second layer, tested on its own: raw HTML handed straight to the renderer."""
    net, hits = listener
    page = (
        "<html><body><p>Visible</p>" + markup.format(secret=secret_file, net=net) + "</body></html>"
    )
    out = tmp_path / "doc.pdf"
    result = await render_pdf(page, out)

    assert (
        result.denied >= 1
    )  # the attempt is counted, and is what documents.external_fetches shows
    assert hits == []
    assert CANARY not in pdf_text(out)
    assert "Visible" in pdf_text(out)


async def test_a_render_that_takes_too_long_is_killed_and_leaves_nothing(tmp_path: Path) -> None:
    out = tmp_path / "doc.pdf"
    with pytest.raises(RenderError) as raised:
        await render_pdf("<html><body>x</body></html>", out, timeout_s=0.01)
    assert raised.value.reason == render.REASON_TIMEOUT
    assert not out.exists()
    assert not (tmp_path / "doc.pdf.part").exists()


async def test_a_document_over_the_page_limit_is_refused(tmp_path: Path) -> None:
    page = "<html><body>" + '<p style="page-break-after: always">page</p>' * 6 + "</body></html>"
    out = tmp_path / "doc.pdf"
    with pytest.raises(RenderError) as raised:
        await render_pdf(page, out, max_pages=3)
    assert raised.value.reason == render.REASON_TOO_LONG
    assert not out.exists()
    assert not (tmp_path / "doc.pdf.part").exists()


async def test_a_child_that_fails_is_a_fixed_reason_with_no_detail(tmp_path: Path) -> None:
    with pytest.raises(RenderError) as raised:
        await render_pdf("<html><body>x</body></html>", tmp_path / "missing-dir" / "doc.pdf")
    assert raised.value.reason == render.REASON_FAILED
    assert str(tmp_path) not in str(raised.value)


async def test_cancelling_the_render_kills_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    killed: list[int] = []
    real_create = asyncio.create_subprocess_exec

    async def spy(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
        process = await real_create(*args, **kwargs)  # type: ignore[arg-type]
        original = process.kill

        def kill() -> None:
            killed.append(process.pid)
            original()

        process.kill = kill  # type: ignore[method-assign]
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    task = asyncio.create_task(render_pdf("<html><body>x</body></html>", tmp_path / "d.pdf"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(killed) == 1
    assert not (tmp_path / "d.pdf").exists()


def test_the_child_gets_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "DATABASE_URL", "AUTH_SECRET", "APP_SECRET", "TAVILY_API_KEY"):
        monkeypatch.setenv(name, "secret-value")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = render.child_env()
    assert env.get("PATH") == "/usr/bin"
    assert not any("secret-value" in value for value in env.values())


# ---------- the child's entry point, in this process (the child itself is not measured)


def test_worker_main_writes_the_pdf_and_reports_pages_and_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io
    import json
    import sys

    from app.documents import worker_main

    out = tmp_path / "x.pdf"
    page = '<html><body><p>Hello</p><img src="http://127.0.0.1:9/x.png"></body></html>'
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(page.encode())})())

    assert worker_main.main(["worker_main", str(out), "30"]) == 0

    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["pages"] == 1 and report["denied"] == 1
    assert "Hello" in pdf_text(out)


def test_worker_main_refuses_a_document_over_the_page_limit_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import sys

    from app.documents import worker_main

    page = "<html><body>" + '<p style="page-break-after: always">p</p>' * 4 + "</body></html>"
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(page.encode())})())

    assert worker_main.main(["worker_main", str(tmp_path / "x.pdf"), "2"]) == 3
    assert not (tmp_path / "x.pdf").exists()
