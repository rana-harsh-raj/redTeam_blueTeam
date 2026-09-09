import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
for p in (str(REPO), str(REPO / "RED_LOOP")):
    if p not in sys.path:
        sys.path.insert(0, p)
