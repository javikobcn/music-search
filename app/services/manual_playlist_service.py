from pathlib import Path
import os
import shutil

from .audio_file_service import (
    get_real_audio_length,
)


PLAYLISTS_ROOT = Path(
    "/library/PLAYLISTS"
)

DOWNLOADS_ROOT = Path(
    "/downloads"
)

LIBRARY_ROOT = Path(
    "/library"
)

AUDIO_EXTENSIONS = {
    ".flac",
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
}


def sanitize_playlist_filename(
    name: str,
) -> str:
    invalid_chars = '<>:"/\\|?*'

    sanitized = "".join(
        "_"
        if char in invalid_chars
        else char
        for char in name
    )

    sanitized = (
        sanitized
        .strip()
        .rstrip(".")
    )

    return sanitized or "Nueva Playlist"


def list_manual_playlists():
    if not PLAYLISTS_ROOT.exists():
        return []

    playlists = []

    for path in PLAYLISTS_ROOT.iterdir():
        if (
            not path.is_file()
            or path.suffix.casefold()
            not in (
                ".m3u",
                ".m3u8",
            )
        ):
            continue

        playlists.append(
            {
                "name": path.stem,
                "filename": path.name,
            }
        )

    playlists.sort(
        key=lambda item: (
            item["name"].casefold()
        )
    )

    return playlists


def find_playlist(
    playlist_name: str,
) -> Path | None:
    if not PLAYLISTS_ROOT.exists():
        return None

    requested = (
        playlist_name
        .casefold()
        .strip()
    )

    for path in PLAYLISTS_ROOT.iterdir():
        if (
            not path.is_file()
            or path.suffix.casefold()
            not in (
                ".m3u",
                ".m3u8",
            )
        ):
            continue

        if (
            path.stem
            .casefold()
            .strip()
            == requested
        ):
            return path

    return None


def local_path_to_m3u_entry(
    local_path: str,
) -> str | None:
    path = Path(local_path)

    if not path.is_file():
        return None

    # --------------------------------------------------
    # ARCHIVOS GESTIONADOS POR SLSKD
    #
    # /downloads/lossless/...
    #
    # corresponde en el host a:
    #
    # /musica/slskd/lossless/...
    #
    # El M3U está en:
    #
    # /musica/PLAYLISTS/
    #
    # por tanto:
    #
    # ../slskd/lossless/...
    # --------------------------------------------------

    try:
        relative = path.relative_to(
            DOWNLOADS_ROOT
        )

        return (
            Path("..")
            / "slskd"
            / relative
        ).as_posix()

    except ValueError:
        pass

    # --------------------------------------------------
    # ARCHIVOS QUE YA ESTÁN EN LA BIBLIOTECA GENERAL
    #
    # /library/Artista/tema.flac
    #
    # desde /library/PLAYLISTS:
    #
    # ../Artista/tema.flac
    # --------------------------------------------------

    try:
        relative = path.relative_to(
            LIBRARY_ROOT
        )

        return (
            Path("..")
            / relative
        ).as_posix()

    except ValueError:
        pass

    return None

def m3u_entry_to_local_path(
    entry: str,
) -> Path | None:
    normalized = (
        entry
        .strip()
        .replace("\\", "/")
    )

    if not normalized:
        return None

    if normalized.startswith(
        "../slskd/"
    ):
        relative = normalized[
            len("../slskd/"):
        ]

        return (
            DOWNLOADS_ROOT
            / relative
        )

    if normalized.startswith(
        "../"
    ):
        relative = normalized[
            len("../"):
        ]

        return (
            LIBRARY_ROOT
            / relative
        )

    if normalized.startswith(
        "/music/"
    ):
        relative = normalized[
            len("/music/"):
        ]

        return (
            LIBRARY_ROOT
            / relative
        )        

    if normalized.startswith(
        "/downloads/"
    ):
        return Path(
            normalized
        )

    if normalized.startswith(
        "/library/"
    ):
        return Path(
            normalized
        )

    return None


