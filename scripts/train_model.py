from __future__ import annotations

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))

from ml.train import train  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(train(), indent=2))
