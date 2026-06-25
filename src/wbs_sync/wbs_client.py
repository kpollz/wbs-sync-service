"""HTTP client for the WBS API: departments, centralized works, per-part work profiles."""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from .models import Department, WorkCode

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
        works_path: str = "/api/works/search",
        departments_path: str = "/api/departments",
        work_profiles_path: str = "/api/work-profiles",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.timeout = timeout
        self.works_path = works_path
        self.departments_path = departments_path
        self.work_profiles_path = work_profiles_path
        self.session = requests.Session()
        self.session.headers.update({"accept": "*/*", "x-api-key": api_key})

    # --- departments (API 1) ---

    def fetch_departments(self) -> list[Department]:
        """List all parts/departments (we only use the name)."""
        url = f"{self.base_url}{self.departments_path}"
        log.debug("GET %s", url)
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        value = data.get("value") if isinstance(data, dict) else data
        value = value or []

        departments: list[Department] = []
        for item in value:
            try:
                departments.append(Department(**item))
            except Exception as exc:
                log.warning("skipping malformed department: %s", exc)
        log.info("fetched %d departments", len(departments))
        return departments

    # --- work codes (default + per-part), shared pagination ---

    def fetch_works(self) -> list[WorkCode]:
        """Centralized list (default target)."""
        return self._fetch_paginated(f"{self.base_url}{self.works_path}", {})

    def fetch_work_profiles(self, department_name: str) -> list[WorkCode]:
        """Per-part list (API 2). departmentName uses the ORIGINAL name."""
        return self._fetch_paginated(
            f"{self.base_url}{self.work_profiles_path}",
            {"departmentName": department_name},
        )

    @staticmethod
    def _total_elements(data: dict[str, Any]) -> Optional[int]:
        for key in _TOTAL_KEYS:
            value = data.get(key)
            if isinstance(value, int):
                return value
        return None

    def _fetch_paginated(self, url: str, extra_params: dict[str, str]) -> list[WorkCode]:
        """Page through a work-code endpoint until the source is exhausted."""
        records: list[WorkCode] = []
        page = 1
        total: Optional[int] = None

        while True:
            params = {"pageNum": str(page), "pageSize": str(self.page_size), **extra_params}
            log.debug("GET %s params=%s", url, params)
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()

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

        log.info("fetched %d work codes from %s (total=%s)", len(records), url, total)
        return records
