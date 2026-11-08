from .search_service import search_best
from .queue_service import enqueue, find_existing, persist
from ..entities import DownloadJob
from ..utils import format_quality, format_size, format_speed
from pathlib import Path
import os
import shutil
import time
from app.services.audio_file_service import (
    get_real_audio_length,
)


DOWNLOADS_ROOT = Path("/downloads")
PLAYLISTS_PENDING_ROOT = (
    DOWNLOADS_ROOT / "playlists_pending"
)

LIBRARY_ROOT = Path("/library")

LIBRARY_ROOTS = (
    DOWNLOADS_ROOT,
    LIBRARY_ROOT,
)



def _find_in_playlist_directories(
    base_dir: Path,
    filename: str,
):
    if not base_dir.exists():
        return None

    try:
        for playlist_dir in base_dir.iterdir():
            if not playlist_dir.is_dir():
                continue

            candidate = (
                playlist_dir / filename
            )

            if candidate.is_file():
                return candidate

    except OSError:
        return None

    return None


def _find_in_library_bounded(
    filename: str,
    timeout_seconds: float = 5.0,
):
    deadline = (
        time.monotonic()
        + timeout_seconds
    )

    if not LIBRARY_ROOT.exists():
        return None

    for (
        dirpath,
        dirnames,
        filenames,
    ) in os.walk(LIBRARY_ROOT):

        if time.monotonic() >= deadline:
            print(
                f"[LOCAL LOOKUP TIMEOUT] "
                f"filename={filename!r} | "
                f"timeout={timeout_seconds}s",
                flush=True,
            )

            return None

        # /library/slskd corresponde al árbol
        # que ya tenemos montado como /downloads.
        # No lo recorremos dos veces.
        if Path(dirpath) == LIBRARY_ROOT:
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if dirname != "slskd"
            ]

        if filename not in filenames:
            continue

        candidate = (
            Path(dirpath)
            / filename
        )

        if candidate.is_file():
            return candidate

    return None


def resolve_completed_local_path(job):
    if not job.local_path:
        return None

    current_path = Path(
        job.local_path
    )

    # --------------------------------------------------
    # 1. LA RUTA GUARDADA SIGUE EXISTIENDO
    # --------------------------------------------------

    if current_path.is_file():
        return current_path

    filename = current_path.name

    print(
        f"[LOCAL LOOKUP] "
        f"stored path missing | "
        f"filename={filename!r}",
        flush=True,
    )

    # --------------------------------------------------
    # 2. UBICACIONES CONOCIDAS DE SINGLES
    # --------------------------------------------------

    direct_candidates = (
        DOWNLOADS_ROOT
        / "lossless"
        / filename,

        DOWNLOADS_ROOT
        / "normal"
        / filename,
    )

    for candidate in direct_candidates:
        if candidate.is_file():
            print(
                f"[LOCAL LOOKUP FOUND] "
                f"{str(candidate)!r}",
                flush=True,
            )

            return candidate

    # --------------------------------------------------
    # 3. PLAYLISTS GESTIONADAS POR LA APP
    #
    # Los archivos están directamente dentro de
    # cada carpeta de playlist, así que esto es
    # muchísimo más rápido que un rglob global.
    # --------------------------------------------------

    playlist_roots = (
        DOWNLOADS_ROOT
        / "lossless"
        / "playlists",

        DOWNLOADS_ROOT
        / "normal"
        / "playlists",

        DOWNLOADS_ROOT
        / "playlists_pending",
    )

    for playlist_root in playlist_roots:
        candidate = (
            _find_in_playlist_directories(
                playlist_root,
                filename,
            )
        )

        if candidate is not None:
            print(
                f"[LOCAL LOOKUP FOUND] "
                f"{str(candidate)!r}",
                flush=True,
            )

            return candidate

    # --------------------------------------------------
    # 4. BIBLIOTECA GENERAL
    #
    # Permitimos buscar un archivo movido
    # manualmente, pero nunca bloqueamos una
    # petición durante minutos.
    # --------------------------------------------------

    candidate = _find_in_library_bounded(
        filename,
        timeout_seconds=5.0,
    )

    if candidate is not None:
        print(
            f"[LOCAL LOOKUP FOUND] "
            f"{str(candidate)!r}",
            flush=True,
        )

        return candidate

    print(
        f"[LOCAL LOOKUP MISS] "
        f"filename={filename!r}",
        flush=True,
    )

    return None

