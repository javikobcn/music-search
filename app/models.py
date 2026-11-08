from pydantic import BaseModel
from typing import Literal


class SearchRequest(BaseModel):
    quality_mode: Literal["best", "mp3_320"] = "best"
    query: str | None = None
    artist: str | None = None
    track: str | None = None
    album: str | None = None
    playlist: str | None = None

class QueryRequest(BaseModel):
    query: str  

class ArtistTracksRequest(BaseModel):
    artist_id: str  

class DownloadRequest(BaseModel):
    username: str
    filename: str
    size: int

class AlbumRequest(BaseModel):
    quality_mode: Literal["best", "mp3_320"] = "best"
    artist: str
    album: str

class ArtistRequest(BaseModel):
    artist: str

class SpotifyPlaylistRequest(BaseModel):
    url: str    