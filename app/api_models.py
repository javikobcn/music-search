from pydantic import BaseModel


class TrackResponse(BaseModel):
    artist: str
    title: str
    album: str | None = None
    quality: str
    size: str


class SourceResponse(BaseModel):
    username: str
    queue: int
    speed: str


class QueueResponse(BaseModel):
    status: str
    position: int
    track: TrackResponse
    source: SourceResponse