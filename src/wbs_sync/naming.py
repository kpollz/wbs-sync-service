"""Filename/name slugification for per-part targets."""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Make a filename-safe slug from a department name.

    Lowercase, collapse runs of non-[a-z0-9] (spaces, '/', '&', ...) into a
    single '_', and trim leading/trailing '_'.
        "R&D / Engineering" -> "r_d_engineering"
        "Sales & Marketing" -> "sales_marketing"
        "QA"                -> "qa"
    """
    slug = _NON_ALNUM.sub("_", (name or "").strip().lower()).strip("_")
    return slug or "unnamed"


def assign_slugs(names: list[str]) -> list[str]:
    """Return a unique slug per input (aligned), suffixing collisions with _2, _3, ...

    Handles both same-name and different-name-same-slug collisions.
    """
    taken: dict[str, bool] = {}
    out: list[str] = []
    for raw in names:
        base = slugify(raw)
        slug = base
        suffix = 2
        while slug in taken:
            slug = f"{base}_{suffix}"
            suffix += 1
        taken[slug] = True
        out.append(slug)
    return out
