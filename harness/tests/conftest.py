"""Test fixtures and path setup for harness tests."""

import sys
from pathlib import Path

# Add harness/ to sys.path so `from runners import ...` and `import run` work
HARNESS_DIR = Path(__file__).parent.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))
