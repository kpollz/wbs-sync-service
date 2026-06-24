"""HTTP client for the LangFlow Files API v2 (list / delete / upload)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)


class LangFlowError(RuntimeError):
    """Raised when a LangFlow API call fails."""


class LangFlowClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        file_name: str = "wbs",
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.file_name = file_name
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
        """Normalise the list-files response (bare list or wrapped dict)."""
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
        """Normalise an upload response (single dict or a list of dicts)."""
        if isinstance(data, list):
            return data[0] if data else {}
        if isinstance(data, dict):
            return data
        return {}

    def list_files(self) -> list[dict[str, Any]]:
        resp = self.session.get(self.files_url, timeout=self.timeout)
        self._check(resp, "list files")
        return self._as_file_list(resp.json())

    def delete_file(self, file_id: str) -> None:
        resp = self.session.delete(f"{self.files_url}/{file_id}", timeout=self.timeout)
        # Non-fatal: a stale/missing id just means nothing to delete.
        if not resp.ok:
            log.warning("delete %s -> HTTP %s (ignored)", file_id, resp.status_code)

    def upload_file(self, path: Path) -> dict[str, Any]:
        # Always upload under the canonical name "<file_name>.json" so LangFlow
        # stores it as `file_name` (it strips the extension). Do NOT use
        # path.name: the local source is the temp file "wbs.tmp.json" and
        # LangFlow would otherwise store it as "wbs.tmp" instead of "wbs".
        upload_name = f"{self.file_name}.json"
        with path.open("rb") as fh:
            files = {"file": (upload_name, fh, "application/json")}
            resp = self.session.post(self.files_url, files=files, timeout=self.timeout)
        self._check(resp, "upload file")
        return self._as_file_meta(resp.json())

    def _is_managed(self, name: str) -> bool:
        """Our canonical file, plus the '<name>.tmp' residue left by an older
        version that uploaded the temp file under its own name."""
        return name == self.file_name or name.startswith(f"{self.file_name}.tmp")

    def replace_file(self, path: Path) -> dict[str, Any]:
        """Delete every managed file (incl. any .tmp residue), then upload.

        Deleting *all* matches guards against leftover duplicates from a
        previously failed delete. Only the freshly uploaded file remains.
        """
        matches = [f for f in self.list_files() if self._is_managed(f.get("name") or "")]
        for f in matches:
            file_id = f.get("id")
            if file_id:
                log.info("deleting old LangFlow file id=%s name=%s", file_id, f.get("name"))
                self.delete_file(file_id)

        meta = self.upload_file(path)
        log.info("uploaded as '%s' -> %s", self.file_name, meta.get("path") or meta.get("id"))
        return meta
