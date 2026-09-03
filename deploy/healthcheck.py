from __future__ import annotations

import os
import sys
from urllib.error import URLError
from urllib.request import urlopen


def main() -> int:
    port = os.environ.get("APP_PORT", "7860")
    url = f"http://127.0.0.1:{port}/healthz"
    try:
        with urlopen(url, timeout=3) as response:
            if 200 <= response.status < 400:
                return 0
            print(f"application returned HTTP {response.status}", file=sys.stderr)
    except (OSError, URLError, ValueError) as exc:
        print(f"application health check failed: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
