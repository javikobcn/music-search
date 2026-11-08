from pathlib import Path

from mutagen import File


def get_real_audio_length(
    file_path: str | Path,
) -> int | None:
    try:
        audio = File(
            str(file_path)
        )

        if (
            audio is None
            or audio.info is None
        ):
            return None

        length = getattr(
            audio.info,
            "length",
            None,
        )

        if length is None:
            return None

        return round(
            float(length)
        )

    except Exception as exc:
        print(
            f"[AUDIO LENGTH ERROR] "
            f"file={str(file_path)!r} | "
            f"error={exc!r}",
            flush=True,
        )

        return None