from .musicbrainz_service import get_album_tracks
from .music_service import get_track
from .queue_service import find_by_job_id

from threading import Thread, RLock
from uuid import uuid4
import logging


logger = logging.getLogger(__name__)

_album_tasks = {}
_album_tasks_lock = RLock()


def start_album_download(
    artist: str,
    album: str,
    quality_mode: str = "best",
):
    task_id = str(
        uuid4()
    )

    with _album_tasks_lock:
        _album_tasks[task_id] = {
            "task_id": task_id,
            "artist": artist,
            "album": album,
            "status": "analyzing",
            "tracks_found": 0,
            "processed": 0,
            "results": [],
            "error": None,
        }

    thread = Thread(
        target=_run_album_download,
        args=(
            task_id,
            artist,
            album,
            quality_mode,
        ),
        daemon=True,
        name=f"album-{task_id[:8]}",
    )

    thread.start()

    return {
        "status": "started",
        "task_id": task_id,
        "artist": artist,
        "album": album,
    }


def _run_album_download(
    task_id: str,
    artist: str,
    album: str,
    quality_mode: str = "best",
):
    try:
        tracks = get_album_tracks(
            artist,
            album,
        )

        print(
            "ALBUM TRACKS:",
            tracks,
            flush=True,
        )

        if not tracks:
            with _album_tasks_lock:
                task = _album_tasks[
                    task_id
                ]

                task["status"] = (
                    "not_found"
                )

            return

        with _album_tasks_lock:
            task = _album_tasks[
                task_id
            ]

            task["tracks_found"] = len(
                tracks
            )

            task["status"] = (
                "processing"
            )

        for track in tracks:
            try:
                result = get_track(
                    artist=artist,
                    track=track,
                    album=album,
                    quality_mode=quality_mode,
                )

            except Exception as exc:
                logger.exception(
                    f"[ALBUM TASK] "
                    f"Error procesando "
                    f"{artist!r} - "
                    f"{track!r}"
                )

                result = {
                    "status": "failed",
                    "error": str(exc),
                }

            with _album_tasks_lock:
                task = _album_tasks[
                    task_id
                ]

                task["results"].append(
                    {
                        "track": track,
                        "result": result,
                    }
                )

                task["processed"] += 1

        with _album_tasks_lock:
            task = _album_tasks[
                task_id
            ]

            task["status"] = (
                "search_complete"
            )

    except Exception as exc:
        logger.exception(
            f"[ALBUM TASK ERROR] "
            f"artist={artist!r} | "
            f"album={album!r}"
        )

        with _album_tasks_lock:
            task = _album_tasks.get(
                task_id
            )

            if task is not None:
                task["status"] = "failed"
                task["error"] = str(exc)


def get_album_download_task(
    task_id: str,
):
    with _album_tasks_lock:
        task = _album_tasks.get(
            task_id
        )

        if task is None:
            return {
                "status": "not_found",
                "task_id": task_id,
            }

        artist = task["artist"]
        album = task["album"]

        task_status = task["status"]

        tracks_found = task[
            "tracks_found"
        ]

        processed = task[
            "processed"
        ]

        error = task.get(
            "error"
        )

        results = list(
            task["results"]
        )

    downloaded_count = 0
    downloading_count = 0
    waiting_count = 0
    failed_count = 0

    completed_tracks = []
    failed_tracks = []

    for entry in results:
        track = entry.get(
            "track",
            "Desconocida",
        )

        result = (
            entry.get("result")
            or {}
        )

        status = result.get(
            "status"
        )

        job_id = result.get(
            "job_id"
        )

        # ------------------------------------------
        # Ya estaba descargada antes de comenzar
        # el álbum.
        # ------------------------------------------

        if status == "completed":
            download = (
                result.get("download")
                or {}
            )

            local_path = (
                download.get("local_path")
                or result.get("local_path")
            )

            downloaded_count += 1

            if local_path:
                completed_tracks.append(
                    {
                        "artist": artist,
                        "track": track,
                        "local_path": local_path,
                    }
                )

            continue

        # ------------------------------------------
        # Tiene un job real: consultar su estado
        # actual.
        # ------------------------------------------

        if job_id:
            job = find_by_job_id(
                job_id
            )

            if job is not None:
                current_status = (
                    job.status
                )

                if current_status == "completed":
                    downloaded_count += 1

                    if job.local_path:
                        completed_tracks.append(
                            {
                                "artist": artist,
                                "track": track,
                                "local_path": (
                                    job.local_path
                                ),
                            }
                        )

                    continue

                if current_status == "downloading":
                    downloading_count += 1
                    continue

                if current_status in (
                    "queued",
                    "starting",
                    "waiting_remote",
                ):
                    waiting_count += 1
                    continue

                if current_status in (
                    "failed",
                    "cancelled",
                ):
                    failed_count += 1

                    failed_tracks.append(
                        track
                    )

                    continue

        # ------------------------------------------
        # Resultado terminal sin job.
        # ------------------------------------------

        if status in (
            "failed",
            "cancelled",
            "not_found",
        ):
            failed_count += 1

            failed_tracks.append(
                track
            )

        elif status in (
            "queued",
            "starting",
            "waiting_remote",
            "downloading",
        ):
            # Puede existir una pequeña ventana
            # antes de que find_by_job_id lo vea.
            if status == "downloading":
                downloading_count += 1
            else:
                waiting_count += 1

        elif task_status == "search_complete":
            # Una búsqueda ya procesada pero sin
            # estado utilizable se considera fallida.
            failed_count += 1

            failed_tracks.append(
                track
            )

    # Las canciones que todavía ni siquiera han
    # pasado por get_track aparecen como "en espera".
    unprocessed = max(
        0,
        tracks_found - processed,
    )

    waiting_count += unprocessed

    finished_count = (
        downloaded_count
        + failed_count
    )

    done = (
        task_status
        in (
            "search_complete",
            "not_found",
            "failed",
        )
        and (
            tracks_found == 0
            or finished_count
            >= tracks_found
        )
    )

    return {
        "status": task_status,
        "task_id": task_id,
        "artist": artist,
        "album": album,
        "tracks_found": tracks_found,
        "processed": processed,
        "downloaded": downloaded_count,
        "downloading": downloading_count,
        "waiting": waiting_count,
        "failed": failed_count,
        "finished": finished_count,
        "done": done,
        "completed_tracks": completed_tracks,
        "failed_tracks": failed_tracks,
        "error": error,
    }


# Mantenemos el método antiguo por compatibilidad.
def get_album(
    artist: str,
    album: str,
    quality_mode: str = "best",
):
    tracks = get_album_tracks(
        artist,
        album,
    )

    print(
        "ALBUM TRACKS:",
        tracks,
    )

    if not tracks:
        return {
            "status": "not_found",
            "artist": artist,
            "album": album,
        }

    results = []

    for track in tracks:
        result = get_track(
            artist=artist,
            track=track,
            album=album,
            quality_mode=quality_mode,
        )

        results.append(
            result
        )

    return {
        "status": "queued",
        "artist": artist,
        "album": album,
        "tracks_found": len(tracks),
        "results": results,
    }