def add_existing_file_to_playlist(
    source_file: Path,
    playlist: str,
    artist: str,
    track: str,
) -> Path:
    # Reutilizamos exactamente las mismas reglas
    # de nombres que usa monitor.py.
    from ..monitor import (
        sanitize_folder_name,
        sanitize_filename_part,
    )

    folder_name = sanitize_folder_name(
        playlist
    )

    destination_dir = (
        PLAYLISTS_PENDING_ROOT
        / folder_name
    )

    destination_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    artist_name = sanitize_filename_part(
        artist
    )

    track_name = sanitize_filename_part(
        track
    )

    extension = (
        source_file.suffix.casefold()
    )

    destination = (
        destination_dir
        / (
            f"{artist_name} - "
            f"{track_name}"
            f"{extension}"
        )
    )

    # Ya está físicamente incorporada
    # a la playlist pendiente.
    if destination.is_file():
        return destination

    try:
        os.link(
            source_file,
            destination,
        )

        print(
            f"[PLAYLIST EXISTING LINK] "
            f"{source_file} -> "
            f"{destination}",
            flush=True,
        )

    except FileExistsError:
        return destination

    except OSError as exc:
        print(
            f"[PLAYLIST EXISTING LINK FALLBACK] "
            f"source={str(source_file)!r} | "
            f"destination={str(destination)!r} | "
            f"error={exc!r}",
            flush=True,
        )

        shutil.copy2(
            source_file,
            destination,
        )

        print(
            f"[PLAYLIST EXISTING COPY] "
            f"{source_file} -> "
            f"{destination}",
            flush=True,
        )

    return destination


