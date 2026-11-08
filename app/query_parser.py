from dataclasses import dataclass
import re


@dataclass
class ParsedQuery:

    type: str

    artist: str | None = None
    track: str | None = None
    album: str | None = None

    url: str | None = None


def parse(query: str) -> ParsedQuery:

    query = query.strip()

    # 1. URL
    if re.match(r"^https?://", query):

        return ParsedQuery(
            type="url",
            url=query,
        )

    # 2. Artista - Canción
    if " - " in query:

        artist, track = query.split(" - ", 1)

        return ParsedQuery(
            type="artist_title",
            artist=artist.strip(),
            track=track.strip(),
        )

    # 3. Desconocido
    return ParsedQuery(
        type="unknown",
    )