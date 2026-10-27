"""NDJSON debug logs for agent session (mounted at /cursor-debug on host)."""

import json
import time
from pathlib import Path

_LOG_PATH = Path("/cursor-debug/debug-03680a.log")
_SESSION = "03680a"


def agent_debug_log(
    location: str,
    message: str,
    data: dict | None = None,
    *,
    hypothesis_id: str | None = None,
    run_id: str | None = None,
) -> None:
    import os

    if run_id is None:
        run_id = os.environ.get("AGENT_DEBUG_RUN_ID", "pre-fix")
    # #region agent log
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessionId": _SESSION,
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data or {},
            "hypothesisId": hypothesis_id,
            "runId": run_id,
        }
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion
