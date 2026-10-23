#!/usr/bin/env python3
"""NFR-PRIV-3's log scan, run by the owner: `uv run python scan_logs.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from inbox_agent.scan_logs import main  # noqa: E402

sys.exit(main())
