import re
from .version_detector import detect_version


PENALTIES = {
    "live": 80,
    "instrumental": 120,
    "karaoke": 150,
    "acoustic": 100,
    "demo": 120,
    "radio edit": 20,
    "edit": 10,
    "mono": 20,
}


BONUSES = {
    "remastered": 5,
}


def apply_penalties(
    text: str,
    search_text: str | None = None,
):

    score = 0
    reasons = []

    lower = text.casefold()

    wanted_version = detect_version(search_text or "")

    penalty_versions = {
        "live": "live",
        "instrumental": "instrumental",
        "karaoke": "karaoke",
        "acoustic": "acoustic",
        "demo": "demo",
        "radio edit": "radio_edit",
    }

    has_radio_edit = bool(
        re.search(r"\bradio edit\b", lower)
    )

    for word, value in PENALTIES.items():

        # Evitar doble penalización:
        # "radio edit" + "edit"
        if word == "edit" and has_radio_edit:
            continue

        # No penalizar una versión que el usuario
        # está buscando expresamente
        version = penalty_versions.get(word)

        if version and wanted_version == version:
            continue

        pattern = rf"\b{re.escape(word)}\b"

        if re.search(pattern, lower):
            score -= value
            reasons.append(f"-{value} {word}")

    return score, reasons


def apply_bonuses(text: str):

    score = 0
    reasons = []

    lower = text.casefold()

    for word, value in BONUSES.items():

        pattern = rf"\b{re.escape(word)}\b"

        if re.search(pattern, lower):

            score += value
            reasons.append(f"+{value} {word}")

    return score, reasons
