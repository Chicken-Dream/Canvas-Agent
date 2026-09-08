from __future__ import annotations

import httpx
import pytest

from app.canvas import outline as outline_mod
from app.canvas.outline import _fetch_external_text as real_fetch_external_text
from app.canvas.outline import detect_course_outline


class FakeCanvasClient:
    """Stands in for CanvasClient in outline-detection tests: no network,
    just canned responses for the three sources detect_course_outline reads.
    """

    def __init__(
        self,
        modules=None,
        pages=None,
        page_bodies=None,
        files=None,
        forbid_modules=False,
        forbid_pages=False,
    ):
        self._modules = modules or []
        self._pages = pages or []
        self._page_bodies = page_bodies or {}
        self._files = files or {}
        self._forbid_modules = forbid_modules
        self._forbid_pages = forbid_pages

    async def list_modules_with_items(self, course_id):
        if self._forbid_modules:
            raise AssertionError("list_modules_with_items should not have been called")
        return self._modules

    async def list_pages(self, course_id):
        if self._forbid_pages:
            raise AssertionError("list_pages should not have been called")
        return self._pages

    async def get_page(self, course_id, url_or_slug):
        return self._page_bodies.get(url_or_slug)

    async def get_file(self, file_id):
        return self._files.get(file_id)


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """detect_course_outline calls out to the public internet when it finds
    an external_url with no fetched_content yet. Replace that with a canned
    value so tests never depend on network access.
    """

    async def fake_fetch(url):
        return f"FETCHED::{url}"

    monkeypatch.setattr(outline_mod, "_fetch_external_text", fake_fetch)


async def test_syllabus_body_with_labeled_external_link_wins():
    course = {
        "id": 1,
        "syllabus_body": '<p>Grading is on the <a href="https://prof.github.io/course/">Course Outline</a>.</p>',
    }
    client = FakeCanvasClient(forbid_modules=True, forbid_pages=True)

    candidate = await detect_course_outline(client, course)

    assert candidate.source == "syllabus_body"
    assert candidate.external_url == "https://prof.github.io/course/"
    assert candidate.confidence == 0.9
    assert candidate.fetched_content == "FETCHED::https://prof.github.io/course/"


async def test_syllabus_body_with_unlabeled_external_link_is_lower_confidence():
    course = {
        "id": 1,
        "syllabus_body": '<p>More info <a href="https://example.com/info">here</a>.</p>',
    }
    client = FakeCanvasClient()

    candidate = await detect_course_outline(client, course)

    assert candidate.source == "syllabus_body"
    assert candidate.external_url == "https://example.com/info"
    assert candidate.confidence == 0.6


async def test_syllabus_body_keyword_only_no_link():
    # Confidence 0.4 is below the 0.9 early-exit threshold, so modules/pages
    # are still searched (and come back empty) before this stays the winner.
    course = {
        "id": 1,
        "syllabus_body": "<p>This course follows the syllabus posted separately.</p>",
    }
    client = FakeCanvasClient()

    candidate = await detect_course_outline(client, course)

    assert candidate.source == "syllabus_body"
    assert candidate.external_url is None
    assert candidate.confidence == 0.4
    assert "syllabus posted separately" in candidate.fetched_content


async def test_module_external_url_item_is_highest_confidence():
    course = {"id": 1, "syllabus_body": None}
    client = FakeCanvasClient(
        modules=[
            {
                "items": [
                    {
                        "title": "Course Outline",
                        "type": "ExternalUrl",
                        "external_url": "https://ext.example.com/outline",
                        "html_url": "https://canvas.test/courses/1/modules/items/1",
                    }
                ]
            }
        ],
        forbid_pages=True,
    )

    candidate = await detect_course_outline(client, course)

    assert candidate.source == "module_item"
    assert candidate.confidence == 0.95
    assert candidate.external_url == "https://ext.example.com/outline"
    assert candidate.fetched_content == "FETCHED::https://ext.example.com/outline"


async def test_module_page_item_with_external_link():
    course = {"id": 1, "syllabus_body": None}
    client = FakeCanvasClient(
        modules=[
            {
                "items": [
                    {"title": "Syllabus", "type": "Page", "page_url": "syllabus-page"},
                ]
            }
        ],
        page_bodies={
            "syllabus-page": {
                "body": '<a href="https://ext.example.com/pdf">Download</a>',
                "html_url": "https://canvas.test/courses/1/pages/syllabus-page",
            }
        },
        forbid_pages=True,
    )

    candidate = await detect_course_outline(client, course)

    assert candidate.source == "module_item"
    assert candidate.confidence == 0.9
    assert candidate.external_url == "https://ext.example.com/pdf"


async def test_module_page_item_without_link_uses_page_text():
    # Confidence 0.7 is below the 0.9 early-exit threshold, so the Pages
    # list is still searched afterwards (and comes back empty here).
    course = {"id": 1, "syllabus_body": None}
    client = FakeCanvasClient(
        modules=[
            {"items": [{"title": "Course Outline", "type": "Page", "page_url": "outline-page"}]}
        ],
        page_bodies={
            "outline-page": {
                "body": "<p>Grading breakdown lives here, no external link.</p>",
                "html_url": "https://canvas.test/courses/1/pages/outline-page",
            }
        },
    )

    candidate = await detect_course_outline(client, course)

    assert candidate.source == "module_item"
    assert candidate.confidence == 0.7
    assert candidate.external_url is None
    assert "Grading breakdown" in candidate.fetched_content


