from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from ..config import settings
from .client import CanvasClient

KEYWORDS = ("outline", "syllabus")

# How much text to keep from a fetched outline document. This is a
# prototype - a real pipeline would chunk/embed this for the future agent
# instead of storing a flat blob.
MAX_FETCHED_CHARS = 20_000


@dataclass
class OutlineCandidate:
    source: str  # "syllabus_body" | "module_item" | "page" | "none" | "user_submitted"
    title: str | None
    canvas_url: str | None
    external_url: str | None
    confidence: float
    fetched_content: str | None = None


def _is_canvas_domain(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    canvas_host = urlparse(settings.canvas_base_url).netloc.lower()
    return host == "" or host == canvas_host


def _text_mentions_keyword(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(k in lowered for k in KEYWORDS)


def _extract_external_links(html: str) -> list[tuple[str, str]]:
    """Returns (link_text, href) pairs for links pointing off the Canvas domain."""
    soup = BeautifulSoup(html, "html.parser")
    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http") and not _is_canvas_domain(href):
            links.append((a.get_text(strip=True), href))
    return links


async def _fetch_external_text(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "html" in content_type:
                soup = BeautifulSoup(resp.text, "html.parser")
                text = soup.get_text(separator="\n", strip=True)
            else:
                text = resp.text
            return text[:MAX_FETCHED_CHARS]
    except (httpx.HTTPError, UnicodeDecodeError):
        return None


async def _fetch_and_extract_file_text(file_meta: dict) -> str | None:
    """Given a Canvas Files API response (has a signed `url` good for an
    unauthenticated download), fetches the bytes and extracts text - PDF via
    pypdf, HTML via BeautifulSoup, otherwise a best-effort text decode.
    """
    download_url = file_meta.get("url")
    if not download_url:
        return None

    content_type = (file_meta.get("content-type") or "").lower()
    filename = (file_meta.get("filename") or file_meta.get("display_name") or "").lower()

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            content = resp.content
    except httpx.HTTPError:
        return None

    if "pdf" in content_type or filename.endswith(".pdf"):
        try:
            reader = PdfReader(BytesIO(content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return None
        return text[:MAX_FETCHED_CHARS] if text.strip() else None

    if "html" in content_type:
        return BeautifulSoup(content, "html.parser").get_text(separator="\n", strip=True)[:MAX_FETCHED_CHARS]

    try:
        return content.decode("utf-8")[:MAX_FETCHED_CHARS]
    except UnicodeDecodeError:
        return None


async def resolve_outline_from_url(client: CanvasClient, course_id: int, url: str) -> OutlineCandidate:
    """Builds an authoritative (confidence=1.0) OutlineCandidate for a URL
    the student submitted themselves as "this is my course outline",
    fetching its content the right way depending on whether it's a Canvas
    page, a Canvas file, or a genuinely external site. `course_id` must be
    the *Canvas* course id (not this app's internal DB id).
    """
    parsed = urlparse(url)
    fetched_content: str | None = None
    canvas_url: str | None = None
    external_url: str | None = None

    if not _is_canvas_domain(url):
        external_url = url
        fetched_content = await _fetch_external_text(url)
    else:
        canvas_url = url
        path = parsed.path
        if "/pages/" in path:
            slug = path.split("/pages/", 1)[1].split("/")[0]
            page = await client.get_page(course_id, slug)
            if page and page.get("body"):
                fetched_content = BeautifulSoup(page["body"], "html.parser").get_text(
                    separator="\n", strip=True
                )[:MAX_FETCHED_CHARS]
        elif "/files/" in path:
            file_id_str = path.split("/files/", 1)[1].split("/")[0]
            if file_id_str.isdigit():
                file_meta = await client.get_file(int(file_id_str))
                if file_meta:
                    fetched_content = await _fetch_and_extract_file_text(file_meta)

    return OutlineCandidate(
        source="user_submitted",
        title="Course Outline",
        canvas_url=canvas_url,
        external_url=external_url,
        confidence=1.0,
        fetched_content=fetched_content,
    )


async def detect_course_outline(client: CanvasClient, course: dict) -> OutlineCandidate:
    """Best-effort search for "the real course outline" within a course.

    UAlberta instructors surface this very inconsistently: sometimes it's
    the Syllabus tab body itself, sometimes it's a Modules item titled
    "Course Outline" that either opens a Canvas Page or is an ExternalUrl
    item that redirects straight to e.g. a GitHub Pages site, and
    sometimes it's buried in the course Pages list under a generic name.
    We try each source in order of how authoritative/cheap it is and keep
    the highest-confidence hit.
    """
    course_id = course["id"]
    best: OutlineCandidate | None = None

    def consider(candidate: OutlineCandidate) -> None:
        nonlocal best
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    # 1) Syllabus tab body (`syllabus_body`, already included on the course object).
    syllabus_body = course.get("syllabus_body")
    if syllabus_body:
        ext_links = _extract_external_links(syllabus_body)
        if ext_links:
            # Prefer a link whose own text calls out the outline/syllabus explicitly.
            labeled = [l for l in ext_links if _text_mentions_keyword(l[0])]
            text, href = labeled[0] if labeled else ext_links[0]
            confidence = 0.9 if labeled else 0.6
            consider(
                OutlineCandidate(
                    source="syllabus_body",
                    title=text or "Course Syllabus",
                    canvas_url=f"{settings.canvas_base_url}/courses/{course_id}/assignments/syllabus",
                    external_url=href,
                    confidence=confidence,
                )
            )
        elif _text_mentions_keyword(syllabus_body):
            consider(
                OutlineCandidate(
                    source="syllabus_body",
                    title="Course Syllabus",
                    canvas_url=f"{settings.canvas_base_url}/courses/{course_id}/assignments/syllabus",
                    external_url=None,
                    confidence=0.4,
                    fetched_content=BeautifulSoup(syllabus_body, "html.parser").get_text(
                        separator="\n", strip=True
                    )[:MAX_FETCHED_CHARS],
                )
            )

    # 2) Modules: look for an item literally titled "Course Outline" / "Syllabus".
    if best is None or best.confidence < 0.9:
        modules = await client.list_modules_with_items(course_id)
        for module in modules:
            for item in module.get("items", []):
                title = item.get("title", "")
                if not _text_mentions_keyword(title):
                    continue

                item_type = item.get("type")
                if item_type == "ExternalUrl" and item.get("external_url"):
                    consider(
                        OutlineCandidate(
                            source="module_item",
                            title=title,
                            canvas_url=item.get("html_url"),
                            external_url=item["external_url"],
                            confidence=0.95,
                        )
                    )
                elif item_type == "Page" and item.get("page_url"):
                    page = await client.get_page(course_id, item["page_url"])
                    if page and page.get("body"):
                        ext_links = _extract_external_links(page["body"])
                        if ext_links:
                            text, href = ext_links[0]
                            consider(
                                OutlineCandidate(
                                    source="module_item",
                                    title=title,
                                    canvas_url=page.get("html_url"),
                                    external_url=href,
                                    confidence=0.9,
                                )
                            )
                        else:
                            consider(
                                OutlineCandidate(
                                    source="module_item",
                                    title=title,
                                    canvas_url=page.get("html_url"),
                                    external_url=None,
                                    confidence=0.7,
                                    fetched_content=BeautifulSoup(
                                        page["body"], "html.parser"
                                    ).get_text(separator="\n", strip=True)[:MAX_FETCHED_CHARS],
                                )
                            )
                elif item_type in ("File", "Assignment", "Discussion") and item.get("html_url"):
                    consider(
                        OutlineCandidate(
                            source="module_item",
                            title=title,
                            canvas_url=item.get("html_url"),
                            external_url=None,
                            confidence=0.5,
                        )
                    )

    # 3) Course Pages list: any page whose title mentions outline/syllabus.
    if best is None or best.confidence < 0.9:
        pages = await client.list_pages(course_id)
        candidate_pages = [p for p in pages if _text_mentions_keyword(p.get("title", ""))]
        for page_stub in candidate_pages:
            page = await client.get_page(course_id, page_stub["url"])
            if not page or not page.get("body"):
                continue
            ext_links = _extract_external_links(page["body"])
            if ext_links:
                text, href = ext_links[0]
                consider(
                    OutlineCandidate(
                        source="page",
                        title=page.get("title"),
                        canvas_url=page.get("html_url"),
                        external_url=href,
                        confidence=0.85,
                    )
                )
            else:
                consider(
                    OutlineCandidate(
                        source="page",
                        title=page.get("title"),
                        canvas_url=page.get("html_url"),
                        external_url=None,
                        confidence=0.6,
                        fetched_content=BeautifulSoup(page["body"], "html.parser").get_text(
                            separator="\n", strip=True
                        )[:MAX_FETCHED_CHARS],
                    )
                )

    if best is None:
        return OutlineCandidate(
            source="none", title=None, canvas_url=None, external_url=None, confidence=0.0
        )

    # If we found an external redirect and don't already have scraped text,
    # try to fetch it - many UAlberta instructors host outlines as public
    # GitHub Pages / plain static sites.
    if best.external_url and not best.fetched_content:
        best.fetched_content = await _fetch_external_text(best.external_url)

    return best
