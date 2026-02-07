#!/usr/bin/env python3
"""
One-time legacy schedule bootstrap import.

Usage:
  python scripts/bootstrap_import_schedule.py --xlsx schedule.xlsx --allow-hard-violations
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

# Ensure `app` package resolves when running as `python scripts/...`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import async_session_maker
from app.services.excel_import import ExcelImportService
from app.services.validation import ValidationError


async def _run(xlsx_path: Path, allow_hard_violations: bool) -> int:
    async with async_session_maker() as db:
        service = ExcelImportService(db)
        try:
            result = await service.import_excel(xlsx_path)
            await db.commit()
            print(json.dumps({"status": "ok", "mode": "strict", "result": result}, indent=2))
            return 0
        except ValidationError as err:
            if not allow_hard_violations:
                await db.rollback()
                print(
                    json.dumps(
                        {
                            "status": "validation_failed",
                            "mode": "strict",
                            "context": err.context,
                            "violations": len(err.violations),
                            "message": "Re-run with --allow-hard-violations for one-time bootstrap import.",
                        },
                        indent=2,
                    )
                )
                return 1

            counts = Counter(v.code for v in err.violations)
            await db.commit()
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "mode": "legacy_bootstrap",
                        "context": err.context,
                        "violations": len(err.violations),
                        "violation_counts": dict(counts),
                    },
                    indent=2,
                )
            )
            return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap import schedule.xlsx into DB")
    parser.add_argument("--xlsx", default="schedule.xlsx", help="Path to the XLSX schedule file")
    parser.add_argument(
        "--allow-hard-violations",
        action="store_true",
        help="Commit parsed assignments even if hard validation fails (one-time migration only)",
    )
    args = parser.parse_args()

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        print(json.dumps({"status": "error", "error": f"File not found: {xlsx_path}"}, indent=2))
        return 1

    return asyncio.run(_run(xlsx_path, args.allow_hard_violations))


if __name__ == "__main__":
    raise SystemExit(main())
