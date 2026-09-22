from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request


class WordPressError(RuntimeError):
    pass


class WordPressClient:
    def __init__(self, base_url: str, username: str, application_password: str, timeout: int = 60):
        self.endpoint = base_url.rstrip("/") + "/wp-json/wp/v2/posts"
        token = base64.b64encode(f"{username}:{application_password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json",
                        "User-Agent": "lftp-knowledge/0.1"}
        self.timeout = timeout

    def _request(self, method: str, url: str, payload: dict) -> dict:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), method=method,
                                         headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise WordPressError(f"WordPress HTTP {exc.code}: {body[:500]}") from exc

    def create_or_update_draft(self, title: str, content: str, existing_post_id: int | None = None) -> dict:
        payload = {"title": title, "content": content, "status": "draft"}
        if existing_post_id:
            return self._request("POST", f"{self.endpoint}/{existing_post_id}", payload)
        return self._request("POST", self.endpoint, payload)

