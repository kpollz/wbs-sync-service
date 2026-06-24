"""Map full WBS records to the slim representation pushed to LangFlow."""

from __future__ import annotations

from .models import WorkCode, WorkCodeSlim


def to_slim(record: WorkCode) -> WorkCodeSlim:
    return WorkCodeSlim(
        name=record.name,
        code=record.code,
        description=record.description,
        input=record.input,
        output=record.output,
        task=record.task,
        workCategory=record.workCategory.name if record.workCategory else None,
        job=record.job.name if record.job else None,
    )


def to_slim_list(records: list[WorkCode]) -> list[WorkCodeSlim]:
    return [to_slim(r) for r in records]
