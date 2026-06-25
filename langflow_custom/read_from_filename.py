from pathlib import Path

from langflow.custom import Component
from langflow.io import MessageTextInput, Output
from langflow.schema.data import Data
from langflow.schema.message import Message
from langflow.base.data.utils import parse_text_file_to_data


class ReadFileByName(Component):
    display_name = "Read File By Name"
    description = "Tra path theo tên file trong file management rồi đọc nội dung mới nhất."
    icon = "file-text"
    name = "ReadFileByName"

    inputs = [
        MessageTextInput(
            name="filename",
            display_name="File Name",
            value="WBS_Agent_Knowledge.xlsx",
            info="Tên file trong My Files, có thể kèm đuôi (test.txt, data.xlsx, info.json).",
        ),
    ]
    outputs = [
        Output(display_name="Content", name="content", method="load_text"),
        Output(display_name="Data", name="data", method="load_data"),
    ]

    # ---- tìm path + đọc, trả về Data (dùng nội bộ) ----
    async def _load(self) -> Data:
        from sqlmodel import select
        from langflow.services.database.models.file import File
        from langflow.services.deps import session_scope

        raw = self.filename.strip()
        stem = Path(raw).stem
        suffix = Path(raw).suffix.lower()

        async with session_scope() as session:
            stmt = select(File).where(File.name == stem)
            if getattr(self, "user_id", None):
                stmt = stmt.where(File.user_id == self.user_id)
            result = (await session.exec(stmt)).all()

        if not result:
            raise FileNotFoundError(f"Không thấy file tên '{stem}' trong file management.")

        record = sorted(
            result,
            key=lambda f: getattr(f, "updated_at", None) or getattr(f, "created_at", 0),
            reverse=True,
        )[0]

        resolved = self.resolve_path(record.path)
        if not suffix:
            suffix = Path(record.path).suffix.lower()

        return self._read_by_suffix(resolved, suffix, source_name=raw)

    # ---- output 1: nội dung dạng text, dùng luôn cho Prompt/Agent ----
    async def load_text(self) -> Message:
        data = await self._load()
        text = data.text or ""
        self.status = text          # hiện full nội dung ngay trong component
        return Message(text=text)

    # ---- output 2: Data, nếu component sau cần ----
    async def load_data(self) -> Data:
        data = await self._load()
        self.status = data
        return data

    def _read_by_suffix(self, path: str, suffix: str, source_name: str) -> Data:
        if suffix in (".xlsx", ".xls"):
            import pandas as pd
            sheets = pd.read_excel(path, sheet_name=None)
            parts = []
            for sheet, sdf in sheets.items():
                try:
                    table = sdf.to_markdown(index=False)
                except Exception:
                    table = sdf.to_csv(index=False)
                parts.append(f"# Sheet: {sheet}\n{table}")
            text = "\n\n".join(parts)
            return Data(text=text, data={"source": source_name, "path": path})

        if suffix == ".csv":
            import pandas as pd
            df = pd.read_csv(path)
            try:
                text = df.to_markdown(index=False)
            except Exception:
                text = df.to_csv(index=False)
            return Data(text=text, data={"source": source_name, "path": path})

        if suffix == ".json":
            import json
            obj = json.loads(Path(path).read_text(encoding="utf-8"))
            return Data(text=json.dumps(obj, ensure_ascii=False, indent=2),
                        data={"source": source_name, "path": path})

        return parse_text_file_to_data(path, silent_errors=False)