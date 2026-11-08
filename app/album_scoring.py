from rapidfuzz import fuzz


COMPILATIONS = [
    "greatest",
    "best of",
    "hits",
    "collection",
    "compilation",
    "ministry",
    "various",
    "va",
    "top 100",
    "now that's",
]


def score_album(filename: str, album: str | None):

    score = 0
    reasons = []

    filename_lower = filename.casefold()
    album_lower = album.casefold() if album else ""

    # ---------- Album ----------

    if album:

        album_ratio = fuzz.partial_ratio(
            album_lower,
            filename_lower,
        )

        if album_ratio >= 95:
            score += 35
            reasons.append(f"+35 Album ({album_ratio:.1f})")

        elif album_ratio >= 90:
            score += 25
            reasons.append(f"+25 Album ({album_ratio:.1f})")

        elif album_ratio >= 80:
            score += 10
            reasons.append(f"+10 Album ({album_ratio:.1f})")

        # Bonus si el álbum aparece literalmente en la ruta
        if album_lower in filename_lower:
            score += 20
            reasons.append("+20 Album Folder")

    # ---------- Compilations ----------

    for word in COMPILATIONS:

        # Si estamos buscando un álbum que contiene esta palabra,
        # no debemos penalizarla.
        if album and word in album_lower:
            continue

        if word in filename_lower:
            score -= 20
            reasons.append(f"-20 {word}")

    return score, reasons