def get_managed_playlist_directory(
    lines: list[str],
) -> Path | None:
    candidates = set()

    managed_roots = (
        DOWNLOADS_ROOT
        / "lossless"
        / "playlists",

        DOWNLOADS_ROOT
        / "normal"
        / "playlists",
    )

    for line in lines:
        stripped = line.strip()

        if (
            not stripped
            or stripped.startswith("#")
        ):
            continue

        local_path = (
            m3u_entry_to_local_path(
                stripped
            )
        )

        if local_path is None:
            continue

        for root in managed_roots:
            try:
                relative = (
                    local_path.relative_to(
                        root
                    )
                )

            except ValueError:
                continue

            # Esperamos algo como:
            #
            # /downloads/lossless/playlists/
            # Tame Impala/cancion.flac
            if len(relative.parts) < 2:
                continue

            candidates.add(
                root
                / relative.parts[0]
            )

    # Solo movemos automáticamente si todo apunta
    # inequívocamente a una única carpeta gestionada.
    if len(candidates) != 1:
        return None

    return next(
        iter(candidates)
    )


def is_direct_slskd_single(
    path: Path,
) -> bool:
    return path.parent in (
        DOWNLOADS_ROOT
        / "lossless",

        DOWNLOADS_ROOT
        / "normal",
    )


def unique_destination(
    destination_dir: Path,
    source: Path,
) -> Path:
    destination = (
        destination_dir
        / source.name
    )

    if not destination.exists():
        return destination

    index = 2

    while True:
        candidate = (
            destination_dir
            / (
                f"{source.stem} "
                f"({index})"
                f"{source.suffix}"
            )
        )

        if not candidate.exists():
            return candidate

        index += 1


def relocate_single_to_playlist(
    source: Path,
    destination_dir: Path,
) -> Path:
    # Solo trasladamos singles gestionados por
    # slskd que estén directamente en:
    #
    # /downloads/lossless/
    # /downloads/normal/
    #
    # Nunca movemos archivos de la biblioteca
    # general ni de otra playlist.
    if not is_direct_slskd_single(
        source
    ):
        return source

    destination_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = unique_destination(
        destination_dir,
        source,
    )

    old_path = str(
        source
    )

    try:
        os.replace(
            source,
            destination,
        )

    except OSError:
        # Por seguridad, si rename no puede hacerse,
        # copiamos primero y solo eliminamos el origen
        # después de una copia correcta.
        shutil.copy2(
            source,
            destination,
        )

        source.unlink()

    new_path = str(
        destination
    )

    try:
        from .queue_service import (
            update_local_path_references,
        )

        update_local_path_references(
            old_path,
            new_path,
        )

    except Exception as exc:
        print(
            f"[PLAYLIST PATH UPDATE ERROR] "
            f"old={old_path!r} | "
            f"new={new_path!r} | "
            f"error={exc!r}",
            flush=True,
        )

    print(
        f"[PLAYLIST SINGLE MOVE] "
        f"{old_path!r} -> "
        f"{new_path!r}",
        flush=True,
    )

    return destination


def get_playlist_stats(
    playlist_path: Path,
) -> dict:
    try:
        content = playlist_path.read_text(
            encoding="utf-8-sig",
        )

    except UnicodeDecodeError:
        content = playlist_path.read_text(
            encoding="latin-1",
        )

    track_count = 0
    duration_seconds = 0
    duration_known = 0
    size_bytes = 0
    size_known = 0
    missing = 0

    for line in content.splitlines():
        entry = line.strip()

        if (
            not entry
            or entry.startswith("#")
        ):
            continue

        track_count += 1

        local_path = (
            m3u_entry_to_local_path(
                entry
            )
        )

        if (
            local_path is None
            or not local_path.is_file()
        ):
            missing += 1
            continue

        if (
            local_path.suffix.casefold()
            not in AUDIO_EXTENSIONS
        ):
            continue

        try:
            size_bytes += int(
                local_path.stat().st_size
            )
            size_known += 1

        except OSError as exc:
            print(
                f"[PLAYLIST SIZE ERROR] "
                f"path={str(local_path)!r} | "
                f"error={exc!r}",
                flush=True,
            )        

        length = get_real_audio_length(
            local_path
        )

        if length is None:
            continue

        duration_seconds += int(
            length
        )

        duration_known += 1

    return {
        "track_count": track_count,
        "duration_seconds": duration_seconds,
        "duration_known": duration_known,
        "size_bytes": size_bytes,
        "size_known": size_known,
        "missing": missing,
    }

