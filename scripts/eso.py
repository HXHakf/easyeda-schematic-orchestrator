#!/usr/bin/env python3
"""Run directly from a skill checkout without an editable install."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eso.cli import main
raise SystemExit(main())
