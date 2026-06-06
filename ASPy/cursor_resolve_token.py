# -*- coding: utf-8 -*-
"""Print resolved Cursor session token to stdout for host process bootstrap."""
from __future__ import annotations

import sys

from cursor_discover import resolve_session_token

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

resolved = resolve_session_token()
if resolved and resolved.get("token"):
    sys.stdout.write(str(resolved["token"]))
    sys.stdout.write("\n")
