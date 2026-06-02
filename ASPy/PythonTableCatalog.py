# -*- coding: utf-8 -*-
"""Minimal table I/O for {{ABBR}}_WORKSPACE (distilled from GeoPile PythonTableCatalog)."""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional

_DATA = "数据表"
_MAT = "材料库"
_ENV = "{{ABBR}}_WORKSPACE"


def _root() -> str:
    r = os.environ.get(_ENV, "").strip()
    if not r:
        raise RuntimeError(f"未设置环境变量 {_ENV}")
    return r


def _table_path(database_name: str, table_name: str) -> str:
    if database_name == "material_data":
        return os.path.join(_root(), _MAT, table_name + ".json")
    return os.path.join(_root(), _DATA, table_name + ".json")


def _json_safe_value(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _json_safe_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe_value(x) for x in v]
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def load_rows(database_name: str, table_name: str) -> List[Dict[str, Any]]:
    path = _table_path(database_name, table_name)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return data


def save_rows(database_name: str, table_name: str, rows: List[Dict[str, Any]]) -> None:
    path = _table_path(database_name, table_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    safe = [{k: _json_safe_value(val) for k, val in row.items()} for row in rows]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe, f, ensure_ascii=False, indent=2, allow_nan=False)


def query_field(database_name: str, table_name: str, field_name: str) -> Optional[Any]:
    rows = load_rows(database_name, table_name)
    if not rows:
        return None
    return rows[0].get(field_name)
