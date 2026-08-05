"""Check a locally loaded LM Studio model without affecting submission outputs.

Usage:
    python scripts/lmstudio_smoke_test.py

The script reads optional values from .env, then calls LM Studio's
OpenAI-compatible /v1/models and /v1/chat/completions endpoints using only the
standard library. It never reads or writes input/, output/, or logging/.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def request_json(url: str, token: str, payload: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    load_dotenv(Path(".env"))
    base_url = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    model = os.getenv("LMSTUDIO_MODEL", "")
    token = os.getenv("LMSTUDIO_API_TOKEN", "")
    try:
        models = request_json(f"{base_url}/models", token)
    except (HTTPError, URLError, TimeoutError) as error:
        raise SystemExit(
            f"Cannot reach LM Studio at {base_url}. Load a model and start the server in the Developer tab. ({error})"
        ) from error
    available = [entry.get("id", "<missing id>") for entry in models.get("data", [])]
    print("LM Studio reachable. Models reported:")
    for model_id in available:
        print(f"- {model_id}")
    if not model:
        raise SystemExit("Set LMSTUDIO_MODEL in .env to the exact loaded model ID, then run this test again.")
    if model not in available:
        raise SystemExit(f"LMSTUDIO_MODEL '{model}' is not listed by the server.")
    reply = request_json(
        f"{base_url}/chat/completions",
        token,
        {
            "model": model,
            "temperature": 0,
            "messages": [{"role": "user", "content": "Reply exactly: LOCAL_MODEL_OK"}],
        },
    )
    content = reply["choices"][0]["message"]["content"]
    print(f"Smoke-test reply: {content}")
    if content.strip() != "LOCAL_MODEL_OK":
        raise SystemExit("The local model answered, but did not pass the deterministic smoke test.")
    print("Local model configuration passed.")


if __name__ == "__main__":
    main()
