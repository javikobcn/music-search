from ..slskd import SlskdClient
from ..ranking import find_best_match
from ..utils import format_size, format_speed, format_quality
from .download_service import download
from .library_service import exists
import threading
import time

SEARCH_CACHE_TTL_SECONDS = 300
SEARCH_CACHE_MAX_ENTRIES = 250
SEARCH_CACHE_CANDIDATES = 5

_search_cache = {}
_search_cache_lock = threading.RLock()


def _search_cache_key(
    artist,
    track,
    album=None,
):
    return (
        (artist or "").casefold().strip(),
        (track or "").casefold().strip(),
        (album or "").casefold().strip(),
    )


def _filter_excluded_results(
    results,
    excluded_sources,
):
    if not excluded_sources:
        return list(results)

    return [
        result
        for result in results
        if (
            result.username
            not in excluded_sources
            and source_key(result)
            not in excluded_sources
        )
    ]


def _get_cached_shortlist(
    artist,
    track,
    album=None,
):
    key = _search_cache_key(
        artist,
        track,
        album,
    )

    now = time.monotonic()

    with _search_cache_lock:
        cached = _search_cache.get(key)

        if cached is None:
            return None

        stored_at, shortlist = cached

        if (
            now - stored_at
            > SEARCH_CACHE_TTL_SECONDS
        ):
            _search_cache.pop(
                key,
                None,
            )

            print(
                f"[SEARCH CACHE EXPIRED] "
                f"artist={artist!r} | "
                f"track={track!r}",
                flush=True,
            )

            return None

        return list(shortlist)


def _store_cached_shortlist(
    artist,
    track,
    album,
    shortlist,
):
    if not shortlist:
        return

    key = _search_cache_key(
        artist,
        track,
        album,
    )

    now = time.monotonic()

    with _search_cache_lock:
        # Limpiar entradas caducadas.
        expired_keys = [
            existing_key
            for existing_key, (
                stored_at,
                _,
            ) in _search_cache.items()
            if (
                now - stored_at
                > SEARCH_CACHE_TTL_SECONDS
            )
        ]

        for expired_key in expired_keys:
            _search_cache.pop(
                expired_key,
                None,
            )

        # Si ya existe esta búsqueda,
        # la sustituimos por la versión nueva.
        _search_cache.pop(
            key,
            None,
        )

        # Límite global de búsquedas guardadas.
        while (
            len(_search_cache)
            >= SEARCH_CACHE_MAX_ENTRIES
        ):
            oldest_key = min(
                _search_cache,
                key=lambda cache_key: (
                    _search_cache[
                        cache_key
                    ][0]
                ),
            )

            _search_cache.pop(
                oldest_key,
                None,
            )

        _search_cache[key] = (
            now,
            list(shortlist),
        )

    print(
        f"[SEARCH CACHE STORE] "
        f"artist={artist!r} | "
        f"track={track!r} | "
        f"candidates={len(shortlist)} | "
        f"users="
        f"{[item.username for item in shortlist]!r}",
        flush=True,
    )


def _build_candidate_shortlist(
    results,
    artist,
    track,
    album=None,
):
    remaining = list(results)
    shortlist = []

    while (
        remaining
        and len(shortlist)
        < SEARCH_CACHE_CANDIDATES
    ):
        best = find_best_match(
            remaining,
            artist=artist,
            track=track,
            album=album,
        )

        if best is None:
            break

        shortlist.append(
            best
        )

        # Una única alternativa por usuario.
        # Si este usuario falla, queremos que
        # el siguiente retry pruebe otro usuario.
        remaining = [
            result
            for result in remaining
            if (
                result.username
                != best.username
            )
        ]

    return shortlist


def search_best(
    artist,
    track,
    album=None,
    excluded_sources=None,
):
    excluded_sources = set(
        excluded_sources or []
    )

    # --------------------------------------------------
    # RETRY: INTENTAR PRIMERO LA BÚSQUEDA YA REALIZADA
    # --------------------------------------------------

    if excluded_sources:
        cached_shortlist = (
            _get_cached_shortlist(
                artist,
                track,
                album,
            )
        )

        if cached_shortlist is not None:
            available_cached = (
                _filter_excluded_results(
                    cached_shortlist,
                    excluded_sources,
                )
            )

            if available_cached:
                best = (
                    available_cached[0]
                )

                print(
                    f"[SEARCH CACHE HIT] "
                    f"artist={artist!r} | "
                    f"track={track!r} | "
                    f"excluded="
                    f"{sorted(excluded_sources)!r} | "
                    f"user={best.username!r} | "
                    f"file={best.filename!r}",
                    flush=True,
                )

                return best

            print(
                f"[SEARCH CACHE EXHAUSTED] "
                f"artist={artist!r} | "
                f"track={track!r} | "
                f"excluded="
                f"{sorted(excluded_sources)!r}",
                flush=True,
            )

    # --------------------------------------------------
    # NO HAY ALTERNATIVA CACHEADA:
    # HACER BÚSQUEDA REAL EN SOULSEEK
    # --------------------------------------------------

    client = SlskdClient()
    client.login()

    results = client.search(
        artist=artist,
        track=track,
        album=album,
    )

    results = _filter_excluded_results(
        results,
        excluded_sources,
    )

    shortlist = (
        _build_candidate_shortlist(
            results,
            artist=artist,
            track=track,
            album=album,
        )
    )

    # --------------------------------------------------
    # FALLBACK PARA TÍTULOS CON "/"
    # --------------------------------------------------

    if (
        not shortlist
        and "/" in track
    ):
        fallback_track = (
            track.split(
                "/",
                1,
            )[0].strip()
        )

        print(
            f"[SEARCH FALLBACK] "
            f"{track!r} -> "
            f"{fallback_track!r}",
            flush=True,
        )

        results = client.search(
            artist=artist,
            track=track,
            album=album,
            search_track=fallback_track,
        )

        results = (
            _filter_excluded_results(
                results,
                excluded_sources,
            )
        )

        shortlist = (
            _build_candidate_shortlist(
                results,
                artist=artist,
                track=track,
                album=album,
            )
        )

    # --------------------------------------------------
    # GUARDAR SOLO LAS MEJORES ALTERNATIVAS
    # --------------------------------------------------

    if shortlist:
        _store_cached_shortlist(
            artist=artist,
            track=track,
            album=album,
            shortlist=shortlist,
        )

        best = shortlist[0]

    else:
        best = None

    print(
        "RESULTADOS:",
        len(results),
        flush=True,
    )

    print(
        "BEST:",
        best,
        flush=True,
    )

    return best

def search_and_download(artist, track, album=None):

    best = search_best(
        artist,
        track,
        album,
    )

    if best is None:

        return {
            "status": "not_found"
        }

    if exists(best.filename):

        return {
            "status": "already_exists",
            "match": best,
        }

    download_result = download(best)

    return {
        "status": "queued",
        "track": {
            "artist": artist,
            "title": track,
            "album": album,
            "quality": format_quality(best),
            "size": format_size(best.size),
            "score": best.score,
            "reasons": best.reasons,
        },

        "source": {
            "username": best.username,
            "queue": best.queueLength,
            "speed": format_speed(best.uploadSpeed),
        },

        "download": download_result.enqueued[0],
    }

def source_key(result) -> str:
    return f"{result.username}|{result.filename}"