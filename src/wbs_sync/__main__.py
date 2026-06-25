"""CLI entrypoint: ``python -m wbs_sync [run-once [--force]]``.

No subcommand (or the implicit ``serve``) starts the scheduler.
"""

from __future__ import annotations

import argparse
import logging
import sys

from pydantic import ValidationError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wbs_sync",
        description="Sync WBS work codes to LangFlow, pushing only on change.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("serve", help="run the scheduler (default)")

    run_once = sub.add_parser("run-once", help="run a single sync cycle and exit")
    run_once.add_argument(
        "--force",
        action="store_true",
        help="push to LangFlow even if nothing changed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Import settings lazily so --help works without a configured environment.
    from .config import get_settings

    try:
        cfg = get_settings()
    except ValidationError as exc:
        missing = ", ".join(
            ".".join(str(p) for p in err["loc"]) for err in exc.errors()
        )
        print(f"error: missing/invalid configuration: {missing}", file=sys.stderr)
        print("set these via environment variables or a .env file.", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=cfg.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    command = args.command or "serve"
    if command == "run-once":
        from .pipeline import run_once

        result = run_once(force=args.force)
        logging.getLogger(__name__).info(
            "done: targets=%d changed=%d uploaded=%d failed=%d removed=%d",
            result.targets, result.changed, result.uploaded, result.failed, result.removed,
        )
        # Exit non-zero if any target's upload failed.
        return 0 if result.failed == 0 else 1

    from .scheduler import run_scheduler

    run_scheduler()
    return 0


if __name__ == "__main__":
    sys.exit(main())
