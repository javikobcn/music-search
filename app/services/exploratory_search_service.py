import time
import requests

from app.services.input_service import HEADERS


MUSICBRAINZ_BASE_URL = "https://musicbrainz.org/ws/2"


def musicbrainz_get(
    endpoint: str,
    params: dict,
) -> dict | None:
    for attempt in range(3):
        response = requests.get(
            f"{MUSICBRAINZ_BASE_URL}/{endpoint}",
            params={
                **params,
                "fmt": "json",
            },
            headers=HEADERS,
            timeout=15,
        )

        if response.status_code == 200:
            return response.json()

        if response.status_code in (429, 503):
            wait_seconds = 2 * (attempt + 1)

            print(
                f"[MUSICBRAINZ SEARCH RETRY] "
                f"status={response.status_code} | "
                f"attempt={attempt + 1}/3 | "
                f"wait={wait_seconds}s",
                flush=True,
            )

            time.sleep(wait_seconds)
            continue

        response.raise_for_status()

    return None


def lucene_quote(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')

    return f'"{text}"'


def artist_credit_text(
    recording: dict,
) -> str:
    credits = recording.get(
        "artist-credit",
        [],
    )

    parts = []

    for credit in credits:
        if isinstance(credit, str):
            parts.append(credit)
            continue

        name = (
            credit.get("name")
            or credit.get(
                "artist",
                {},
            ).get("name")
            or ""
        )

        parts.append(name)
        parts.append(
            credit.get("joinphrase", "")
        )

    return "".join(parts).strip()


def recording_to_dict(
    recording: dict,
) -> dict:
    length_ms = recording.get("length")

    if length_ms:
        length_seconds = round(
            length_ms / 1000
        )
    else:
        length_seconds = None

    return {
        "id": recording.get("id"),
        "artist": artist_credit_text(
            recording
        ),
        "track": recording.get(
            "title",
            "Desconocida",
        ),
        "length": length_seconds,
        "score": int(
            recording.get("score", 0)
            or 0
        ),
    }

def normalize_explore_text(text: str) -> str:
    normalized = text.casefold().strip()

    if normalized.startswith("the "):
        normalized = normalized[4:]

    return normalized

def search_options(
    query: str,
) -> dict:
    query = query.strip()

    quoted = lucene_quote(query)

    # Una sola búsqueda de recordings que contempla
    # coincidencias tanto en título como en artista.
    recordings_data = musicbrainz_get(
        "recording",
        {
            "query": f"recording:{quoted}",
            "limit": 50,
        },
    )

    # Respetamos el ritmo de peticiones de MusicBrainz.
    time.sleep(1.1)

    artists_data = musicbrainz_get(
        "artist",
        {
            "query": f"artist:{quoted}",
            "limit": 5,
        },
    )

    if (
        recordings_data is None
        and artists_data is None
    ):
        return {
            "status": "temporarily_unavailable",
            "query": query,
            "tracks": [],
            "artists": [],
        }

    tracks = []

    seen_tracks = set()

    for recording in (
        recordings_data or {}
    ).get("recordings", []):
        item = recording_to_dict(
            recording
        )

        if (
            not item["artist"]
            or not item["track"]
        ):
            continue

        duplicate_key = (
            normalize_explore_text(
                item["artist"]
            ),
            normalize_explore_text(
                item["track"]
            ),
        )

        if duplicate_key in seen_tracks:
            continue

        seen_tracks.add(
            duplicate_key
        )

        tracks.append(item)

    query_normalized = normalize_explore_text(
        query
    )


    def track_sort_key(item):
        artist_normalized = normalize_explore_text(
            item["artist"]
        )

        track_normalized = normalize_explore_text(
            item["track"]
        )

        artist_exact = (
            artist_normalized == query_normalized
        )

        track_exact = (
            track_normalized == query_normalized
        )

        artist_contains = (
            query_normalized in artist_normalized
        )

        track_contains = (
            query_normalized in track_normalized
        )

        return (
            artist_exact,
            track_exact,
            artist_contains,
            track_contains,
            item["score"],
        )


    tracks.sort(
        key=track_sort_key,
        reverse=True,
    )

    artists = []

    for artist in (
        artists_data or {}
    ).get("artists", []):
        artist_id = artist.get("id")
        artist_name = artist.get("name")

        if (
            not artist_id
            or not artist_name
        ):
            continue

        artists.append(
            {
                "id": artist_id,
                "name": artist_name,
                "score": int(
                    artist.get("score", 0)
                    or 0
                ),
                "type": artist.get("type"),
                "disambiguation": artist.get(
                    "disambiguation"
                ),
            }
        )

    def artist_sort_key(item):
        artist_normalized = normalize_explore_text(
            item["name"]
        )

        exact = (
            artist_normalized == query_normalized
        )

        contains = (
            query_normalized in artist_normalized
        )

        return (
            exact,
            contains,
            item["score"],
        )


    artists.sort(
        key=artist_sort_key,
        reverse=True,
    )

    return {
        "status": "ok",
        "query": query,
        "tracks": tracks[:30],
        "artists": artists[:5],
    }


def get_artist_tracks(
    artist_id: str,
) -> dict:
    data = musicbrainz_get(
        "recording",
        {
            "query": f"arid:{artist_id}",
            "limit": 100,
        },
    )

    if data is None:
        return {
            "status": "temporarily_unavailable",
            "tracks": [],
        }

    tracks = []
    seen = set()

    special_words = (
        "remix",
        "live",
        "instrumental",
        "karaoke",
        "demo",
        "acoustic",
        "extended",
        "radio edit",
        "mix)",
        "mix]",
    )

    for recording in data.get(
        "recordings",
        [],
    ):
        item = recording_to_dict(
            recording
        )

        if (
            not item["artist"]
            or not item["track"]
        ):
            continue

        duplicate_key = (
            item["track"]
            .casefold()
            .strip()
        )

        if duplicate_key in seen:
            continue

        seen.add(duplicate_key)

        title_lower = (
            item["track"]
            .casefold()
        )

        item["_special_version"] = any(
            word in title_lower
            for word in special_words
        )

        tracks.append(item)

    tracks.sort(
        key=lambda item: (
            item["_special_version"],
            item["track"].casefold(),
            item.get("length") or 0,
        )
    )

    for item in tracks:
        item.pop(
            "_special_version",
            None,
        )

    return {
        "status": "ok",
        "tracks": tracks,
    }