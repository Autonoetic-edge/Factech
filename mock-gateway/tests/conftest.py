import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("ENGINE_API_KEY", "gateway-test-key")

os.environ["FACETECH_CAPTURE_DB"] = "off"
