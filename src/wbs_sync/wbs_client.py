"""HTTP client for the WBS /api/works/search endpoint with pagination."""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from .models import WorkCode

log = logging.getLogger(__name__)

# Hard cap on pages to avoid an infinite loop if the API misbehaves.
_MAX_PAGES = 10_000
# Candidate keys that may carry the total element count in the response wrapper.
_TOTAL_KEYS = ("totalElements", "total", "totalCount", "count")


class WBSClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        page_size: int = 500,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"accept": "*/*", "x-api-key": api_key})

    def fetch_page(self, page_num: int) -> dict[str, Any]:
        url = f"{self.base_url}/api/works/search"
        params = {"pageNum": str(page_num), "pageSize": str(self.page_size)}
        log.debug("GET %s params=%s", url, params)
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _total_elements(data: dict[str, Any]) -> Optional[int]:
        for key in _TOTAL_KEYS:
            value = data.get(key)
            if isinstance(value, int):
                return value
        return None

    def fetch_all(self) -> list[WorkCode]:
        """Page through every work code until the source is exhausted."""
        records: list[WorkCode] = []
        page = 1
        total: Optional[int] = None

        while True:
            data = self.fetch_page(page)
            if total is None:
                total = self._total_elements(data)

            content = data.get("content") or []
            for item in content:
                try:
                    records.append(WorkCode(**item))
                except Exception as exc:  # malformed record — skip, keep going
                    log.warning("skipping malformed work code: %s", exc)

            page_len = len(content)
            last_page = page_len < self.page_size
            reached_total = total is not None and len(records) >= total

            if last_page or reached_total:
                break

            page += 1
            if page > _MAX_PAGES:
                log.warning("reached page safety cap (%d); stopping", _MAX_PAGES)
                break

        log.info("fetched %d work codes (total=%s)", len(records), total)
        return records
