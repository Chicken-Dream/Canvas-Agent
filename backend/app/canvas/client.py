from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from ..config import settings


class CanvasAPIError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Canvas API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class CanvasClient:
    """Thin async wrapper around the Canvas LMS REST API
    (https://canvas.instructure.com/doc/api/) for a single UAlberta Canvas
    instance, authenticated as one user (via OAuth2 access token or a
    Personal Access Token - both are just bearer tokens to the API).
    """

    def __init__(self, access_token: str, base_url: str | None = None):
        self.base_url = (base_url or settings.canvas_base_url).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v1/",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "CanvasClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        resp = await self._client.get(path, params=params)
        if resp.status_code >= 400:
            raise CanvasAPIError(resp.status_code, resp.text[:500])
        return resp

    async def get_paginated(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Follows Canvas's RFC5988 `Link` header pagination until exhausted."""
        results: list[dict] = []
        url: str | None = path
        query = params
        while url:
            resp = await self._get(url, params=query)
            query = None  # only applied to the first request; `next` link carries the rest
            data = resp.json()
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)

            next_url = resp.links.get("next", {}).get("url")
            if next_url:
                # `next` is absolute; httpx needs it relative to base_url or absolute is fine too.
                url = next_url
            else:
                url = None
        return results

    async def get_self(self) -> dict:
        resp = await self._get("users/self")
        return resp.json()

    async def list_courses(self) -> list[dict]:
        return await self.get_paginated(
            "courses",
            params={
                "enrollment_state": "active",
                "include[]": ["syllabus_body", "term", "course_image"],
                "per_page": 100,
            },
        )

    async def list_assignments(self, course_id: int) -> list[dict]:
        return await self.get_paginated(
            f"courses/{course_id}/assignments",
            params={"include[]": ["submission"], "order_by": "due_at", "per_page": 100},
        )

    async def get_front_page(self, course_id: int) -> dict | None:
        try:
            resp = await self._get(f"courses/{course_id}/front_page")
            return resp.json()
        except CanvasAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def list_pages(self, course_id: int) -> list[dict]:
        try:
            return await self.get_paginated(f"courses/{course_id}/pages", params={"per_page": 100})
        except CanvasAPIError as exc:
            if exc.status_code in (403, 404):
                return []
            raise

    async def get_page(self, course_id: int, url_or_slug: str) -> dict | None:
        try:
            resp = await self._get(f"courses/{course_id}/pages/{url_or_slug}")
            return resp.json()
        except CanvasAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def list_modules_with_items(self, course_id: int) -> list[dict]:
        try:
            return await self.get_paginated(
                f"courses/{course_id}/modules",
                params={"include[]": ["items"], "per_page": 100},
            )
        except CanvasAPIError as exc:
            if exc.status_code in (403, 404):
                return []
            raise

    async def get_file(self, file_id: int) -> dict | None:
        """Canvas Files API (https://canvas.instructure.com/doc/api/files.html) -
        returns metadata including a signed `url` for the actual file bytes,
        which is fetchable without further auth (the signature is in the URL).
        """
        try:
            resp = await self._get(f"files/{file_id}")
            return resp.json()
        except CanvasAPIError as exc:
            if exc.status_code == 404:
                return None
            raise


def canvas_oauth_authorize_url(state: str) -> str:
    from urllib.parse import urlencode

    params = {
        "client_id": settings.canvas_client_id,
        "response_type": "code",
        "redirect_uri": settings.canvas_redirect_uri,
        "state": state,
        "scope": "url:GET|/api/v1/users/:id url:GET|/api/v1/courses url:GET|/api/v1/courses/:course_id/assignments",
    }
    return urljoin(settings.canvas_base_url + "/", "login/oauth2/auth") + "?" + urlencode(params)


async def exchange_code_for_token(code: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            urljoin(settings.canvas_base_url + "/", "login/oauth2/token"),
            data={
                "grant_type": "authorization_code",
                "client_id": settings.canvas_client_id,
                "client_secret": settings.canvas_client_secret,
                "redirect_uri": settings.canvas_redirect_uri,
                "code": code,
            },
        )
        if resp.status_code >= 400:
            raise CanvasAPIError(resp.status_code, resp.text[:500])
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            urljoin(settings.canvas_base_url + "/", "login/oauth2/token"),
            data={
                "grant_type": "refresh_token",
                "client_id": settings.canvas_client_id,
                "client_secret": settings.canvas_client_secret,
                "refresh_token": refresh_token,
            },
        )
        if resp.status_code >= 400:
            raise CanvasAPIError(resp.status_code, resp.text[:500])
        return resp.json()
