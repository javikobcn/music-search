import threading
import time
import logging
from datetime import datetime
from .services.queue_service import downloads
from .slskd import SlskdClient
from .services.queue_service import (
    downloads,
    persist,
    requeue,
)
from pathlib import Path
from .services.audio_file_service import (
    get_real_audio_length,
)
import re
import requests

logger = logging.getLogger(__name__)

_user_status_cache = {}

MAX_AUTO_RETRIES = 2

OFFLINE_REMOTE_TIMEOUT = 30
STALLED_REMOTE_TIMEOUT = 10 * 60
ORPHAN_REMOTE_TIMEOUT = 60

_missing_remote_since = {}

USER_STATUS_CACHE_SECONDS = 15

DOWNLOADS_ROOT = Path("/downloads")
PLAYLISTS_PENDING_ROOT = DOWNLOADS_ROOT / "playlists_pending"
LOSSLESS_ROOT = DOWNLOADS_ROOT / "lossless"
NORMAL_ROOT = DOWNLOADS_ROOT / "normal"
INCOMPLETE_ROOT = DOWNLOADS_ROOT / "incomplete"
PLAYLIST_FILES_ROOT = Path(
    "/library/PLAYLISTS"
)

INCOMPLETE_CLEANUP_INTERVAL = 60
_last_incomplete_cleanup = 0.0


def find_downloaded_file(
    remote_filename,
):
    normalized_filename = (
        remote_filename.replace(
            "\\",
            "/",
        )
    )

    filename = Path(
        normalized_filename
    ).name

    logger.warning(
        f"[FILE SEARCH] "
        f"remote={remote_filename!r} | "
        f"basename={filename!r} | "
        f"root={str(DOWNLOADS_ROOT)!r}"
    )

    for path in DOWNLOADS_ROOT.rglob("*"):
        if (
            path.is_file()
            and path.name == filename
        ):
            logger.warning(
                f"[FILE FOUND] "
                f"{str(path)!r}"
            )

            return path

    logger.warning(
        f"[FILE NOT FOUND] "
        f"basename={filename!r}"
    )

    return None


def sanitize_folder_name(name: str) -> str:
    # Una o varias barras pasan a un único guion.
    sanitized = re.sub(
        r"\s*[\\/]+\s*",
        "-",
        name,
    )

    # Resto de caracteres no válidos.
    invalid_chars = '<>:"|?*'

    sanitized = "".join(
        "_" if char in invalid_chars else char
        for char in sanitized
    )

    sanitized = sanitized.strip().rstrip(".")

    return sanitized or "YouTube Playlist"


def sanitize_filename_part(text: str) -> str:
    invalid_chars = '<>:"/\\|?*'

    sanitized = "".join(
        "_" if char in invalid_chars else char
        for char in text
    )

    sanitized = sanitized.strip().rstrip(".")

    return sanitized or "Unknown"


def cleanup_empty_directories(
    start_dir: Path,
    preserve_start: bool = False,
) -> None:
    current = (
        start_dir.parent
        if preserve_start
        else start_dir
    )

    while (
        current != DOWNLOADS_ROOT
        and current.exists()
    ):
        try:
            current.rmdir()

        except OSError:
            # La carpeta no está vacía.
            break

        print(
            f"[CLEANUP EMPTY DIR] "
            f"removed={str(current)!r}",
            flush=True,
        )

        current = current.parent

