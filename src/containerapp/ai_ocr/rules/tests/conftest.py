import sys
from pathlib import Path

CONTAINERAPP_ROOT = Path(__file__).resolve().parents[3]
if str(CONTAINERAPP_ROOT) not in sys.path:
    sys.path.insert(0, str(CONTAINERAPP_ROOT))
