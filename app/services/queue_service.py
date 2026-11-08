from rapidfuzz import fuzz
from collections import deque
from ..entities import DownloadJob
from .storage_service import save_downloads, load_downloads
from datetime import datetime
import threading
import re

# Cola pendiente de procesar por el worker
_queue = deque()

# Todas las descargas (pendientes, activas y finalizadas)
_downloads: list[DownloadJob] = []

_state_lock = threading.RLock()


def enqueue(job: DownloadJob) -> int:

    with _state_lock:

        if job not in _downloads:
            _downloads.append(job)

        if job not in _queue:
            _queue.append(job)

        persist()

        return len(_queue)


def requeue(job: DownloadJob) -> int:

    with _state_lock:

        if job not in _downloads:
            _downloads.append(job)

        if job not in _queue:
            _queue.append(job)

        persist()

        return len(_queue)


def dequeue() -> DownloadJob | None:

    with _state_lock:

        if _queue:
            return _queue.popleft()

        return None


def queue() -> list[DownloadJob]:

    with _state_lock:
        return list(_queue)

def cancel_queued(job_id: str) -> DownloadJob | None:

    for job in list(_queue):

        if job.job_id == job_id:

            _queue.remove(job)

            job.status = "cancelled"
            job.finished_at = datetime.now()

            persist()

            return job

    return None


def downloads() -> list[DownloadJob]:

    with _state_lock:
        return list(_downloads)


def active_downloads() -> list[DownloadJob]:

    return [
        job
        for job in _downloads
        if job.status not in ("completed", "failed")
    ]


def completed_downloads() -> list[DownloadJob]:

    return [
        job
        for job in _downloads
        if job.status == "completed"
    ]


def failed_downloads() -> list[DownloadJob]:

    return [
        job
        for job in _downloads
        if job.status == "failed"
    ]


def size():

    return len(_queue)


def clear_queue():

    _queue.clear()


def clear_downloads():

    _downloads.clear()


def find(download_id: str) -> DownloadJob | None:

    for job in _downloads:

        if job.download_id == download_id:
            return job

    return None

def find_by_job_id(
    job_id: str,
) -> DownloadJob | None:

    with _state_lock:

        for job in _downloads:
            if job.job_id == job_id:
                return job

        return None

def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    return (
        value
        .casefold()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("`", "'")
        .strip()
    )

def tracks_match(track_a: str, track_b: str) -> bool:
    a = normalize_text(track_a)
    b = normalize_text(track_b)

    if a == b:
        return True

    # Los números forman parte de la identidad
    # de la canción.
    #
    # Computerwelt   != Computerwelt 2
    # Part 1         != Part 2
    # Song 2         != Song 3
    #
    # Pero:
    # Part 01        == Part 1
    # Song 007       == Song 7

    numbers_a = tuple(
        int(value)
        for value in re.findall(
            r"\d+",
            a,
        )
    )

    numbers_b = tuple(
        int(value)
        for value in re.findall(
            r"\d+",
            b,
        )
    )

    if numbers_a != numbers_b:
        return False

    # Canonicalizar números antes del fuzzy match.
    #
    # "part 01" -> "part 1"
    # "song 007" -> "song 7"

    canonical_a = re.sub(
        r"\d+",
        lambda match: str(
            int(match.group())
        ),
        a,
    )

    canonical_b = re.sub(
        r"\d+",
        lambda match: str(
            int(match.group())
        ),
        b,
    )

    if canonical_a == canonical_b:
        return True

    token_score = fuzz.token_set_ratio(
        canonical_a,
        canonical_b,
    )

    ratio_score = fuzz.ratio(
        canonical_a,
        canonical_b,
    )

    return (
        token_score >= 95
        and ratio_score >= 90
    )

