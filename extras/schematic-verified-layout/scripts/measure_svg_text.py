"""Compatibility launcher for the bundled ESO native SVG renderer."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eso.svg_text import main
if __name__ == '__main__':
    raise SystemExit(main())