def get_track(
    artist: str,
    track: str,
    album: str | None = None,
    playlist: str | None = None,
):

    print(
        f"[TRACK] artist={artist!r} | "
        f"track={track!r} | "
        f"playlist={playlist!r}",
        flush=True,
    )
    
    if not track:
        return {
            "status": "invalid_request",
            "error": "track is required",
        }

    # Buscar si ya existe un trabajo para esta canción.
    existing = find_existing(
        artist=artist,
        track=track,
        album=album,
    )

    # ---------------------------------------------------------
    # Trabajo completado:
    # solo cuenta como descargado si el archivo existe
    # físicamente.
    # ---------------------------------------------------------
    if existing and existing.status == "completed":
        local_file = resolve_completed_local_path(
            existing
        )

        if local_file is not None:
            new_local_path = str(
                local_file
            )

            changed = False

            if (
                existing.local_path
                != new_local_path
            ):
                print(
                    f"[LOCAL PATH UPDATED] "
                    f"{existing.local_path!r} "
                    f"-> {new_local_path!r}",
                    flush=True,
                )

                existing.local_path = (
                    new_local_path
                )

                changed = True


            real_length = (
                get_real_audio_length(
                    local_file
                )
            )

            try:
                real_size = (
                    local_file.stat().st_size
                )

            except OSError:
                real_size = None


            if (
                real_size is not None
                and existing.local_size
                != real_size
            ):
                existing.local_size = (
                    real_size
                )

                changed = True

            if (
                real_length is not None
                and existing.length
                != real_length
            ):
                print(
                    f"[AUDIO LENGTH UPDATED] "
                    f"track={existing.track!r} | "
                    f"{existing.length!r} "
                    f"-> {real_length}s",
                    flush=True,
                )

                existing.length = (
                    real_length
                )

                changed = True


            if changed:
                persist()

            response_local_path = (
                existing.local_path
            )

            response_local_size = (
                existing.local_size
            )

            # Si esta petición pertenece a una
            # playlist, la canción existente debe
            # formar parte físicamente de ella.
            if playlist:
                existing_file = (
                    resolve_completed_local_path(
                        existing
                    )
                )

                if existing_file is not None:
                    try:
                        playlist_file = (
                            add_existing_file_to_playlist(
                                source_file=existing_file,
                                playlist=playlist,
                                artist=artist,
                                track=track,
                            )
                        )

                        response_local_path = str(
                            playlist_file
                        )

                        response_local_size = (
                            playlist_file.stat().st_size
                        )

                    except Exception as exc:
                        print(
                            f"[PLAYLIST EXISTING ERROR] "
                            f"artist={artist!r} | "
                            f"track={track!r} | "
                            f"playlist={playlist!r} | "
                            f"error={exc!r}",
                            flush=True,
                        )

            return {
                "status": existing.status,
                "already_exists": True,
                "job_id": existing.job_id,
                "track": {
                    "artist": existing.artist,
                    "title": existing.track,
                    "album": existing.album,
                },
                "download": {
                "progress": round(
                    existing.progress,
                    1,
                ),
                "download_id": (
                    existing.download_id
                ),
                "local_path": (
                    response_local_path
                ),
                "local_size": (
                    response_local_size
                ),
                "extension": (
                    existing.extension
                ),
                "length": (
                    existing.length
                ),
            },
            }

        # El historial dice completed, pero el archivo
        # físico ya no está. Ignoramos este job y
        # permitimos una descarga nueva.
        print(
            f"[LOCAL FILE MISSING] "
            f"artist={existing.artist!r} | "
            f"track={existing.track!r} | "
            f"local_path={existing.local_path!r}",
            flush=True,
        )

        existing = None


    # ---------------------------------------------------------
    # Ya existe un trabajo activo:
    # no crear otra descarga.
    # ---------------------------------------------------------
    if existing and existing.status not in (
        "failed",
        "cancelled",
    ):

        return {
            "status": existing.status,
            "already_exists": True,
            "job_id": existing.job_id,
            "track": {
                "artist": existing.artist,
                "title": existing.track,
                "album": existing.album,
            },
            "download": {
                "progress": round(
                    existing.progress,
                    1,
                ),
                "download_id": (
                    existing.download_id
                ),
            },
        }

    # ---------------------------------------------------------
    # Existe un trabajo fallido.
    # En este punto permitimos buscar una nueva alternativa.
    # ---------------------------------------------------------
    if existing and existing.status in ("failed", "cancelled"):
        print(
            f"[GET_TRACK] existing failed -> buscando retry "
            f"artist='{artist}' | track='{track}' | album='{album}'",
            flush=True,
        )

    # ---------------------------------------------------------
    # No existe ningún trabajo válido o el anterior falló.
    # Buscar una nueva fuente en Soulseek.
    # ---------------------------------------------------------

    best = search_best(
        artist=artist,
        track=track,
        album=album,
    )

    if best is None:
        return {
            "status": "not_found",
        }

    print(
        f"[GET_TRACK] BEST username={best.username!r} "
        f"| filename={best.filename!r} "
        f"| size={best.size} "
        f"| score={best.score}",
        flush=True,
    )

    job = DownloadJob(
        username=best.username,
        filename=best.filename,
        size=best.size,
        score=best.score,
        artist=artist,
        track=track,
        album=album,
        playlist=playlist,
        extension=best.extension,
        length=best.length,
    )

    position = enqueue(job)

    print(
        f"[GET_TRACK] descarga creada | "
        f"download_id={job.download_id} | "
        f"position={position}",
        flush=True,
    )

    return {
        "status": "queued",
        "job_id": job.job_id,
        "position": position,
        "track": {
            "artist": artist,
            "title": track,
            "album": album,
            "quality": format_quality(best),
            "size": format_size(best.size),
        },
        "source": {
            "username": best.username,
            "queue": best.queueLength,
            "speed": format_speed(best.uploadSpeed),
        },
    }