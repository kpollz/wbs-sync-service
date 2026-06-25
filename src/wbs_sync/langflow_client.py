"""HTTP client for the LangFlow Files API v2 (list / delete / upload by name)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)


class LangFlowError(RuntimeError):
    """Raised when a LangFlow API call fails."""


class LangFlowClient:
    """Name-agnostic client: callers pass the target filename explicitly.

    A file uploaded as ``<base>.json`` is stored by LangFlow under the name
    ``<base>`` (the extension is stripped). ``delete_by_base`` matches that name
    (plus any stale ``<base>.tmp`` residue) and removes it.
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"accept": "application/json", "x-api-key": api_key})

    @property
    def files_url(self) -> str:
        return f"{self.base_url}/api/v2/files"

    def _check(self, resp: requests.Response, action: str) -> None:
        if not resp.ok:
            raise LangFlowError(f"{action} failed: HTTP {resp.status_code}: {resp.text[:300]}")

    @staticmethod
    def _as_file_list(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("files", "data", "items", "results"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
            return [data]
        return []

    @staticmethod
    def _as_file_meta(data: Any) -> dict[str, Any]:
        if isinstance(data, list):
            return data[0] if data else {}
        if isinstance(data, dict):
            return data
        return {}

    @staticmethod
    def _matches(name: str, base: str) -> bool:
        """Our canonical file, plus the '<base>.tmp' residue from older runs."""
        return name == base or name.startswith(base + ".tmp")

    def list_files(self) -> list[dict[str, Any]]:
        resp = self.session.get(self.files_url, timeout=self.timeout)
        self._check(resp, "list files")
        return self._as_file_list(resp.json())

    def delete_file(self, file_id: str) -> None:
        resp = self.session.delete(f"{self.files_url}/{file_id}", timeout=self.timeout)
        if not resp.ok:  # non-fatal: a stale/missing id just means nothing to delete
            log.warning("delete %s -> HTTP %s (ignored)", file_id, resp.status_code)

    def delete_by_base(self, base: str) -> None:
        """Delete every file whose name == base (or '<base>.tmp' residue)."""
        for f in self.list_files():
            if self._matches(f.get("name") or "", base):
                file_id = f.get("id")
                if file_id:
                    log.info("deleting LangFlow file id=%s name=%s", file_id, f.get("name"))
                    self.delete_file(file_id)

    def upload_file(self, path: Path, filename: str) -> dict[str, Any]:
        """Upload ``path`` under the explicit ``filename`` (e.g. 'wbs_agent_knowledge_qa.json')."""
        with path.open("rb") as fh:
            files = {"file": (filename, fh, "application/json")}
            resp = self.session.post(self.files_url, files=files, timeout=self.timeout)
        self._check(resp, "upload file")
        return self._as_file_meta(resp.json())

    def replace_file(self, path: Path, filename: str) -> dict[str, Any]:
        """Delete the old file(s) for this base name, then upload the new one."""
        base = Path(filename).stem  # 'wbs_agent_knowledge_qa.json' -> 'wbs_agent_knowledge_qa'
        self.delete_by_base(base)
        meta = self.upload_file(path, filename)
        log.info("uploaded '%s' -> %s", base, meta.get("path") or meta.get("id"))
        return meta
