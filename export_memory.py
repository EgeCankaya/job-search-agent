#!/usr/bin/env python3
"""MODE-3 export, run by the owner: `uv run python export_memory.py [--show 5]`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from inbox_agent.export_memory import main  # noqa: E402

sys.exit(main())
