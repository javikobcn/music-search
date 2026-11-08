import json
from pathlib import Path

DATA_FILE = Path("/app/data/downloads.json")

MAX_HISTORY = 500

def save_downloads(downloads):

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    history = downloads[-MAX_HISTORY:]

    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(
            [job.to_dict() for job in history],
            f,
            indent=2,
            ensure_ascii=False,
        )
        

def load_downloads():

    if not DATA_FILE.exists():
        return []

    with DATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)
    