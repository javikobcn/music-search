import os


def parse_filename(path: str):

    path = path.replace("\\", "/")

    parts = path.split("/")

    filename = os.path.basename(path)

    name = filename.rsplit(".", 1)[0]

    return {
        "path": path,
        "filename": filename,
        "name": name,
        "folders": parts
    }