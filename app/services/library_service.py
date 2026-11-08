from pathlib import Path

MUSIC_ROOT = Path("/music")


def exists(filename: str) -> bool:

    name = Path(filename).stem.lower()

    for file in MUSIC_ROOT.rglob("*"):

        if file.is_file():

            if file.stem.lower() == name:

                return True

    return False