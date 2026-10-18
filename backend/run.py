from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == "__main__":
    uvicorn.run(
        "backend.app.main:app",
        host=os.environ.get("FLUJO_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLUJO_PORT", "47821")),
        log_level=os.environ.get("FLUJO_LOG_LEVEL", "warning"),
        access_log=False,
    )
