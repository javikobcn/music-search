from pydantic import BaseModel


class SearchRequest(BaseModel):
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
    artist: str
    album: str

class ArtistRequest(BaseModel):
    artist: str

class SpotifyPlaylistRequest(BaseModel):
    url: str    