def find_existing(
    artist: str,
    track: str | None,
    album: str | None = None,
    quality_mode: str = "best",
) -> DownloadJob | None:

    if not artist or not track:
        return None

    target_artist = normalize_text(artist)
    target_track = normalize_text(track)
    target_album = normalize_text(album)

    matches = []

    for job in _downloads:

        if job.quality_mode != quality_mode:
            continue

        if normalize_text(job.artist) != target_artist:
            continue

        if normalize_text(job.album) != target_album:
            continue

        if tracks_match(track, job.track):
            matches.append(job)

    if not matches:
        return None

    state_priority = {
    "completed": 0,
    "waiting_remote": 1,
    "downloading": 1,
    "starting": 1,
    "queued": 1,
    "failed": 2,
    "cancelled": 2,
}

    def job_priority(job):
        return state_priority.get(job.status, 2)

    matches.sort(
        key=lambda job: (
            job_priority(job),
            -(job.queued_at.timestamp() if job.queued_at else 0),
        )
    )

    return matches[0]

def update_local_path_references(
    old_path: str,
    new_path: str,
) -> int:
    old_normalized = (
        str(old_path)
        .replace("\\", "/")
        .casefold()
    )

    changed = 0

    with _state_lock:
        for job in _downloads:
            current = (
                str(job.local_path or "")
                .replace("\\", "/")
                .casefold()
            )

            if current != old_normalized:
                continue

            job.local_path = new_path
            changed += 1

        if changed:
            save_downloads(
                _downloads
            )

    if changed:
        print(
            f"[LOCAL PATH REFERENCES UPDATED] "
            f"old={old_path!r} | "
            f"new={new_path!r} | "
            f"jobs={changed}",
            flush=True,
        )

    return changed

def persist():

    with _state_lock:
        save_downloads(_downloads)

def load_history():
    _downloads.clear()
    _queue.clear()

    state_priority = {
        "completed": 0,
        "waiting_remote": 1,
        "downloading": 1,
        "starting": 1,
        "queued": 1,
        "failed": 2,
        "cancelled": 2,
    }

    unique_jobs = []

    for data in load_downloads():
        job = DownloadJob.from_dict(data)

        duplicate = None

        for existing in unique_jobs:

            same_artist = (
                normalize_text(existing.artist)
                == normalize_text(job.artist)
            )

            same_album = (
                normalize_text(existing.album)
                == normalize_text(job.album)
            )

            same_track = tracks_match(
                existing.track,
                job.track,
            )

            if same_artist and same_album and same_track:
                duplicate = existing
                break

        if duplicate is None:
            unique_jobs.append(job)
            continue

        existing_priority = state_priority.get(
            duplicate.status,
            2,
        )

        new_priority = state_priority.get(
            job.status,
            2,
        )

        if new_priority < existing_priority:
            index = unique_jobs.index(duplicate)
            unique_jobs[index] = job

        elif new_priority == existing_priority:
            existing_time = (
                duplicate.queued_at.timestamp()
                if duplicate.queued_at
                else 0
            )

            new_time = (
                job.queued_at.timestamp()
                if job.queued_at
                else 0
            )

            if new_time > existing_time:
                index = unique_jobs.index(duplicate)
                unique_jobs[index] = job

    _downloads.extend(unique_jobs)

    for job in _downloads:

        # Estaba pendiente y nunca llegó al worker.
        if job.status == "queued":
            _queue.append(job)

        # El contenedor cayó mientras el worker
        # estaba iniciando la descarga.
        elif job.status == "starting":

            if job.download_id:
                job.status = "waiting_remote"

            else:
                job.status = "queued"
                _queue.append(job)

        # Estos estados los recupera el monitor.
        elif job.status in ("waiting_remote", "downloading"):

            # Si por algún historial antiguo falta el id,
            # el monitor no podría seguir el trabajo.
            if not job.download_id:
                job.status = "queued"
                _queue.append(job)

    save_downloads(_downloads)

    print(
        f"[HISTORY] cargados={len(_downloads)} trabajos únicos "
        f"| reencolados={len(_queue)}",
        flush=True,
    )