import json
import re
import time

import requests


SPOTIFY_PLAYLIST_RE = re.compile(
    r"(?:https?://)?open\.spotify\.com/"
    r"(?:intl-[a-z]{2}/)?playlist/"
    r"([A-Za-z0-9]+)",
    re.IGNORECASE,
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

PARTNER_API = (
    "https://api-partner.spotify.com/"
    "pathfinder/v1/query"
)

# Hash actual de fetchPlaylist utilizado
# por el Web Player de Spotify.
FETCH_PLAYLIST_HASH = (
    "a65e12194ed5fc443a1cdebed5fabe33"
    "ca5b07b987185d63c72483867ad13cb4"
)


def extract_playlist_id(
    url: str,
) -> str | None:
    if not url:
        return None

    match = SPOTIFY_PLAYLIST_RE.search(
        url.strip()
    )

    if not match:
        return None

    return match.group(1)


def spotify_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
):
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }

    if headers:
        request_headers.update(headers)

    last_response = None

    for attempt in range(3):
        response = requests.get(
            url,
            params=params,
            headers=request_headers,
            timeout=20,
        )

        last_response = response

        if response.status_code == 200:
            return response

        if response.status_code in (
            429,
            500,
            502,
            503,
            504,
        ):
            time.sleep(
                2 * (attempt + 1)
            )
            continue

        response.raise_for_status()

    if last_response is not None:
        last_response.raise_for_status()

    raise RuntimeError(
        "Spotify request failed"
    )


def fetch_embed_payload(
    playlist_id: str,
) -> dict:
    url = (
        "https://open.spotify.com/"
        f"embed/playlist/{playlist_id}"
    )

    response = spotify_get(url)

    match = re.search(
        r'<script[^>]*'
        r'id=["\']__NEXT_DATA__["\']'
        r'[^>]*>(.*?)</script>',
        response.text,
        re.DOTALL,
    )

    if not match:
        raise ValueError(
            "spotify_embed_payload_not_found"
        )

    return json.loads(
        match.group(1)
    )


def get_embed_entity(
    payload: dict,
) -> dict:
    page_props = (
        payload
        .get("props", {})
        .get("pageProps", {})
    )

    state = page_props.get(
        "state",
        {},
    )

    candidates = [
        (
            state
            .get("data", {})
            .get("entity")
        ),
        page_props.get("entity"),
        (
            page_props
            .get("data", {})
            .get("entity")
        ),
    ]

    for candidate in candidates:
        if isinstance(
            candidate,
            dict,
        ):
            return candidate

    raise ValueError(
        "spotify_entity_not_found"
    )


def get_embed_token(
    payload: dict,
) -> str | None:
    try:
        return (
            payload["props"]
            ["pageProps"]
            ["state"]
            ["settings"]
            ["session"]
            ["accessToken"]
        )
    except (
        KeyError,
        TypeError,
    ):
        return None


def spotify_id_from_uri(
    uri: str | None,
) -> str | None:
    if not uri:
        return None

    parts = uri.split(":")

    if not parts:
        return None

    return parts[-1]


def parse_artists(
    track: dict,
) -> list[str]:
    artists = track.get(
        "artists",
        [],
    )

    names = []

    if isinstance(artists, list):
        for artist in artists:
            if isinstance(
                artist,
                str,
            ):
                name = artist.strip()

            elif isinstance(
                artist,
                dict,
            ):
                name = (
                    artist.get("name")
                    or ""
                ).strip()

            else:
                continue

            if name:
                names.append(name)

    if names:
        return names

    subtitle = track.get(
        "subtitle"
    )

    if isinstance(
        subtitle,
        str,
    ):
        return [
            item.strip()
            for item in subtitle.split(",")
            if item.strip()
        ]

    return []


def parse_embed_track(
    item: dict,
) -> dict | None:
    track = item.get("track")

    if isinstance(track, dict):
        track = dict(track)

        if (
            not track.get("subtitle")
            and item.get("subtitle")
        ):
            track["subtitle"] = (
                item["subtitle"]
            )

    else:
        track = item

    if not isinstance(
        track,
        dict,
    ):
        return None

    spotify_id = (
        track.get("id")
        or spotify_id_from_uri(
            track.get("uri")
        )
    )

    title = (
        track.get("name")
        or track.get("title")
        or ""
    ).strip()

    artists = parse_artists(track)

    if not title or not artists:
        return None

    duration_ms = (
        track.get("duration")
        or track.get("duration_ms")
        or 0
    )

    return {
        "spotify_id": spotify_id,
        "artist": artists[0],
        "artists": artists,
        "track": title,
        "album": None,
        "length": (
            round(
                int(duration_ms)
                / 1000
            )
            if duration_ms
            else None
        ),
    }


def parse_embed_tracks(
    entity: dict,
) -> list[dict]:
    items = (
        entity.get("trackList")
        or []
    )

    tracks = []

    for item in items:
        if not isinstance(
            item,
            dict,
        ):
            continue

        track = parse_embed_track(
            item
        )

        if track:
            tracks.append(track)

    return tracks


