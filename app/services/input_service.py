import re
import requests
import time

from rapidfuzz import fuzz


MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/recording"

HEADERS = {
    "User-Agent": "music-search/1.0"
}


def normalize_text(value: str) -> str:
    return (
        value.casefold()
        .replace("’", "'")
        .replace("‘", "'")
        .strip()
    )

def version_penalty(
    candidate_track: str,
    track_hint: str,
) -> int:
    candidate = normalize_text(candidate_track)
    requested = normalize_text(track_hint)

    version_words = (
        "remix",
        "live",
        "acoustic",
        "instrumental",
        "karaoke",
        "demo",
        "radio edit",
        "drumless",
    )

    for version in version_words:
        if (
            version in candidate
            and version not in requested
        ):
            return 30

    return 0


def escape_lucene(value: str) -> str:
    return (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )


def get_artist_name(recording: dict) -> str:
    parts = []

    for credit in recording.get("artist-credit", []):
        if not isinstance(credit, dict):
            continue

        name = (
            credit.get("name")
            or credit.get("artist", {}).get("name")
        )

        if name:
            parts.append(name)

        joinphrase = credit.get("joinphrase")

        if joinphrase:
            parts.append(joinphrase)

    return "".join(parts).strip()


def pair_score(
    candidate_artist: str,
    candidate_track: str,
    artist_hint: str,
    track_hint: str,
) -> float:

    artist_score = fuzz.token_set_ratio(
        normalize_text(artist_hint),
        normalize_text(candidate_artist),
    )

    track_token_score = fuzz.token_set_ratio(
        normalize_text(track_hint),
        normalize_text(candidate_track),
    )

    track_ratio_score = fuzz.ratio(
        normalize_text(track_hint),
        normalize_text(candidate_track),
    )

    # Token set mantiene la búsqueda permisiva,
    # ratio ayuda a preferir el título más parecido.
    track_score = (
        track_token_score * 0.75
        + track_ratio_score * 0.25
    )

    score = (
        artist_score * 0.5
        + track_score * 0.5
    )

    score -= version_penalty(
        candidate_track,
        track_hint,
    )

    return max(score, 0)


def resolve_track_query(query: str):

    if not query or not query.strip():
        return {
            "status": "invalid_query",
            "error": "La búsqueda está vacía",
        }

    parts = re.split(
        r"\s+[-–—]\s+",
        query.strip(),
        maxsplit=1,
    )

    if len(parts) != 2:
        return {
            "status": "invalid_format",
            "query": query,
            "error": "Usa el formato canción - artista o artista - canción",
        }

    left = parts[0].strip()
    right = parts[1].strip()

    if not left or not right:
        return {
            "status": "invalid_format",
            "query": query,
        }

    left_safe = escape_lucene(left)
    right_safe = escape_lucene(right)

    # Buscamos las dos posibilidades en una sola consulta:
    #
    # izquierda = artista / derecha = canción
    # o
    # derecha = artista / izquierda = canción

    search_query = (
        f'(artistname:"{left_safe}" AND recording:"{right_safe}") '
        f'OR '
        f'(artistname:"{right_safe}" AND recording:"{left_safe}")'
    )

    response = None

    for attempt in range(3):
        response = requests.get(
            MUSICBRAINZ_URL,
            params={
                "query": search_query,
                "fmt": "json",
                "limit": 10,
            },
            headers=HEADERS,
            timeout=15,
        )

        if response.status_code == 200:
            break

        if response.status_code in (429, 503):
            wait_seconds = 2 * (attempt + 1)

            print(
                f"[MUSICBRAINZ RETRY] "
                f"status={response.status_code} | "
                f"attempt={attempt + 1}/3 | "
                f"wait={wait_seconds}s",
                flush=True,
            )

            time.sleep(wait_seconds)
            continue

        response.raise_for_status()

    if response is None or response.status_code != 200:
        print(
            f"[MUSICBRAINZ] temporalmente no disponible | "
            f"status={response.status_code if response else None}",
            flush=True,
        )

        return {
            "status": "not_found",
            "artist": None,
            "track": None,
            "score": None,
        }

    recordings = response.json().get(
        "recordings",
        [],
    )

    if not recordings:
        return {
            "status": "not_found",
            "query": query,
        }

    candidates = []

    for recording in recordings:

        artist = get_artist_name(recording)
        track = recording.get("title", "")

        if not artist or not track:
            continue

        # Opción 1:
        # izquierda = artista
        # derecha = canción
        score_a = pair_score(
            candidate_artist=artist,
            candidate_track=track,
            artist_hint=left,
            track_hint=right,
        )

        # Opción 2:
        # derecha = artista
        # izquierda = canción
        score_b = pair_score(
            candidate_artist=artist,
            candidate_track=track,
            artist_hint=right,
            track_hint=left,
        )

        if score_a >= score_b:
            local_score = score_a
            orientation = "artist-track"
        else:
            local_score = score_b
            orientation = "track-artist"

        mb_score = int(recording.get("score", 0))

        final_score = (
            local_score * 0.9
            + mb_score * 0.1
        )

        candidates.append(
            {
                "artist": artist,
                "track": track,
                "musicbrainz_score": mb_score,
                "match_score": round(local_score, 1),
                "final_score": round(final_score, 1),
                "orientation": orientation,
            }
        )

    if not candidates:
        return {
            "status": "not_found",
            "query": query,
        }

    candidates.sort(
        key=lambda item: item["final_score"],
        reverse=True,
    )

    best = candidates[0]

    if best["match_score"] < 80:
        return {
            "status": "ambiguous",
            "query": query,
            "candidates": candidates[:3],
        }

    return {
        "status": "resolved",
        "query": query,
        "artist": best["artist"],
        "track": best["track"],
        "score": best["final_score"],
    }