# -*- coding: utf-8 -*-
"""Sample calculation driver for AgentStatistics."""
from __future__ import annotations

import os
import sys
import traceback

from PythonTableCatalog import load_rows, save_rows

_ENV = "{{ABBR}}_WORKSPACE"
_RESULT_TABLE = "样本计算结果"


def main() -> int:
    workspace = input().strip()
    if not workspace:
        print("missing workspace path on stdin", file=sys.stderr)
        return 1
    os.environ[_ENV] = workspace
    try:
        rows = load_rows("project_data", "项目基本信息")
        app_name = "unknown"
        if rows:
            app_name = str(rows[0].get("值") or rows[0].get("app") or app_name)
        save_rows(
            "project_data",
            _RESULT_TABLE,
            [{"status": "ok", "message": f"sample run for {app_name}"}],
        )
        print(f"<log>样本计算完成: {app_name}", flush=True)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
