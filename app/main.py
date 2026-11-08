import os
import requests
from fastapi import FastAPI, Header
from .models import (
    SearchRequest,
    DownloadRequest,
    AlbumRequest,
    QueryRequest,
    ArtistRequest,
    ArtistTracksRequest,
    SpotifyPlaylistRequest,
)
from .services.music_service import get_track
from .slskd import SlskdClient
from .services.queue_service import (
    downloads,
    load_history,
    queue,
    cancel_queued,
    find_by_job_id,
    persist,
)
from .worker import start_worker
import logging
from .monitor import start_monitor, finalize_playlist_folder
from .services.album_service import (
    get_album,
    start_album_download,
    get_album_download_task,
)
from datetime import datetime
from .services.input_service import resolve_track_query
from .services.artist_album_service import get_artist_albums
from app.services.exploratory_search_service import (
    search_options,
    get_artist_tracks,
)
from app.services.spotify_service import (
    get_spotify_playlist,
)
from app.services.manual_playlist_service import (
    list_manual_playlists,
    create_manual_playlist,
    add_paths_to_manual_playlist,
    find_playlists_containing_path,
    get_audio_directory_stats,
)


logger = logging.getLogger(__name__)

app = FastAPI()


@app.on_event("startup")
def startup_event():

    logger.warning("[APP] Startup")

    load_history()
    start_worker()
    start_monitor()

@app.get("/")
def root():
    return {
        "status": "ok"
    }


@app.get("/login")
def login():

    client = SlskdClient()

    data = client.login()

    return {
        "name": data["name"],
        "expires": data["expires"]
    }

@app.get("/queue")
def get_queue():

    return [
        {
            "position": position,
            **job.to_dict(),
        }
        for position, job in enumerate(
            queue(),
            start=1,
        )
    ]

@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = find_by_job_id(job_id)

    if job is None:
        return {
            "status": "not_found",
            "job_id": job_id,
        }

    return job.to_dict()
    
    
@app.post("/search-and-download-album")
def search_download_album(request: AlbumRequest):

    return get_album(
        artist=request.artist,
        album=request.album,
    )

@app.post("/album-download-tasks")
def start_album_download_endpoint(
    request: AlbumRequest,
):
    return start_album_download(
        artist=request.artist,
        album=request.album,
    )


@app.get("/album-download-tasks/{task_id}")
def get_album_download_task_endpoint(
    task_id: str,
):
    return get_album_download_task(
        task_id
    )


@app.post("/albums")
def search_artist_albums(request: ArtistRequest):
    return get_artist_albums(request.artist)


@app.post("/search-and-download")
def search_and_download(request: SearchRequest):

    result = get_track(
        artist=request.artist,
        track=request.track,
        album=request.album,
        playlist=request.playlist,
    )

    return result


@app.post("/download")
def download(request: DownloadRequest):

    client = SlskdClient()

    client.login()

    return client.download(
        username=request.username,
        filename=request.filename,
        size=request.size,
    )

@app.get("/downloads")
def get_downloads():

    return [job.to_dict() for job in downloads()]

@app.get("/downloads/{download_id}")
def get_download(download_id: str):

    for job in downloads():

        if job.download_id == download_id:
            return job.to_dict()

    return {
        "status": "not_found"
    }

@app.post("/test-album")
def test_album(request: AlbumRequest):

    return get_album(
        artist=request.artist,
        album=request.album,
    )

@app.post("/resolve-track")
def resolve_track(request: QueryRequest):
    return resolve_track_query(request.query)

@app.post("/search-options")
def search_options_endpoint(
    request: QueryRequest,
):
    return search_options(
        request.query
    )

@app.post("/spotify-playlist")
def spotify_playlist_endpoint(
    request: SpotifyPlaylistRequest,
):
    return get_spotify_playlist(
        request.url
    )


@app.post("/artist-tracks")
def artist_tracks_endpoint(
    request: ArtistTracksRequest,
):
    return get_artist_tracks(
        request.artist_id
    )

@app.post("/finalize-playlist")
def finalize_playlist(request: dict):
    playlist = request.get("playlist")

    final_name = (
        request.get("final_name")
        or ""
    ).strip()

    if not playlist:
        return {
            "status": "error",
            "error": "playlist_required",
        }

    destination = finalize_playlist_folder(
        playlist=playlist,
        final_name=final_name or None,
    )

    if destination is None:
        return {
            "status": "not_found",
            "playlist": playlist,
        }

    stats = get_audio_directory_stats(
        destination
    )

    return {
        "status": "completed",
        "playlist": playlist,
        "final_name": (
            final_name
            or playlist
        ),
        "destination": str(destination),
        "track_count": stats[
            "track_count"
        ],
        "duration_seconds": stats[
            "duration_seconds"
        ],
        "duration_known": stats[
            "duration_known"
        ],
        "size_bytes": stats[
            "size_bytes"
        ],
        "size_known": stats[
            "size_known"
        ],        
    }