async def test_module_file_item_is_medium_confidence():
    # Confidence 0.5 is below the 0.9 early-exit threshold, so the Pages
    # list is still searched afterwards (and comes back empty here).
    course = {"id": 1, "syllabus_body": None}
    client = FakeCanvasClient(
        modules=[
            {
                "items": [
                    {
                        "title": "Syllabus",
                        "type": "File",
                        "html_url": "https://canvas.test/courses/1/files/9",
                    }
                ]
            }
        ],
    )

    candidate = await detect_course_outline(client, course)

    assert candidate.source == "module_item"
    assert candidate.confidence == 0.5
    assert candidate.canvas_url == "https://canvas.test/courses/1/files/9"


async def test_falls_back_to_pages_list_when_nothing_else_found():
    course = {"id": 1, "syllabus_body": None}
    client = FakeCanvasClient(
        modules=[],
        pages=[{"title": "Course Outline", "url": "course-outline"}],
        page_bodies={
            "course-outline": {
                "body": '<a href="https://ext.example.com/x">Outline</a>',
                "html_url": "https://canvas.test/courses/1/pages/course-outline",
            }
        },
    )

    candidate = await detect_course_outline(client, course)

    assert candidate.source == "page"
    assert candidate.confidence == 0.85
    assert candidate.external_url == "https://ext.example.com/x"


async def test_higher_confidence_source_overrides_lower_one_found_earlier():
    # Syllabus body only scores 0.4 (keyword, no link), but a module item
    # scores 0.95 - the module result must win even though syllabus was
    # checked first.
    course = {
        "id": 1,
        "syllabus_body": "<p>See the syllabus for details.</p>",
    }
    client = FakeCanvasClient(
        modules=[
            {
                "items": [
                    {
                        "title": "Course Outline",
                        "type": "ExternalUrl",
                        "external_url": "https://ext.example.com/outline",
                        "html_url": "https://canvas.test/x",
                    }
                ]
            }
        ],
        forbid_pages=True,
    )

    candidate = await detect_course_outline(client, course)

    assert candidate.confidence == 0.95
    assert candidate.source == "module_item"


async def test_high_confidence_syllabus_hit_short_circuits_module_and_page_search():
    course = {
        "id": 1,
        "syllabus_body": '<p><a href="https://prof.github.io/course/">Course Outline</a></p>',
    }
    # These would raise AssertionError if ever called - proves the >=0.9
    # early-exit in detect_course_outline actually skips extra API calls.
    client = FakeCanvasClient(forbid_modules=True, forbid_pages=True)

    candidate = await detect_course_outline(client, course)

    assert candidate.confidence == 0.9


async def test_nothing_found_returns_none_source_without_network_fetch():
    course = {"id": 1, "syllabus_body": None}
    client = FakeCanvasClient(modules=[], pages=[])

    candidate = await detect_course_outline(client, course)

    assert candidate.source == "none"
    assert candidate.confidence == 0.0
    assert candidate.external_url is None
    assert candidate.fetched_content is None


async def test_fetch_external_text_swallows_network_errors(monkeypatch):
    class RaisingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise httpx.ConnectError("simulated DNS failure")

    monkeypatch.setattr(outline_mod.httpx, "AsyncClient", RaisingAsyncClient)

    result = await real_fetch_external_text("https://unreachable.example.invalid/")

    assert result is None


async def test_resolve_outline_from_url_fetches_canvas_page():
    client = FakeCanvasClient(page_bodies={"course-outline": {"body": "<p>Grading breakdown lives here.</p>"}})

    candidate = await outline_mod.resolve_outline_from_url(
        client, 34230, "https://canvas.ualberta.ca/courses/34230/pages/course-outline?module_item_id=3822853"
    )

    assert candidate.source == "user_submitted"
    assert candidate.confidence == 1.0
    assert candidate.canvas_url == "https://canvas.ualberta.ca/courses/34230/pages/course-outline?module_item_id=3822853"
    assert candidate.external_url is None
    assert "Grading breakdown" in candidate.fetched_content


async def test_resolve_outline_from_url_fetches_external_site():
    client = FakeCanvasClient()
    url = "https://ualberta-cmput301.github.io/general/outline.html"

    candidate = await outline_mod.resolve_outline_from_url(client, 36448, url)

    assert candidate.source == "user_submitted"
    assert candidate.canvas_url is None
    assert candidate.external_url == url
    assert candidate.fetched_content == f"FETCHED::{url}"


async def test_resolve_outline_from_url_extracts_pdf_text(monkeypatch):
    class FakeResponse:
        content = b"%PDF-fake-bytes-not-real"

        def raise_for_status(self):
            pass

    class FakeDownloadClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return FakeResponse()

    class FakePage:
        def extract_text(self):
            return "Grading: Assignment 1 is worth 20% of the final grade."

    class FakePdfReader:
        def __init__(self, stream):
            self.pages = [FakePage()]

    monkeypatch.setattr(outline_mod.httpx, "AsyncClient", FakeDownloadClient)
    monkeypatch.setattr(outline_mod, "PdfReader", FakePdfReader)

    client = FakeCanvasClient(
        files={9029352: {"url": "https://signed.example.com/outline.pdf", "content-type": "application/pdf"}}
    )

    candidate = await outline_mod.resolve_outline_from_url(
        client, 34505, "https://canvas.ualberta.ca/courses/34505/files/9029352?module_item_id=4070086"
    )

    assert candidate.source == "user_submitted"
    assert "20%" in candidate.fetched_content


async def test_resolve_outline_from_url_handles_page_not_found():
    client = FakeCanvasClient(page_bodies={})  # get_page returns None for any slug

    candidate = await outline_mod.resolve_outline_from_url(
        client, 1, "https://canvas.ualberta.ca/courses/1/pages/does-not-exist"
    )

    assert candidate.source == "user_submitted"
    assert candidate.confidence == 1.0
    assert candidate.fetched_content is None
