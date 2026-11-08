import requests


MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2"

HEADERS = {
    "User-Agent": "music-search/1.0"
}


def find_artist(artist_name: str):
    response = requests.get(
        f"{MUSICBRAINZ_URL}/artist",
        params={
            "query": f'artist:"{artist_name}"',
            "fmt": "json",
            "limit": 5,
        },
        headers=HEADERS,
        timeout=15,
    )

    response.raise_for_status()

    artists = response.json().get("artists", [])

    if not artists:
        return None

    return artists[0]


def get_artist_albums(artist_name: str):
    artist = find_artist(artist_name)

    if artist is None:
        return {
            "status": "not_found",
            "artist": artist_name,
            "albums": [],
        }

    artist_id = artist["id"]
    resolved_artist = artist["name"]

    response = requests.get(
        f"{MUSICBRAINZ_URL}/release-group",
        params={
            "artist": artist_id,
            "type": "album",
            "release-group-status": "website-default",
            "fmt": "json",
            "limit": 100,
        },
        headers=HEADERS,
        timeout=15,
    )

    response.raise_for_status()

    release_groups = response.json().get(
        "release-groups",
        []
    )

    albums = []

    for release_group in release_groups:
        title = release_group.get("title")

        if not title:
            continue

        secondary_types = release_group.get(
            "secondary-types",
            []
        )

        albums.append(
            {
                "id": release_group["id"],
                "title": title,
                "date": release_group.get(
                    "first-release-date"
                ),
                "secondary_types": secondary_types,
            }
        )

    albums.sort(
        key=lambda album: (
            album["date"] or "9999",
            album["title"].casefold(),
        )
    )

    return {
        "status": "resolved",
        "artist": resolved_artist,
        "artist_id": artist_id,
        "albums": albums,
    }