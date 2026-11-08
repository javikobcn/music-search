import requests


BASE_URL = "https://musicbrainz.org/ws/2"

HEADERS = {
    "User-Agent": "music-search/1.0 (https://github.com/tu-proyecto)"
}


def search_release(artist: str, album: str) -> str | None:

    query = f'artist:"{artist}" AND release:"{album}"'

    response = requests.get(
        f"{BASE_URL}/release",
        params={
            "query": query,
            "fmt": "json",
            "limit": 1,
        },
        headers=HEADERS,
        timeout=10,
    )

    response.raise_for_status()

    releases = response.json().get("releases", [])

    if not releases:
        return None

    return releases[0]["id"]


def get_album_tracks(artist: str, album: str):

    release_id = search_release(artist, album)

    if release_id is None:
        return []

    response = requests.get(
        f"{BASE_URL}/release/{release_id}",
        params={
            "inc": "recordings",
            "fmt": "json",
        },
        headers=HEADERS,
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()

    tracks = []

    for medium in data.get("media", []):

        for track in medium.get("tracks", []):

            tracks.append(track["title"])

    return tracks