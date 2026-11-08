import re


VERSIONS = {
    "remix": [
        "remix",
        "mix",
        "club mix",
        "extended mix",
        "dub mix",
    ],

    "live": [
        "live",
    ],

    "acoustic": [
        "acoustic",
    ],

    "instrumental": [
        "instrumental",
    ],

    "karaoke": [
        "karaoke",
    ],

    "demo": [
        "demo",
    ],

    "radio_edit": [
        "radio edit",
    ],

    "edit": [
        "edit",
    ],

    "drumless": [
        "drumless",
        "drumless edition",
    ],
}


def detect_version(text: str) -> str:

    lower = text.casefold()

    for version, words in VERSIONS.items():

        for word in words:

            pattern = rf"\b{re.escape(word)}\b"

            if re.search(pattern, lower):

                return version

    return "original"