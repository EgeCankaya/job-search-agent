#!/usr/bin/env python3
"""TRK-3 import, run by the owner: `uv run python import_applications.py <csv> --table <name>`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from inbox_agent.import_applications import main  # noqa: E402

sys.exit(main())
