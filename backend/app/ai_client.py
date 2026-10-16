"""HTTP adapter for AI Gateway v1; no model/provider SDK or browser knowledge."""
import hashlib
import json
import time
from urllib.parse import urlsplit

import httpx


class AIError(RuntimeError):
    def __init__(self, code, *, response=None, indeterminate=False):
        super().__init__(code)
        self.code = code
        self.response = response
        self.indeterminate = indeterminate


def digest(value) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class AIClient:
    def __init__(self, base_url, api_key, timeout=210, *, transport=None, sleep=time.sleep):
        url = urlsplit(base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("AI_BASE_URL must be an HTTP(S) URL without credentials, query or fragment")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport
        self.sleep = sleep
        self.namespace = digest({"endpoint": self.base_url, "account": hashlib.sha256(api_key.encode()).hexdigest()})

    def _request(self, method, path, deadline, *, body=None, key=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AIError("client_timeout", indeterminate=bool(key))
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if key:
            headers["Idempotency-Key"] = key
        # Never forward credentials through redirects or system HTTP proxies.
        with httpx.Client(transport=self.transport, follow_redirects=False, trust_env=False) as client:
            with client.stream(method, self.base_url + path, headers=headers, json=body, timeout=remaining) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise AIError("response_too_large", indeterminate=bool(key))
                    if time.monotonic() >= deadline:
                        raise AIError("client_timeout", indeterminate=bool(key))
                    chunks.append(chunk)
                raw = b"".join(chunks)
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError) as exc:
                    raise AIError("invalid_http_response", response={"http_status": response.status_code}, indeterminate=bool(key)) from exc
                if not isinstance(data, dict):
                    raise AIError("invalid_http_response", indeterminate=bool(key))
                return response.status_code, data

    def profiles(self):
        try:
            status, data = self._request("GET", "/v1/profiles", time.monotonic() + min(self.timeout, 5))
        except httpx.HTTPError as exc:
            raise AIError("gateway_unavailable") from exc
        if status != 200 or not isinstance(data.get("routing_version"), str):
            raise AIError("gateway_unavailable", response=data)
        return data

    def readiness(self) -> dict:
        """The gateway's own view of its browser worker ({"ready", "code"}). A gateway process can be
        up and answering /v1/profiles while its worker is parked (e.g. deadline_exceeded) and every
        job fails fast -- that is exactly the state that went unnoticed for a day, so ask directly."""
        try:
            status, data = self._request("GET", "/readyz", time.monotonic() + min(self.timeout, 2))
        except (httpx.HTTPError, AIError) as exc:
            raise AIError("gateway_unavailable") from exc
        if status not in (200, 503) or not isinstance(data.get("ready"), bool):
            raise AIError("gateway_unavailable", response=data)
        result = {"ready": data["ready"], "code": data.get("code") or ""}
        if isinstance(data.get("dispatch"), dict):
            result["dispatch"] = data["dispatch"]
        return result

    def _pause(self, deadline, retry=0):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AIError("client_timeout", indeterminate=True)
        self.sleep(min(remaining, 0.3 * 2 ** retry))

    def generate(self, body, key):
        deadline = time.monotonic() + self.timeout
        method, path, retries = "POST", "/v1/generate", 0
        while True:
            try:
                status, data = self._request(method, path, deadline, body=body if method == "POST" else None, key=key)
            except httpx.HTTPError as exc:
                if retries >= 2:
                    raise AIError("transport_error", indeterminate=True) from exc
                retries += 1
                self._pause(deadline, retries)
                continue
            state = data.get("status")
            if state == "completed" and status == 200 and isinstance(data.get("output"), list):
                return data
            if state in ("queued", "running") and status in (200, 202):
                response_id = data.get("id", "")
                if not isinstance(response_id, str) or not response_id.startswith("resp_") or not all(c.isalnum() or c in "_-" for c in response_id):
                    raise AIError("invalid_http_response", response=data, indeterminate=True)
                method, path = "GET", f"/v1/responses/{response_id}"
                self._pause(deadline)
                continue
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            if not data.get("id") and status in (429, 503) and error.get("retryable") and retries < 2:
                retries += 1
                self._pause(deadline, retries)
                continue
            raise AIError(error.get("code", "invalid_http_response"), response=data,
                          indeterminate=state == "indeterminate" or (state not in ("failed", "completed") and not error))
