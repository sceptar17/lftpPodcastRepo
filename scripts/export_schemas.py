import json
from pathlib import Path

from lftp_kb.models import Episode, Topic, TranscriptionResult

root = Path(__file__).resolve().parents[1] / "schemas"
root.mkdir(exist_ok=True)
for model in (Episode, Topic, TranscriptionResult):
    (root / f"{model.__name__.lower()}.schema.json").write_text(
        json.dumps(model.model_json_schema(), indent=2) + "\n"
    )

