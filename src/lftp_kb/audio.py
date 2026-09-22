from __future__ import annotations

import hashlib
import mimetypes
import urllib.parse
import urllib.request
from pathlib import Path


def download_audio(url: str, cache_dir: Path, timeout: int = 120) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    extension = Path(urllib.parse.urlparse(url).path).suffix or ".audio"
    target = cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}{extension}"
    if target.exists() and target.stat().st_size > 0:
        return target
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "lftp-knowledge/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
    temporary.replace(target)
    return target


def mime_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
