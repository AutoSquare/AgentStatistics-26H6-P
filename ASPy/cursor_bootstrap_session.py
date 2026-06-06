# -*- coding: utf-8 -*-
"""Emit JSON bootstrap result for WPF host (browser launch + IDE session)."""
from __future__ import annotations

import json
import os
import sys

from cursor_dashboard_auth import bootstrap_dashboard_session

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

skip_launch = os.getenv("AS_SKIP_BROWSER_LAUNCH", "").strip().lower() in {"1", "true", "yes"}
result = bootstrap_dashboard_session(launch_browser=not skip_launch, wait_seconds=5)
sys.stdout.write(json.dumps(result, ensure_ascii=False))
sys.stdout.write("\n")