def parse_graphql_track(
    item: dict,
) -> dict | None:
    item_v2 = item.get(
        "itemV2",
        {},
    )

    if (
        item_v2.get("__typename")
        != "TrackResponseWrapper"
    ):
        return None

    track = item_v2.get(
        "data",
        {},
    )

    if not isinstance(
        track,
        dict,
    ):
        return None

    title = (
        track.get("name")
        or ""
    ).strip()

    spotify_id = spotify_id_from_uri(
        track.get("uri")
    )

    artist_items = (
        track
        .get("artists", {})
        .get("items", [])
    )

    artists = []

    for artist in artist_items:
        if not isinstance(
            artist,
            dict,
        ):
            continue

        profile = artist.get(
            "profile",
            {},
        )

        name = (
            profile.get("name")
            or ""
        ).strip()

        if name:
            artists.append(name)

    if not title or not artists:
        return None

    album_data = (
        track.get("albumOfTrack")
        or {}
    )

    album = (
        album_data.get("name")
        if isinstance(
            album_data,
            dict,
        )
        else None
    )

    duration_ms = (
        track
        .get("trackDuration", {})
        .get("totalMilliseconds")
        or 0
    )

    return {
        "spotify_id": spotify_id,
        "artist": artists[0],
        "artists": artists,
        "track": title,
        "album": album,
        "length": (
            round(
                int(duration_ms)
                / 1000
            )
            if duration_ms
            else None
        ),
    }


def fetch_playlist_page(
    playlist_id: str,
    token: str,
    offset: int,
    limit: int = 100,
) -> dict:
    variables = {
        "uri": (
            f"spotify:playlist:"
            f"{playlist_id}"
        ),
        "offset": offset,
        "limit": limit,
        "enableWatchFeedEntrypoint": False,
        "includeEpisodeContentRatingsV2": False,
    }

    extensions = {
        "persistedQuery": {
            "version": 1,
            "sha256Hash": (
                FETCH_PLAYLIST_HASH
            ),
        }
    }

    response = spotify_get(
        PARTNER_API,
        params={
            "operationName": (
                "fetchPlaylist"
            ),
            "variables": json.dumps(
                variables,
                separators=(",", ":"),
            ),
            "extensions": json.dumps(
                extensions,
                separators=(",", ":"),
            ),
        },
        headers={
            "Authorization": (
                f"Bearer {token}"
            ),
            "app-platform": "WebPlayer",
        },
    )

    data = response.json()

    if data.get("errors"):
        raise ValueError(
            f"spotify_graphql_error: "
            f"{data['errors']}"
        )

    playlist = (
        data
        .get("data", {})
        .get("playlistV2")
    )

    if not isinstance(
        playlist,
        dict,
    ):
        raise ValueError(
            "spotify_playlist_not_found"
        )

    return playlist


def fetch_all_playlist_tracks(
    playlist_id: str,
    token: str,
) -> tuple[str | None, list[dict]]:
    offset = 0
    limit = 100

    playlist_name = None
    tracks = []

    while True:
        playlist = fetch_playlist_page(
            playlist_id=playlist_id,
            token=token,
            offset=offset,
            limit=limit,
        )

        if playlist_name is None:
            playlist_name = (
                playlist.get("name")
                or None
            )

        content = (
            playlist.get("content")
            or {}
        )

        items = (
            content.get("items")
            or []
        )

        for item in items:
            if not isinstance(
                item,
                dict,
            ):
                continue

            track = parse_graphql_track(
                item
            )

            if track:
                tracks.append(track)

        total = (
            content.get(
                "totalCount"
            )
            or 0
        )

        received = len(items)

        if received == 0:
            break

        offset += received

        if offset >= total:
            break

    return (
        playlist_name,
        tracks,
    )


def get_spotify_playlist(
    url: str,
) -> dict:
    playlist_id = extract_playlist_id(
        url
    )

    if not playlist_id:
        return {
            "status": "invalid_url",
            "error": (
                "spotify_playlist_url_required"
            ),
        }

    try:
        payload = fetch_embed_payload(
            playlist_id
        )

        entity = get_embed_entity(
            payload
        )

    except Exception as exc:
        print(
            f"[SPOTIFY EMBED ERROR] "
            f"{exc!r}",
            flush=True,
        )

        return {
            "status": "error",
            "error": (
                "spotify_embed_failed"
            ),
        }

    playlist_name = (
        entity.get("name")
        or entity.get("title")
        or "Spotify Playlist"
    )

    token = get_embed_token(
        payload
    )

    tracks = []
    source = "embed"

    if token:
        try:
            (
                graphql_name,
                tracks,
            ) = fetch_all_playlist_tracks(
                playlist_id,
                token,
            )

            if graphql_name:
                playlist_name = (
                    graphql_name
                )

            source = "graphql"

        except Exception as exc:
            print(
                f"[SPOTIFY GRAPHQL ERROR] "
                f"{exc!r}",
                flush=True,
            )

    # Fallback: si Spotify cambia GraphQL,
    # usamos las canciones visibles en el embed.
    if not tracks:
        tracks = parse_embed_tracks(
            entity
        )

        source = "embed"

    # Evitamos posibles duplicados por Spotify ID.
    clean_tracks = []
    seen = set()

    for track in tracks:
        spotify_id = track.get(
            "spotify_id"
        )

        duplicate_key = (
            spotify_id
            or (
                track["artist"].casefold(),
                track["track"].casefold(),
            )
        )

        if duplicate_key in seen:
            continue

        seen.add(
            duplicate_key
        )

        clean_tracks.append(
            track
        )

    print(
        f"[SPOTIFY PLAYLIST] "
        f"name={playlist_name!r} | "
        f"tracks={len(clean_tracks)} | "
        f"source={source}",
        flush=True,
    )

    return {
        "status": "ok",
        "source": "spotify",
        "metadata_source": source,
        "playlist": {
            "id": playlist_id,
            "name": playlist_name,
            "url": (
                "https://open.spotify.com/"
                f"playlist/{playlist_id}"
            ),
        },
        "count": len(clean_tracks),
        "tracks": clean_tracks,
    }