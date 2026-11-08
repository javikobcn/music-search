from pathlib import PureWindowsPath
import re


def parse_filename(filename: str):

    path = PureWindowsPath(filename)

    parts = list(path.parts)

    name = path.stem

    album = None

    if len(parts) >= 2:
        album = parts[-2]

    track = None

    m = re.match(r"^(\d+)", name)

    if m:
        track = int(m.group(1))

    return {
        "album": album,
        "track": track,
        "name": name,
    }