import re
from pathlib import PureWindowsPath

from rapidfuzz import fuzz


def normalize_text(value: str) -> str:
    value = value.casefold()

    value = (
        value
        .replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
        .replace("&", " and ")
        .replace("/", " ")
        .replace("+", " ")
    )

    value = re.sub(r"[^\w\s']", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()

def get_artist_similarity(
    filename: str,
    artist: str,
) -> float:
    full_path = normalize_text(
        filename
    )

    artist_normalized = normalize_text(
        artist
    )

    if not artist_normalized:
        return 0.0

    # Coincidencia del artista completo.
    if artist_normalized in full_path:
        return 100.0

    # --------------------------------------------------
    # COLABORACIONES
    # --------------------------------------------------
    #
    # Ejemplos:
    #
    # Chase & Status feat. Burro Banton
    # Fre4knc & Klinical
    # Aries, aLr, Kelvin 373 & 4K
    #
    # Buscamos también artistas individuales.
    # --------------------------------------------------

    artist_without_feat = re.split(
        r"\b(?:feat|featuring|ft)\b",
        artist_normalized,
        maxsplit=1,
    )[0].strip()

    artist_parts = re.split(
        r"\s+and\s+|,\s*",
        artist_without_feat,
    )

    artist_parts = [
        part.strip()
        for part in artist_parts
        if part.strip()
    ]

    # Si alguno de los colaboradores aparece
    # literalmente, consideramos válido el artista.
    full_tokens = set(
        full_path.split()
    )

    for part in artist_parts:

        if part in full_path:
            return 100.0

        # Para nombres cortos como 4K o ALR
        # solo aceptamos coincidencia exacta de token.
        if (
            len(part) <= 3
            and part in full_tokens
        ):
            return 100.0

    # --------------------------------------------------
    # FUZZY
    # --------------------------------------------------
    #
    # Solo para el artista principal/completo.
    # No hacemos fuzzy con nombres cortos.
    # --------------------------------------------------

    best_score = 0.0

    candidates = [
        artist_normalized,
        artist_without_feat,
    ]

    candidates.extend(
        part
        for part in artist_parts
        if len(part) >= 4
    )

    for candidate in candidates:

        if not candidate:
            continue

        similarity = fuzz.partial_ratio(
            candidate,
            full_path,
        )

        best_score = max(
            best_score,
            similarity,
        )

    return best_score


def score_similarity(
    filename: str,
    artist: str,
    track: str,
):
    score = 0
    reasons = []

    full_path = normalize_text(filename)
    artist_normalized = normalize_text(artist)
    track_normalized = normalize_text(track)

    file_name = PureWindowsPath(filename).stem
    file_name_normalized = normalize_text(file_name)

    # --------------------------------------------------
    # ARTIST
    # --------------------------------------------------

    artist_score = get_artist_similarity(
        filename,
        artist,
    )

    artist_points = round(artist_score * 0.4)

    score += artist_points
    reasons.append(
        f"+{artist_points} Artist ({artist_score:.1f})"
    )

    # --------------------------------------------------
    # TRACK
    # --------------------------------------------------

    if (
        track_normalized
        and track_normalized in file_name_normalized
    ):
        track_score = 100.0
        track_points = 80

    else:
        track_score = fuzz.token_set_ratio(
            track_normalized,
            file_name_normalized,
        )

        track_points = round(track_score * 0.5)

    score += track_points
    reasons.append(
        f"+{track_points} Track ({track_score:.1f})"
    )

    return score, reasons