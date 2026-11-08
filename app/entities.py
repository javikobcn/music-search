from dataclasses import dataclass
from datetime import datetime
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class SearchResult:

    username: str
    filename: str

    size: int

    score: int
    reasons: list

    code: int | None = None
    extension: str | None = None

    length: int | None = None

    bitDepth: int |None = None
    bitRate: int | None = None
    sampleRate: int | None = None

    isLocked: bool = False

    queueLength: int = 0
    uploadSpeed: int = 0
    hasFreeUploadSlot: bool = False

    def __post_init__(self):
        filename_normalized = self.filename.replace("\\", "/")

        if "." in filename_normalized:
            real_extension = (
                filename_normalized
                .rsplit(".", 1)[-1]
                .casefold()
            )

            if real_extension:
                self.extension = real_extension


@dataclass
class DownloadJob:
    # Información de la canción
    artist: str
    track: str
    album: str | None = None
    playlist: str | None = None

    # Fuente (Soulseek)
    username: str = ""
    filename: str = ""
    size: int = 0
    score: int = 0
    extension: str | None = None
    length: int | None = None

    # Estado de la descarga
    status: str = "queued"
    progress: float = 0.0
    downloaded: int = 0
    remaining: int = 0
    speed: float = 0.0
    job_id: str = field(default_factory=lambda: str(uuid4()))
    download_id: str | None = None
    local_path: str | None = None
    local_size: int | None = None
    retry_count: int = 0
    failed_sources: list[str] = field(default_factory=list)

    # Error si lo hubiera
    error: str | None = None

    # Fechas
    queued_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def to_dict(self):
        return {
            "artist": self.artist,
            "track": self.track,
            "album": self.album,
            "playlist": self.playlist,
            "filename": self.filename,
            "size": self.size,
            "status": self.status,
            "progress": round(self.progress, 1),
            "downloaded": self.downloaded,
            "remaining": self.remaining,
            "speed": self.speed,
            "username": self.username,
            "score": self.score,
            "download_id": self.download_id,
            "local_path": self.local_path,
            "local_size": self.local_size,
            "error": self.error,
            "queued_at": self.queued_at.isoformat() if self.queued_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "job_id": self.job_id,
            "retry_count": self.retry_count,
            "failed_sources": self.failed_sources,
            "extension": self.extension,
            "length": self.length,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            username=data["username"],
            filename=data.get("filename", ""),
            size=data.get("size", 0),
            score=data["score"],
            artist=data["artist"],
            track=data["track"],
            album=data.get("album"),
            playlist=data.get("playlist"),
            status=data["status"],
            progress=data.get("progress", 0),
            downloaded=data.get("downloaded", 0),
            remaining=data.get("remaining", 0),
            speed=data.get("speed", 0),
            extension=data.get("extension"),
            length=data.get("length"),
            download_id=data.get("download_id"),
            error=data.get("error"),
            local_path=data.get("local_path"),
            local_size=data.get("local_size"),
            queued_at=(
                datetime.fromisoformat(data["queued_at"])
                if data.get("queued_at")
                else datetime.now()
            ),
            started_at=(
                datetime.fromisoformat(data["started_at"])
                if data.get("started_at")
                else None
            ),
            finished_at=(
                datetime.fromisoformat(data["finished_at"])
                if data.get("finished_at")
                else None
            ),
            job_id=data.get(
                "job_id",
                str(uuid4()),
            ),
            retry_count=data.get("retry_count", 0),
            failed_sources=data.get("failed_sources", []),
        )