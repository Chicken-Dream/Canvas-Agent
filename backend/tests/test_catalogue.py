from __future__ import annotations

import httpx
import pytest

import app.catalogue as catalogue_mod
from app.catalogue import fetch_course_description, parse_subject_and_number


@pytest.mark.parametrize(
    "course_code, course_name, expected",
    [
        ("CMPUT 300 LEC X01 - Fall 2026 - COMPUTERS AND SOCIETY", None, ("cmput", "300")),
        ("CMPUT 301 - Fall 2026", None, ("cmput", "301")),
        ("CMPUT 379 LEC A1 - CMPUT 379 LEC EA1 - Fall 2026", None, ("cmput", "379")),
        (None, "CMPUT 365 LEC A1 - Fall 2026 - REINFORCEMENT LEARNING", ("cmput", "365")),
        ("", "", None),
        (None, None, None),
    ],
)
def test_parse_subject_and_number(course_code, course_name, expected):
    assert parse_subject_and_number(course_code, course_name) == expected


CATALOGUE_HTML = """
<div class="container">
  <h1 class="mb-1">CMPUT 379 - Operating System Concepts</h1>
  <h2 class="fs-5 mb-1">3 units (fi 6)(EITHER, 3-0-3)</h2>
  <p>Faculty of Science</p>
  <p>Introduction to the structure, components, and concepts behind modern operating systems.</p>
  <div class="row">Term info here</div>
</div>
"""


class _FakeResponse:
    text = CATALOGUE_HTML
    status_code = 200

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return _FakeResponse()


class _RaisingClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        raise httpx.ConnectError("simulated failure")


async def test_fetch_course_description_parses_real_page_structure(monkeypatch):
    monkeypatch.setattr(catalogue_mod.httpx, "AsyncClient", _FakeClient)

    result = await fetch_course_description("cmput", "379")

    assert result["title"] == "CMPUT 379 - Operating System Concepts"
    assert "Introduction to the structure" in result["description"]
    assert result["url"] == "https://apps.ualberta.ca/catalogue/course/cmput/379"


async def test_fetch_course_description_handles_fetch_failure(monkeypatch):
    monkeypatch.setattr(catalogue_mod.httpx, "AsyncClient", _RaisingClient)

    result = await fetch_course_description("cmput", "999")

    assert result["title"] is None
    assert result["description"] is None
    assert result["url"] == "https://apps.ualberta.ca/catalogue/course/cmput/999"


async def test_fetch_course_description_falls_back_to_page_text_on_unexpected_structure(monkeypatch):
    class OddStructureResponse:
        text = "<html><body><p>Just some text, no h1 heading at all.</p></body></html>"
        status_code = 200

        def raise_for_status(self):
            pass

    class OddClient(_FakeClient):
        async def get(self, url):
            return OddStructureResponse()

    monkeypatch.setattr(catalogue_mod.httpx, "AsyncClient", OddClient)

    result = await fetch_course_description("cmput", "379")

    assert result["title"] is None
    assert "Just some text" in result["description"]
