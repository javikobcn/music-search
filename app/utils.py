def format_size(size: int) -> str:

    if size >= 1024 ** 3:
        return f"{size / (1024 ** 3):.2f} GB"

    if size >= 1024 ** 2:
        return f"{size / (1024 ** 2):.1f} MB"

    if size >= 1024:
        return f"{size / 1024:.1f} KB"

    return f"{size} B"


def format_speed(speed: int) -> str:
    if speed >= 1024 ** 2:
        return f"{speed / (1024 ** 2):.1f} MB/s"
    if speed >= 1024:
        return f"{speed / 1024:.1f} KB/s"
    return f"{speed} B/s"


def format_quality(file) -> str:

    if file.bitDepth:
        khz = (file.sampleRate or 0) / 1000
        return f"FLAC {file.bitDepth}-bit · {khz:.1f} kHz"

    if file.bitRate:
        return f"MP3 {file.bitRate} kbps"

    if file.extension:
        return file.extension.upper()

    return "Unknown"