def get_audio_directory_stats(
    directory: str | Path,
) -> dict:
    directory = Path(
        directory
    )

    if not directory.is_dir():
        return {
            "track_count": 0,
            "duration_seconds": 0,
            "duration_known": 0,
            "size_bytes": 0,
            "size_known": 0,
        }

    track_count = 0
    duration_seconds = 0
    duration_known = 0

    for path in directory.iterdir():
        if (
            not path.is_file()
            or path.suffix.casefold()
            not in AUDIO_EXTENSIONS
        ):
            continue

        track_count += 1

        try:
            size_bytes += int(
                path.stat().st_size
            )
            size_known += 1

        except OSError as exc:
            print(
                f"[DIRECTORY SIZE ERROR] "
                f"path={str(path)!r} | "
                f"error={exc!r}",
                flush=True,
            )        

        length = get_real_audio_length(
            path
        )

        if length is None:
            continue

        duration_seconds += int(
            length
        )

        duration_known += 1

    return {
        "track_count": track_count,
        "duration_seconds": duration_seconds,
        "duration_known": duration_known,
        "size_bytes": size_bytes,
        "size_known": size_known,
    }

def find_playlists_containing_path(
    local_path: str,
) -> dict:
    source_path = Path(
        local_path
    )

    normalized_source = (
        str(source_path)
        .replace("\\", "/")
        .casefold()
    )

    matches = []

    source_name = (
        source_path.name.casefold()
    )

    try:
        source_stat = (
            source_path.stat()
        )

    except OSError:
        source_stat = None    

    if not PLAYLISTS_ROOT.exists():
        return {
            "status": "ok",
            "local_path": local_path,
            "count": 0,
            "playlists": [],
        }

    playlist_files = sorted(
        (
            path
            for path in PLAYLISTS_ROOT.iterdir()
            if (
                path.is_file()
                and path.suffix.casefold()
                in (
                    ".m3u",
                    ".m3u8",
                )
            )
        ),
        key=lambda path: (
            path.name.casefold()
        ),
    )

    for playlist_path in playlist_files:
        try:
            content = (
                playlist_path.read_text(
                    encoding="utf-8-sig",
                )
            )

        except UnicodeDecodeError:
            content = (
                playlist_path.read_text(
                    encoding="latin-1",
                )
            )

        found = False

        for line in content.splitlines():
            entry = line.strip()

            if (
                not entry
                or entry.startswith("#")
            ):
                continue

            candidate = (
                m3u_entry_to_local_path(
                    entry
                )
            )

            if candidate is None:
                continue

            normalized_candidate = (
                str(candidate)
                .replace("\\", "/")
                .casefold()
            )

            if (
                normalized_candidate
                == normalized_source
            ):
                found = True
                break

            # Fallback para hardlinks.
            #
            # Evitamos hacer stat()/samefile()
            # para todas las canciones de todas
            # las playlists. Los hardlinks que
            # crea la aplicación conservan
            # normalmente el nombre del archivo.
            if (
                source_stat is not None
                and candidate.name.casefold()
                == source_name
            ):
                try:
                    candidate_stat = (
                        candidate.stat()
                    )

                    if (
                        candidate_stat.st_dev
                        == source_stat.st_dev
                        and candidate_stat.st_ino
                        == source_stat.st_ino
                    ):
                        found = True
                        break

                except OSError:
                    pass

        if found:
            matches.append(
                {
                    "name": (
                        playlist_path.stem
                    ),
                    "filename": (
                        playlist_path.name
                    ),
                }
            )

    return {
        "status": "ok",
        "local_path": local_path,
        "count": len(matches),
        "playlists": matches,
    }


def create_manual_playlist(
    playlist_name: str,
):
    PLAYLISTS_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    existing = find_playlist(
        playlist_name
    )

    if existing is not None:
        return {
            "status": "already_exists",
            "name": existing.stem,
            "filename": existing.name,
        }

    safe_name = sanitize_playlist_filename(
        playlist_name
    )

    path = (
        PLAYLISTS_ROOT
        / f"{safe_name}.m3u"
    )

    # Escritura atómica.
    temporary = path.with_suffix(
        ".m3u.tmp"
    )

    temporary.write_text(
        "#EXTM3U\n",
        encoding="utf-8",
    )

    temporary.replace(
        path
    )

    return {
        "status": "created",
        "name": safe_name,
        "filename": path.name,
    }