def cleanup_stale_incomplete_files():
    if not INCOMPLETE_ROOT.exists():
        return

    now = time.time()

    for path in INCOMPLETE_ROOT.rglob("*"):
        if not path.is_file():
            continue

        try:
            stat = path.stat()

            if stat.st_size != 0:
                continue

            age_seconds = (
                now - stat.st_mtime
            )

            if age_seconds < 10 * 60:
                continue

            path.unlink()

            logger.warning(
                f"[INCOMPLETE ZERO CLEANUP] "
                f"removed={str(path)!r}"
            )

        except OSError as exc:
            logger.warning(
                f"[INCOMPLETE ZERO CLEANUP ERROR] "
                f"path={str(path)!r} | "
                f"error={exc!r}"
            )

    directories = sorted(
        (
            path
            for path in INCOMPLETE_ROOT.rglob("*")
            if path.is_dir()
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    )

    for directory in directories:
        try:
            directory.rmdir()

            logger.warning(
                f"[INCOMPLETE DIR CLEANUP] "
                f"removed={str(directory)!r}"
            )

        except OSError:
            # No está vacía.
            pass     


def move_single_download(
    local_file: Path,
    artist: str,
    track: str,
) -> Path:
    extension = local_file.suffix.casefold()

    if extension in (".flac", ".wav", ".alac", ".aiff"):
        destination_dir = LOSSLESS_ROOT

    elif extension == ".mp3":
        destination_dir = NORMAL_ROOT

    else:
        # Por ahora, si aparece otro formato,
        # lo dejamos en normal.
        destination_dir = NORMAL_ROOT

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

    clean_filename = (
        f"{artist_name} - {track_name}"
        f"{extension}"
    )

    destination = (
        destination_dir
        / clean_filename
    )

    source_dir = local_file.parent

    if destination.exists():
        print(
            f"[SINGLE MOVE] "
            f"destination already exists | "
            f"destination={str(destination)!r}",
            flush=True,
        )

        # No borramos el archivo existente.
        # Eliminamos la descarga recién llegada
        # para evitar duplicados.
        local_file.unlink()

        cleanup_empty_directories(
            source_dir
        )

        return destination

    local_file.rename(
        destination
    )

    cleanup_empty_directories(
        source_dir
    )

    print(
        f"[SINGLE MOVE] "
        f"source={str(local_file)!r} | "
        f"destination={str(destination)!r}",
        flush=True,
    )

    return destination


def move_to_playlist_pending(
    local_file: Path,
    playlist: str,
    artist: str,
    track: str,
) -> Path:
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

    extension = local_file.suffix.casefold()

    clean_filename = (
        f"{artist_name} - {track_name}"
        f"{extension}"
    )

    destination = (
        destination_dir
        / clean_filename
    )

    if destination.exists():
        destination.unlink()

    source_dir = local_file.parent

    local_file.rename(
        destination
    )

    cleanup_empty_directories(
        source_dir
    )

    return destination


def get_playlist_pending_dir(
    playlist: str,
) -> Path:
    return (
        PLAYLISTS_PENDING_ROOT
        / sanitize_folder_name(playlist)
    )


def classify_playlist_folder(
    playlist: str,
) -> tuple[str, int, int, int]:
    playlist_dir = get_playlist_pending_dir(
        playlist
    )

    if not playlist_dir.exists():
        return "normal", 0, 0, 0

    files = [
        path
        for path in playlist_dir.iterdir()
        if path.is_file()
    ]

    lossless_count = sum(
        1
        for path in files
        if path.suffix.casefold() in (".flac", ".wav", ".alac", ".aiff")
    )

    mp3_count = sum(
        1
        for path in files
        if path.suffix.casefold() == ".mp3"
    )

    total = lossless_count + mp3_count

    if total == 0:
        return "normal", lossless_count, mp3_count, total

    lossless_ratio = lossless_count / total

    destination_type = (
        "lossless"
        if lossless_ratio > 0.5
        else "normal"
    )

    return (
        destination_type,
        lossless_count,
        mp3_count,
        total,
    )

def update_playlist_m3u(
    playlist: str,
    destination_dir: Path,
) -> Path | None:
    try:
        PLAYLIST_FILES_ROOT.mkdir(
            parents=True,
            exist_ok=True,
        )

        playlist_name = sanitize_folder_name(
            playlist
        )

        try:
            relative_destination = (
                destination_dir.relative_to(
                    DOWNLOADS_ROOT
                )
            )

        except ValueError:
            relative_destination = None


        if (
            relative_destination is not None
            and relative_destination.parts
            and relative_destination.parts[0] == "lossless"
        ):
            m3u_filename = (
                f"{playlist_name} - lossless.m3u"
            )

        else:
            m3u_filename = (
                f"{playlist_name}.m3u"
            )


        m3u_path = (
            PLAYLIST_FILES_ROOT
            / m3u_filename
        )

        audio_files = sorted(
            (
                path
                for path in destination_dir.iterdir()
                if (
                    path.is_file()
                    and path.suffix.casefold()
                    in (
                        ".flac",
                        ".mp3",
                        ".wav",
                        ".alac",
                        ".aiff",
                        ".m4a",
                        ".aac",
                        ".ogg",
                        ".opus",
                    )
                )
            ),
            key=lambda path: (
                path.name.casefold()
            ),
        )

        lines = [
            "#EXTM3U",
        ]

        for audio_file in audio_files:
            relative_download_path = (
                audio_file.relative_to(
                    DOWNLOADS_ROOT
                )
            )

            m3u_entry = (
                Path("..")
                / "slskd"
                / relative_download_path
            )

            lines.append(
                m3u_entry.as_posix()
            )

        temporary_path = (
            m3u_path.with_suffix(
                ".m3u.tmp"
            )
        )

        temporary_path.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

        temporary_path.replace(
            m3u_path
        )

        print(
            f"[PLAYLIST M3U UPDATED] "
            f"playlist={playlist!r} | "
            f"tracks={len(audio_files)} | "
            f"path={str(m3u_path)!r}",
            flush=True,
        )

        return m3u_path

    except Exception as exc:
        logger.exception(
            f"[PLAYLIST M3U ERROR] "
            f"playlist={playlist!r} | "
            f"destination={str(destination_dir)!r} | "
            f"error={exc!r}"
        )

        return None

def finalize_playlist_folder(
    playlist: str,
    final_name: str | None = None,
) -> Path | None:
    source_dir = get_playlist_pending_dir(
        playlist
    )

    if not source_dir.exists():
        return None

    final_playlist_name = (
        (final_name or "").strip()
        or playlist
    )

    (
        destination_type,
        lossless_count,
        mp3_count,
        total,
    ) = classify_playlist_folder(
        playlist
    )

    folder_name = sanitize_folder_name(
        final_playlist_name
    )

    lossless_destination = (
        LOSSLESS_ROOT
        / "playlists"
        / folder_name
    )

    normal_destination = (
        NORMAL_ROOT
        / "playlists"
        / folder_name
    )

    # Si la playlist ya existe en alguno de los
    # destinos finales, mantenemos ese destino.
    # Así evitamos que una ejecución futura la
    # divida entre lossless y normal.
    if lossless_destination.exists():
        destination_dir = (
            lossless_destination
        )

        base_dir = (
            LOSSLESS_ROOT
            / "playlists"
        )

        print(
            f"[PLAYLIST DESTINATION EXISTING] "
            f"playlist={playlist!r} | "
            f"type='lossless' | "
            f"destination="
            f"{str(destination_dir)!r}",
            flush=True,
        )

    elif normal_destination.exists():
        destination_dir = (
            normal_destination
        )

        base_dir = (
            NORMAL_ROOT
            / "playlists"
        )

        print(
            f"[PLAYLIST DESTINATION EXISTING] "
            f"playlist={playlist!r} | "
            f"type='normal' | "
            f"destination="
            f"{str(destination_dir)!r}",
            flush=True,
        )

    else:
        # Primera vez que se finaliza esta playlist:
        # usamos la clasificación normal.
        if destination_type == "lossless":
            base_dir = (
                LOSSLESS_ROOT
                / "playlists"
            )
        else:
            base_dir = (
                NORMAL_ROOT
                / "playlists"
            )

        destination_dir = (
            base_dir
            / folder_name
        )

    base_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    if destination_dir.exists():
        print(
            f"[PLAYLIST FINALIZE] "
            f"destination already exists, merging | "
            f"source={str(source_dir)!r} | "
            f"destination={str(destination_dir)!r}",
            flush=True,
        )

        moved_count = 0
        duplicate_count = 0

        for source_file in list(
            source_dir.iterdir()
        ):
            if not source_file.is_file():
                continue

            destination_file = (
                destination_dir
                / source_file.name
            )

            if destination_file.exists():
                # La canción ya existe en la playlist
                # final. Conservamos la copia que ya
                # estaba allí y eliminamos únicamente
                # la entrada pendiente.
                source_file.unlink()

                duplicate_count += 1

                print(
                    f"[PLAYLIST MERGE DUPLICATE] "
                    f"keeping={str(destination_file)!r}",
                    flush=True,
                )

                continue

            source_file.rename(
                destination_file
            )

            moved_count += 1

            print(
                f"[PLAYLIST MERGE MOVE] "
                f"{str(source_file)!r} -> "
                f"{str(destination_file)!r}",
                flush=True,
            )

        # Eliminamos la carpeta pending solamente
        # si ya ha quedado completamente vacía.
        try:
            source_dir.rmdir()
        except OSError:
            pass

        print(
            f"[PLAYLIST FINALIZE MERGED] "
            f"playlist={playlist!r} | "
            f"moved={moved_count} | "
            f"duplicates={duplicate_count} | "
            f"destination={str(destination_dir)!r}",
            flush=True,
        )

        update_playlist_m3u(
            playlist=final_playlist_name,
            destination_dir=destination_dir,
        )       

        return destination_dir

    source_dir.rename(
        destination_dir
    )

    print(
        f"[PLAYLIST FINALIZE] "
        f"playlist={playlist!r} | "
        f"lossless={lossless_count} | "
        f"mp3={mp3_count} | "
        f"total={total} | "
        f"destination={str(destination_dir)!r}",
        flush=True,
    )

    update_playlist_m3u(
        playlist=final_playlist_name,
        destination_dir=destination_dir,
    )  

    return destination_dir

def get_cached_user_presence(
    client,
    username: str,
):
    now = time.monotonic()

    cached = _user_status_cache.get(
        username
    )

    if cached:
        presence, checked_at = cached

        if (
            now - checked_at
            < USER_STATUS_CACHE_SECONDS
        ):
            return presence

    presence = client.get_user_presence(
        username
    )

    _user_status_cache[
        username
    ] = (
        presence,
        now,
    )

    return presence

def cancel_remote_transfer_safely(
    client,
    username: str,
    download_id: str | None,
) -> bool:

    if not download_id:
        return True

    for attempt in range(2):

        try:
            client.cancel_download(
                username=username,
                download_id=download_id,
            )

        except Exception as exc:
            logger.warning(
                f"[REMOTE CANCEL ERROR] "
                f"user={username!r} | "
                f"download_id={download_id!r} | "
                f"attempt={attempt + 1}/2 | "
                f"error={exc!r}"
            )

        # Damos tiempo a slskd para actualizar
        # el estado de la transferencia.
        time.sleep(1)

        try:
            remote = client.get_download(
                download_id
            )

        except Exception as exc:
            logger.warning(
                f"[REMOTE CANCEL VERIFY ERROR] "
                f"download_id={download_id!r} | "
                f"attempt={attempt + 1}/2 | "
                f"error={exc!r}"
            )

            continue

        # Ya no existe en slskd.
        if remote is None:
            logger.warning(
                f"[REMOTE CANCEL CONFIRMED] "
                f"user={username!r} | "
                f"download_id={download_id!r}"
            )

            return True

        state = str(
            remote.get("state", "")
        )

        # También consideramos terminados estados
        # que ya no pueden iniciar una descarga.
        if any(
            value in state
            for value in (
                "Cancelled",
                "Canceled",
                "Aborted",
                "Rejected",
                "Errored",
                "Failed",
            )
        ):
            logger.warning(
                f"[REMOTE CANCEL CONFIRMED] "
                f"user={username!r} | "
                f"download_id={download_id!r} | "
                f"state={state!r}"
            )

            return True

        logger.warning(
            f"[REMOTE CANCEL STILL ACTIVE] "
            f"user={username!r} | "
            f"download_id={download_id!r} | "
            f"state={state!r} | "
            f"attempt={attempt + 1}/2"
        )

    return False

def abandon_remote_source(
    client,
    job,
    reason,
):
    old_username = job.username
    old_filename = job.filename
    old_download_id = job.download_id

    logger.warning(
        f"[REMOTE ABANDON] "
        f"job={job.job_id} | "
        f"artist={job.artist!r} | "
        f"track={job.track!r} | "
        f"user={old_username!r} | "
        f"reason={reason!r}"
    )

    cancelled = (
        cancel_remote_transfer_safely(
            client,
            old_username,
            old_download_id,
        )
    )

    # Un fallo remoto indica que esta fuente no es
    # fiable para este job. Excluimos al usuario
    # completo para que el retry tenga que probar
    # con otra persona.
    failed_source = old_username

    if (
        failed_source
        and failed_source
        not in job.failed_sources
    ):
        job.failed_sources.append(
            failed_source
        )

    # Si la transferencia antigua continúa activa,
    # NO iniciamos otra copia.
    if not cancelled:
        job.status = "failed"
        job.error = (
            "remote_cancel_failed"
        )
        job.finished_at = datetime.now()

        persist()

        logger.error(
            f"[REMOTE CANCEL FAILED] "
            f"job={job.job_id} | "
            f"user={old_username!r} | "
            f"download_id={old_download_id!r}"
        )

        return

    # A partir de aquí sabemos que la transferencia
    # antigua ya no está activa.
    job.download_id = None
    job.progress = 0
    job.downloaded = 0
    job.remaining = 0
    job.speed = 0
    job.error = reason

    if (
        job.retry_count
        < MAX_AUTO_RETRIES
    ):
        job.retry_count += 1

        job.status = "queued"
        job.started_at = None
        job.finished_at = None

        persist()

        logger.warning(
            f"[REMOTE REQUEUE] "
            f"job={job.job_id} | "
            f"retry={job.retry_count}/"
            f"{MAX_AUTO_RETRIES} | "
            f"failed_source="
            f"{failed_source!r}"
        )

        requeue(job)

    else:
        job.status = "failed"
        job.finished_at = datetime.now()

        persist()

        logger.warning(
            f"[REMOTE RETRIES EXHAUSTED] "
            f"job={job.job_id} | "
            f"artist={job.artist!r} | "
            f"track={job.track!r}"
        )

def cleanup_empty_incomplete_dirs():
    if not INCOMPLETE_ROOT.exists():
        return

    directories = sorted(
        (
            path
            for path in INCOMPLETE_ROOT.rglob("*")
            if path.is_dir()
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    )

    removed = 0

    for directory in directories:
        try:
            directory.rmdir()

            logger.warning(
                f"[INCOMPLETE CLEANUP] "
                f"removed={str(directory)!r}"
            )

            removed += 1

        except OSError:
            # No está vacía.
            pass

    if removed:
        logger.warning(
            f"[INCOMPLETE CLEANUP] "
            f"removed_dirs={removed}"
        )        


def monitor():

    client = None

    while True:
        try:
            client = SlskdClient()
            client.login()

            logger.warning(
                "[MONITOR] Monitor conectado a slskd"
            )

            break

        except Exception as exc:
            logger.warning(
                f"[MONITOR] No se pudo conectar a slskd | "
                f"error={exc!r} | "
                f"reintento=5s"
            )

            time.sleep(5)

    while True:

        global _last_incomplete_cleanup

        now = time.monotonic()

        if (
            now - _last_incomplete_cleanup
            >= INCOMPLETE_CLEANUP_INTERVAL
        ):
            try:
                cleanup_stale_incomplete_files()

            except Exception:
                logger.exception(
                    "[INCOMPLETE CLEANUP ERROR] "
                    "La limpieza ha fallado, "
                    "pero el monitor continúa"
                )

            finally:
                _last_incomplete_cleanup = now

        jobs = downloads()

        try:
            remote_downloads = (
                client.get_downloads()
            )

        except requests.HTTPError as exc:
            response = getattr(
                exc,
                "response",
                None,
            )

            if (
                response is not None
                and response.status_code == 401
            ):
                logger.warning(
                    "[MONITOR AUTH] "
                    "Token de slskd no válido. "
                    "Intentando renovar sesión."
                )

                try:
                    client.login()

                    remote_downloads = (
                        client.get_downloads()
                    )

                    logger.warning(
                        "[MONITOR AUTH] "
                        "Sesión de slskd renovada"
                    )

                except Exception as login_exc:
                    logger.warning(
                        f"[MONITOR AUTH ERROR] "
                        f"error={login_exc!r} | "
                        f"reintento siguiente ciclo"
                    )

                    time.sleep(2)
                    continue

            else:
                logger.warning(
                    f"[MONITOR FETCH ERROR] "
                    f"error={exc!r} | "
                    f"reintento siguiente ciclo"
                )

                time.sleep(2)
                continue

        except Exception as exc:
            logger.warning(
                f"[MONITOR FETCH ERROR] "
                f"error={exc!r} | "
                f"reintento siguiente ciclo"
            )

            time.sleep(2)
            continue


        remote_by_id = {}

        for remote_user in remote_downloads:

            for directory in remote_user.get(
                "directories",
                [],
            ):

                for remote_file in directory.get(
                    "files",
                    [],
                ):

                    remote_id = (
                        remote_file.get("id")
                    )

                    if remote_id:
                        remote_by_id[
                            remote_id
                        ] = remote_file

        for job in jobs:

            if (
                not job.download_id
                or job.status in ("completed", "failed", "cancelled")
            ):
                continue

            remote = remote_by_id.get(
                job.download_id
            )

            if remote is None:

                now = time.monotonic()

                missing_since = (
                    _missing_remote_since.get(
                        job.job_id
                    )
                )

                if missing_since is None:
                    _missing_remote_since[
                        job.job_id
                    ] = now

                    logger.warning(
                        f"[REMOTE MISSING] "
                        f"job={job.job_id} | "
                        f"artist={job.artist!r} | "
                        f"track={job.track!r} | "
                        f"status={job.status!r} | "
                        f"waiting for grace period"
                    )

                    continue

                missing_seconds = (
                    now - missing_since
                )

                if (
                    missing_seconds
                    < ORPHAN_REMOTE_TIMEOUT
                ):
                    continue

                logger.warning(
                    f"[REMOTE ORPHAN] "
                    f"job={job.job_id} | "
                    f"artist={job.artist!r} | "
                    f"track={job.track!r} | "
                    f"status={job.status!r} | "
                    f"missing={missing_seconds:.0f}s"
                )

                _missing_remote_since.pop(
                    job.job_id,
                    None,
                )

                abandon_remote_source(
                    client,
                    job,
                    "remote_transfer_missing",
                )

                continue


            # La transferencia vuelve a existir:
            # eliminamos cualquier contador de ausencia.
            _missing_remote_since.pop(
                job.job_id,
                None,
            )

            previous_status = job.status

            job.progress = remote.get("percentComplete", 0)
            job.downloaded = remote.get("bytesTransferred", 0)
            job.remaining = remote.get("bytesRemaining", 0)
            job.speed = remote.get("averageSpeed", 0)

            state = remote.get("state", "")

            # ---------------------------------------------------------
            # Watchdog para playlists bloqueadas en cola remota.
            #
            # Offline:
            #   30 segundos sin transferir nada.
            #
            # Online/desconocido:
            #   10 minutos sin transferir nada.
            # ---------------------------------------------------------
            if (
                job.playlist
                and job.status
                == "waiting_remote"
                and "Queued" in state
            ):
                bytes_transferred = int(
                    remote.get(
                        "bytesTransferred",
                        0,
                    )
                    or 0
                )

                average_speed = float(
                    remote.get(
                        "averageSpeed",
                        0,
                    )
                    or 0
                )

                if (
                    bytes_transferred == 0
                    and average_speed == 0
                    and job.started_at
                ):
                    waiting_seconds = (
                        datetime.now()
                        - job.started_at
                    ).total_seconds()

                    presence = (
                        get_cached_user_presence(
                            client,
                            job.username,
                        )
                    )

                    is_offline = (
                        presence == "offline"
                    )

                    if (
                        is_offline
                        and waiting_seconds
                        >= OFFLINE_REMOTE_TIMEOUT
                    ):
                        logger.warning(
                            f"[REMOTE OFFLINE] "
                            f"track={job.track!r} | "
                            f"user={job.username!r} | "
                            f"wait={waiting_seconds:.0f}s"
                        )

                        abandon_remote_source(
                            client,
                            job,
                            reason=(
                                "remote_user_offline"
                            ),
                        )

                    elif (
                        not is_offline
                        and waiting_seconds
                        >= STALLED_REMOTE_TIMEOUT
                    ):
                        logger.warning(
                            f"[REMOTE STALLED] "
                            f"track={job.track!r} | "
                            f"user={job.username!r} | "
                            f"presence={presence!r} | "
                            f"wait={waiting_seconds:.0f}s"
                        )

                        abandon_remote_source(
                            client,
                            job,
                            reason=(
                                "remote_wait_timeout"
                            ),
                        )

            state_text = str(
                state or ""
            )

            state_lower = (
                state_text.casefold()
            )

            # ---------------------------------------------------------
            # ESTADOS REMOTOS TERMINADOS CON ERROR
            # ---------------------------------------------------------
            #
            # slskd puede devolver estados compuestos:
            #
            #   Completed, Errored
            #
            # Ese estado NO es una descarga completada.
            # Debemos abandonar esa fuente y buscar otra.
            # ---------------------------------------------------------

            remote_failed = any(
                marker in state_lower
                for marker in (
                    "errored",
                    "failed",
                    "aborted",
                    "rejected",
                    "cancelled",
                    "canceled",
                )
            )

            if remote_failed:
                remote_exception = (
                    remote.get("exception")
                    or state_text
                )

                logger.warning(
                    f"[REMOTE FAILED] "
                    f"track={job.track!r} | "
                    f"user={job.username!r} | "
                    f"state={state_text!r} | "
                    f"error={remote_exception!r}"
                )

                abandon_remote_source(
                    client,
                    job,
                    reason=(
                        "remote_transfer_failed"
                    ),
                )

            elif "inprogress" in state_lower:
                job.status = "downloading"

            elif "succeeded" in state_lower:
                remote_filename = remote.get(
                    "filename",
                    "",
                )

                local_file = find_downloaded_file(
                    remote_filename
                )

                # slskd puede marcar la transferencia como Succeeded
                # unos instantes antes de que el archivo aparezca
                # físicamente en /downloads.
                if local_file is None:
                    print(
                        f"[DOWNLOAD FILE WAIT] "
                        f"job_id={job.job_id} | "
                        f"remote_filename={remote_filename!r}"
                    )
                    continue

                final_file = local_file

                try:
                    if job.playlist:
                        final_file = move_to_playlist_pending(
                            local_file,
                            job.playlist,
                            job.artist,
                            job.track,
                        )

                        print(
                            f"[PLAYLIST MOVE] "
                            f"{local_file} -> {final_file}"
                        )

                    else:
                        final_file = move_single_download(
                            local_file,
                            job.artist,
                            job.track,
                        )

                        print(
                            f"[SINGLE MOVE] "
                            f"{local_file} -> {final_file}"
                        )

                except Exception as exc:
                    print(
                        f"[DOWNLOAD MOVE ERROR] "
                        f"job_id={job.job_id} | "
                        f"error={exc!r}"
                    )
                    continue

                print(
                    f"[DOWNLOAD COMPLETED] "
                    f"job_id={job.job_id} | "
                    f"playlist={job.playlist!r} | "
                    f"remote_filename={remote_filename!r} | "
                    f"local_file={str(final_file)!r}"
                )

                job.local_path = str(
                    final_file
                )

                try:
                    job.local_size = (
                        final_file.stat().st_size
                    )

                    logger.warning(
                        f"[AUDIO SIZE] "
                        f"track={job.track!r} | "
                        f"real={job.local_size} bytes"
                    )

                except OSError as exc:
                    logger.warning(
                        f"[AUDIO SIZE ERROR] "
                        f"file={str(final_file)!r} | "
                        f"error={exc!r}"
                    )

                real_length = (
                    get_real_audio_length(
                        final_file
                    )
                )

                if real_length is not None:
                    print(
                        f"[AUDIO LENGTH] "
                        f"track={job.track!r} | "
                        f"old={job.length!r} | "
                        f"real={real_length}s",
                        flush=True,
                    )

                    job.length = real_length

                job.status = "completed"
                job.finished_at = (
                    datetime.now()
                )

                persist()            

            if job.status != previous_status:
                logger.warning(
                    f"[MONITOR] {job.track}: "
                    f"{previous_status} -> {job.status} "
                    f"({job.progress:.1f}%)"
                )

        time.sleep(2)


def start_monitor():

    thread = threading.Thread(
        target=monitor,
        daemon=True,
    )

    logger.warning("[MONITOR] Iniciado")

    thread.start()