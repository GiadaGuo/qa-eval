"""API container healthcheck.

/api/v1/health always returns HTTP 200 — a degraded database or read-only
artifact store is reported in the JSON body. Parse the body and only pass
when status == "ok".
"""

import json
import sys
import urllib.request

URL = "http://127.0.0.1:8000/api/v1/health"

try:
    with urllib.request.urlopen(URL, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
    print(f"healthcheck failed: {exc}", file=sys.stderr)
    sys.exit(1)

if payload.get("status") != "ok":
    print(f"healthcheck degraded: {json.dumps(payload, ensure_ascii=False)}")
    sys.exit(1)

print("ok")
