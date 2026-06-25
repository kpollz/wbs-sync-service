"""LangFlow custom component: read the WBS knowledge file for a Part, by name.

WHY THIS EXISTS
---------------
The built-in **File** component reads by *path* (the hashed server path, e.g.
``USER/3f9a....json``). Our sync service re-pushes a file by **delete-old +
upload-new**, so even though the file *name* stays ``wbs_agent_knowledge_sales``,
its *path* changes every sync (new hash). A path-based reference therefore breaks
on every sync.

This component reads by **name** instead: given a Part (department) name, it
rebuilds the canonical filename the service uploaded (``<base>_<slug>``), looks
that name up in LangFlow's file management (DB), resolves the current path, and
reads the newest content. Survives any number of re-pushes.

It mirrors the built-in File component's three JSON outputs — **Structured
Content** (DataFrame, one record per row), **JSON Content**, and **Raw Content**
— so it is a drop-in, name-stable replacement.

SLUG CONTRACT (must match the service!)
---------------------------------------
The filename is built as ``f"{base}_{slugify(part_name)}"`` where ``slugify`` is
copied **verbatim** from ``src/wbs_sync/naming.py``:

    lowercase -> collapse runs of non-[a-z0-9] into one '_' -> trim '_'

If the service's slug rule ever changes, update ``_slugify`` here to match, or
the computed name won't line up with what was uploaded.

Usage: paste into a Custom Component node. Feed ``Part Name`` (e.g. ``Sales``,
``IT & Ops``) — the original department name or the slug both work, since
slugify is idempotent on already-slugified input.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langflow.custom import Component
from langflow.io import MessageTextInput, Output, StrInput
from langflow.schema.data import Data
from langflow.schema.message import Message

# LangFlow's DataFrame is a pandas subclass; fall back to plain pandas if the
# import path differs between versions. Either works for an Output return.
try:  # pragma: no cover - import path varies across LangFlow versions
    from langflow.schema.dataframe import DataFrame
except Exception:  # pragma: no cover
    import pandas as _pd

    DataFrame = _pd.DataFrame


# --- MUST match src/wbs_sync/naming.py:slugify (verbatim) ---------------------
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Lowercase, collapse non-[a-z0-9] runs to '_', trim ends. '' -> 'unnamed'."""
    slug = _NON_ALNUM.sub("_", (name or "").strip().lower()).strip("_")
    return slug or "unnamed"
# -----------------------------------------------------------------------------


class ReadWbsViaPartName(Component):
    """Read the synced WBS knowledge file for a Part, looked up by name.

    Inputs:
      - ``part_name``: department / Part name (e.g. ``Sales``). Empty -> the
        centralized default file (``<base>`` with no slug suffix).
      - ``base_name``: the file base name the service uploads under
        (default ``wbs_agent_knowledge``; matches ``LANGFLOW_FILE_NAME``).

    Outputs (same shape as the built-in File component for a single JSON file):
      - ``dataframe``: Structured Content — DataFrame, one record per row.
      - ``json``     : JSON Content — Data wrapping the parsed JSON.
      - ``message``  : Raw Content — Message with the raw JSON text (for Prompt/Agent).
    """

    display_name = "Read WBS Via Part Name"
    description = (
        "Đọc file WBS knowledge của 1 Part theo TÊN (không theo path). "
        "Tự ghép base + slug(part_name) -> tra file management -> đọc bản mới nhất. "
        "Cho ra Structured Content / JSON / Raw như File component mặc định."
    )
    icon = "file-text"
    name = "ReadWbsViaPartName"

    inputs = [
        MessageTextInput(
            name="part_name",
            display_name="Part Name",
            value="",
            info=(
                "Tên Part / bộ phận (như khi gọi work-profile, vd: 'Sales', 'IT & Ops'). "
                "Để trống nếu muốn đọc file default tập trung."
            ),
        ),
        StrInput(
            name="base_name",
            display_name="File Base Name",
            value="wbs_agent_knowledge",
            advanced=True,
            info="Base name service đẩy lên (trùng LANGFLOW_FILE_NAME). File = base + '_' + slug(part_name).",
        ),
    ]

    outputs = [
        Output(display_name="Structured Content", name="dataframe", method="load_structured"),
        Output(display_name="JSON Content", name="json", method="load_json"),
        Output(display_name="Raw Content", name="message", method="load_message"),
    ]

    # ---- filename the service would have uploaded for this Part -------------
    def _target_name(self) -> str:
        """Canonical LangFlow file name (no extension) for the current inputs."""
        part = (self.part_name or "").strip()
        if part:
            return f"{self.base_name}_{_slugify(part)}"
        return self.base_name  # no part -> centralized default file

    # ---- DB lookup + read (cached per build so the 3 outputs share one read) -
    async def _load(self) -> tuple[Any, str]:
        """Resolve the newest file for the target name and parse it.

        Returns ``(parsed_obj, resolved_path)``. Cached on the instance keyed by
        (base_name, part_name) so multiple outputs don't re-query/re-read.
        """
        cache_key = (self.base_name, (self.part_name or "").strip())
        cache = getattr(self, "_read_cache", None)
        if cache and cache.get("key") == cache_key:
            return cache["obj"], cache["path"]

        from sqlmodel import select

        from langflow.services.database.models.file import File
        from langflow.services.deps import session_scope

        target = self._target_name()

        async with session_scope() as session:
            stmt = select(File).where(File.name == target)
            if getattr(self, "user_id", None):
                stmt = stmt.where(File.user_id == self.user_id)
            rows = (await session.exec(stmt)).all()

        if not rows:
            raise FileNotFoundError(
                f"Không thấy file tên '{target}' trong file management "
                f"(base='{self.base_name}', part='{self.part_name}'). "
                "Kiểm tra Part name / base name, hoặc chạy sync trước."
            )

        # Re-push deletes the old row first, so normally exactly one remains;
        # take the newest defensively anyway.
        record = sorted(
            rows,
            key=lambda f: getattr(f, "updated_at", None) or getattr(f, "created_at", 0),
            reverse=True,
        )[0]

        resolved = self.resolve_path(record.path)
        obj = json.loads(Path(resolved).read_text(encoding="utf-8"))

        self._read_cache = {"key": cache_key, "obj": obj, "path": resolved}
        return obj, resolved

    # ---- output: Structured Content (DataFrame, one record per row) ---------
    async def load_structured(self) -> DataFrame:
        obj, path = await self._load()

        if isinstance(obj, list):
            records = [r for r in obj if isinstance(r, dict)]
            rows = records or [{"value": obj}]  # list of scalars -> single cell
        elif isinstance(obj, dict):
            rows = [obj]
        else:
            rows = [{"value": obj}]

        df = DataFrame(rows)
        df.attrs["source_file_path"] = path
        self.status = df
        return df

    # ---- output: JSON Content (Data) ----------------------------------------
    async def load_json(self) -> Data:
        obj, _ = await self._load()
        # Data.data is conventionally a dict; wrap bare lists/scalars.
        if isinstance(obj, dict):
            payload = obj
        elif isinstance(obj, list):
            payload = {"records": obj, "count": len(obj)}
        else:
            payload = {"value": obj}
        data = Data(data=payload)
        self.status = data
        return data

    # ---- output: Raw Content (Message) --------------------------------------
    async def load_message(self) -> Message:
        obj, _ = await self._load()
        text = json.dumps(obj, ensure_ascii=False, indent=2)
        self.status = text
        return Message(text=text)
