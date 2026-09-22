import json
from unittest.mock import patch

from lftp_kb.wordpress import WordPressClient


class Response:
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self): return json.dumps({"id": 42, "link": "https://example.com/?p=42"}).encode()


def test_existing_post_is_updated_not_duplicated():
    client = WordPressClient("https://example.com", "user", "app pass")
    with patch("urllib.request.urlopen", return_value=Response()) as opened:
        response = client.create_or_update_draft("Title", "Body", existing_post_id=42)
    assert response["id"] == 42
    assert opened.call_args.args[0].full_url.endswith("/wp-json/wp/v2/posts/42")
    assert json.loads(opened.call_args.args[0].data)["status"] == "draft"

