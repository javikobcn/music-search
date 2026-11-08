from .version_detector import detect_version


MATCH_SCORE = 30

MISMATCH = {
    ("original", "remix"): -70,
    ("original", "live"): -50,
    ("original", "instrumental"): -120,
    ("original", "karaoke"): -150,
    ("original", "demo"): -80,
    ("original", "acoustic"): -60,
    ("original", "radio_edit"): -20,
    ("original", "edit"): -30,
    ("edit", "original"): -30,
    ("remix", "original"): -30,
    ("live", "original"): -30,
    ("acoustic", "original"): -30,
    ("instrumental", "original"): -50,
    ("karaoke", "original"): -50,
    ("demo", "original"): -30,
    ("radio_edit", "original"): -20,
    ("original", "drumless"): -80,
    ("drumless", "original"): -30,
}


def score_version(search_text: str, filename: str):

    wanted = detect_version(search_text)
    found = detect_version(filename)

    if wanted == found:
        return MATCH_SCORE, [f"+{MATCH_SCORE} {found} version"]

    penalty = MISMATCH.get((wanted, found), 0)

    if penalty:
        return penalty, [f"{penalty} expected {wanted}, got {found}"]

    return 0, []