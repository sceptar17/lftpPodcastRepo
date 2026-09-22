from __future__ import annotations

import difflib
import json
import re
from pathlib import Path


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", value.lower())


def compare_files(paths: list[Path]) -> dict:
    documents = []
    for path in paths:
        payload = json.loads(path.read_text())
        documents.append({"path": str(path), "provider": payload.get("provider"),
                          "model": payload.get("model"), "text": payload.get("text", "")})
    pairs = []
    for index, left in enumerate(documents):
        for right in documents[index + 1:]:
            ratio = difflib.SequenceMatcher(a=_words(left["text"]), b=_words(right["text"])).ratio()
            pairs.append({"left": left["path"], "right": right["path"],
                          "word_sequence_similarity": round(ratio, 4),
                          "left_word_count": len(_words(left["text"])),
                          "right_word_count": len(_words(right["text"]))})
    return {"documents": [{k: v for k, v in d.items() if k != "text"} for d in documents], "pairs": pairs}

