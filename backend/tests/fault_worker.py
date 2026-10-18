from __future__ import annotations

import sys
import time

# This fixture is intentionally inert. It never touches project data, network, or secrets.
if __name__ == "__main__":
    time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 60)