def add_paths_to_manual_playlist(
    playlist_name: str,
    local_paths: list[str],
    create_if_missing: bool = False,
):
    PLAYLISTS_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    playlist_path = find_playlist(
        playlist_name
    )

    if playlist_path is None:
        if not create_if_missing:
            return {
                "status": "not_found",
                "playlist": playlist_name,
            }

        creation = create_manual_playlist(
            playlist_name
        )

        if creation["status"] not in (
            "created",
            "already_exists",
        ):
            return creation

        playlist_path = find_playlist(
            playlist_name
        )

    if playlist_path is None:
        return {
            "status": "error",
            "error": "playlist_not_available",
        }

    # --------------------------------------------------
    # LEER SIN MODIFICAR EL CONTENIDO EXISTENTE
    # --------------------------------------------------

    try:
        original_content = (
            playlist_path.read_text(
                encoding="utf-8-sig",
            )
        )

    except UnicodeDecodeError:
        original_content = (
            playlist_path.read_text(
                encoding="latin-1",
            )
        ) 

    existing_lines = (
        original_content.splitlines()
    )

    managed_playlist_dir = (
        get_managed_playlist_directory(
            existing_lines
        )
    )

    # Para detectar duplicados usamos únicamente
    # líneas que representan rutas.
    existing_entries = {
        line.strip().replace("\\", "/")
        for line in existing_lines
        if (
            line.strip()
            and not line.lstrip().startswith(
                "#"
            )
        )
    }

    entries_to_add = []
    invalid_paths = []
    duplicates = []

    for local_path in local_paths:
        source_path = Path(
            local_path
        )

        if not source_path.is_file():
            invalid_paths.append(
                local_path
            )
            continue

        effective_path = (
            source_path
        )

        # Si el M3U corresponde claramente a una
        # playlist física gestionada por slskd,
        # trasladamos allí los singles sueltos.
        if managed_playlist_dir is not None:
            try:
                effective_path = (
                    relocate_single_to_playlist(
                        source=source_path,
                        destination_dir=(
                            managed_playlist_dir
                        ),
                    )
                )

            except Exception as exc:
                print(
                    f"[PLAYLIST SINGLE MOVE ERROR] "
                    f"source={str(source_path)!r} | "
                    f"playlist="
                    f"{str(managed_playlist_dir)!r} | "
                    f"error={exc!r}",
                    flush=True,
                )

                invalid_paths.append(
                    local_path
                )
                continue

        entry = local_path_to_m3u_entry(
            str(effective_path)
        )

        if not entry:
            invalid_paths.append(
                str(effective_path)
            )
            continue

        normalized_entry = (
            entry
            .strip()
            .replace("\\", "/")
        )

        if normalized_entry in existing_entries:
            duplicates.append(
                entry
            )
            continue

        entries_to_add.append(
            entry
        )

        existing_entries.add(
            normalized_entry
        )

    # --------------------------------------------------
    # SI NO HAY NADA NUEVO, NO TOCAMOS EL ARCHIVO
    # --------------------------------------------------

    if not entries_to_add:
        stats = get_playlist_stats(
            playlist_path
        )
        return {
            "status": "unchanged",
            "playlist": playlist_path.stem,
            "added": 0,
            "duplicates": len(
                duplicates
            ),
            "invalid": len(
                invalid_paths
            ),
            **stats,
        }

    # --------------------------------------------------
    # PRESERVAR ÍNTEGRAMENTE EL CONTENIDO EXISTENTE
    # Y AÑADIR SOLO AL FINAL
    # --------------------------------------------------

    output_lines = list(
        existing_lines
    )

    if not output_lines:
        output_lines.append(
            "#EXTM3U"
        )

    output_lines.extend(
        entries_to_add
    )

    temporary = (
        playlist_path.with_suffix(
            playlist_path.suffix
            + ".tmp"
        )
    )

    temporary.write_text(
        "\n".join(output_lines)
        + "\n",
        encoding="utf-8",
    )

    os.replace(
        temporary,
        playlist_path,
    )

    stats = get_playlist_stats(
        playlist_path
    )

    return {
        "status": "updated",
        "playlist": playlist_path.stem,
        "added": len(
            entries_to_add
        ),
        "duplicates": len(
            duplicates
        ),
        "invalid": len(
            invalid_paths
        ),
        **stats,
    }