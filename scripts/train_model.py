from __future__ import annotations

import json

from ml.train import train


if __name__ == "__main__":
    print(json.dumps(train(), indent=2))
