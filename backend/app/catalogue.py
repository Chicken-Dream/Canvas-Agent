from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

CATALOGUE_URL_TEMPLATE = "https://apps.ualberta.ca/catalogue/course/{subject}/{number}"

_SUBJECT_NUMBER_RE = re.compile(r"([A-Za-z]{2,10})\s*0*(\d{3})")


def parse_subject_and_number(course_code: str | None, course_name: str | None) -> tuple[str, str] | None:
    """Extracts ("cmput", "379")-style (subject, number) from a Canvas
    course's code or name, for building a UAlberta catalogue URL.
    """
    for field in (course_code, course_name):
        if not field:
            continue
        match = _SUBJECT_NUMBER_RE.search(field)
        if match:
            return match.group(1).lower(), match.group(2)
    return None


async def fetch_course_description(subject: str, number: str) -> dict:
    """Fetches the official UAlberta course calendar page (e.g.
    apps.ualberta.ca/catalogue/course/cmput/379) and extracts its title and
    description paragraph.

    Page layout (as of writing) is a `.container` div with, in order: a nav,
    an `<h1 class="mb-1">SUBJECT NUM - Title</h1>`, an `<h2>` with unit
    info, a `<p>Faculty of X</p>`, then a `<p>` with the actual description.
    That's brittle against a redesign, so this falls back to the page's
    visible text if the expected structure isn't found.
    """
    url = CATALOGUE_URL_TEMPLATE.format(subject=subject.lower().strip(), number=str(number).strip())
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.HTTPError:
        return {"url": url, "title": None, "description": None}

    soup = BeautifulSoup(resp.text, "html.parser")
    heading = soup.find("h1")
    container = heading.find_parent("div", class_="container") if heading else None

    description = None
    if container:
        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p", recursive=False)]
        paragraphs = [p for p in paragraphs if p]
        # Direct <p> children right after the heading are [Faculty line,
        # description] - take the second, or whatever's there if not.
        description = paragraphs[1] if len(paragraphs) > 1 else (paragraphs[0] if paragraphs else None)

    if not description:
        description = soup.get_text(" ", strip=True)[:1000] or None

    return {
        "url": url,
        "title": heading.get_text(strip=True) if heading else None,
        "description": description,
    }