@app.get("/manual-playlists")
def manual_playlists():
    playlists = list_manual_playlists()

    return {
        "status": "ok",
        "count": len(playlists),
        "playlists": playlists,
    }

@app.post(
    "/manual-playlists/containing-track"
)
def manual_playlists_containing_track(
    request: dict,
):
    local_path = (
        request.get("local_path")
        or ""
    ).strip()

    if not local_path:
        return {
            "status": "error",
            "error": "local_path_required",
        }

    return find_playlists_containing_path(
        local_path
    )


@app.post("/manual-playlists/create")
def manual_playlist_create(
    request: dict,
):
    playlist = (
        request.get("playlist")
        or ""
    ).strip()

    if not playlist:
        return {
            "status": "error",
            "error": "playlist_required",
        }

    return create_manual_playlist(
        playlist
    )


@app.post("/manual-playlists/add-tracks")
def manual_playlist_add_tracks(
    request: dict,
):
    playlist = (
        request.get("playlist")
        or ""
    ).strip()

    local_paths = (
        request.get("local_paths")
        or []
    )

    create_if_missing = bool(
        request.get(
            "create_if_missing",
            False,
        )
    )

    if not playlist:
        return {
            "status": "error",
            "error": "playlist_required",
        }

    if not isinstance(
        local_paths,
        list,
    ):
        return {
            "status": "error",
            "error": "local_paths_must_be_list",
        }

    local_paths = [
        str(path)
        for path in local_paths
        if path
    ]

    if not local_paths:
        return {
            "status": "error",
            "error": "local_paths_required",
        }

    return add_paths_to_manual_playlist(
        playlist_name=playlist,
        local_paths=local_paths,
        create_if_missing=create_if_missing,
    )

@app.delete("/downloads/{job_id}")
def cancel_download_job(job_id: str):

    job = find_by_job_id(job_id)

    if job is None:
        return {
            "status": "not_found",
            "job_id": job_id,
        }

    if job.status == "queued":

        cancelled = cancel_queued(job_id)

        return {
            "status": "cancelled",
            "job": cancelled.to_dict(),
        }

    if job.status == "starting":

        job.status = "cancelled"
        job.finished_at = datetime.now()
        persist()

        return {
            "status": "cancelled",
            "job": job.to_dict(),
        }

    if job.status in ("waiting_remote", "downloading"):

        if not job.download_id:
            return {
                "status": "cannot_cancel",
                "job_id": job_id,
                "reason": "download_id is missing",
            }

        previous_status = job.status

        job.status = "cancelled"
        job.finished_at = datetime.now()
        persist()

        client = SlskdClient()
        client.login()

        try:
            cancelled = client.cancel_download(
                username=job.username,
                download_id=job.download_id,
            )

        except Exception:
            job.status = previous_status
            job.finished_at = None
            persist()
            raise

        if not cancelled:
            job.status = previous_status
            job.finished_at = None
            persist()

            return {
                "status": "not_found_remote",
                "job_id": job_id,
                "download_id": job.download_id,
            }

        return {
            "status": "cancelled",
            "job": job.to_dict(),
        }

    return {
        "status": "not_active",
        "job": job.to_dict(),
    }

@app.post("/slskd/private-message")
def slskd_private_message(
    payload: dict,
    x_slskd_webhook_token: str | None = Header(
        default=None,
        alias="X-SLSKD-Webhook-Token",
    ),
):
    expected_token = os.getenv(
        "SLSKD_WEBHOOK_TOKEN"
    )

    if (
        not expected_token
        or x_slskd_webhook_token
        != expected_token
    ):
        return {
            "status": "unauthorized"
        }

    telegram_token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    telegram_user_id = os.getenv(
        "TELEGRAM_ALLOWED_USER_ID"
    )

    if (
        not telegram_token
        or not telegram_user_id
    ):
        return {
            "status": "error",
            "error": "telegram_not_configured",
        }

    username = (
        payload.get("username")
        or payload.get("user")
        or payload.get("from")
        or "Desconocido"
    )

    message = (
        payload.get("message")
        or payload.get("text")
        or payload.get("content")
        or "Sin contenido"
    )

    text = (
        "💬 Nuevo mensaje en Soulseek\n\n"
        f"👤 {username}\n\n"
        f"📝 {message}"
    )

    response = requests.post(
        (
            f"https://api.telegram.org/"
            f"bot{telegram_token}/sendMessage"
        ),
        json={
            "chat_id": telegram_user_id,
            "text": text,
        },
        timeout=10,
    )

    response.raise_for_status()

    return {
        "status": "ok"
    }