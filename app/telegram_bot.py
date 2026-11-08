import os
import asyncio
import logging
import requests
import time
import re
import secrets
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
    ApplicationHandlerStop,
    TypeHandler,
)
from urllib.parse import urlparse, parse_qs
from datetime import datetime

SPOTIFY_PLAYLIST_RE = re.compile(
    r"https?://open\.spotify\.com/"
    r"(?:intl-[a-z]{2}/)?playlist/"
    r"[A-Za-z0-9]+",
    re.IGNORECASE,
)

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]


logger = logging.getLogger(__name__)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_URL = os.getenv(
    "MUSIC_SEARCH_API_URL",
    "http://music-search:8080",
)

_allowed_user_id = os.getenv(
    "TELEGRAM_ALLOWED_USER_ID",
    "",
).strip()

ALLOWED_TELEGRAM_USER_ID = (
    int(_allowed_user_id)
    if _allowed_user_id.isdigit()
    else None
)

ALBUMS_PER_PAGE = 8
ARTIST_TRACKS_PER_PAGE = 10
EXPLORE_TRACKS_PER_PAGE = 10
MANUAL_PLAYLISTS_PER_PAGE = 8
AUDIO_EXTENSIONS = (
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


async def authorized_user_guard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    chat = update.effective_chat

    if (
        ALLOWED_TELEGRAM_USER_ID is not None
        and user is not None
        and user.id == ALLOWED_TELEGRAM_USER_ID
    ):
        # Usuario autorizado:
        # dejamos continuar el update.
        return

    logger.warning(
        f"[TELEGRAM BLOCKED] "
        f"user_id={user.id if user else None} | "
        f"username={user.username if user else None} | "
        f"chat_id={chat.id if chat else None} | "
        f"chat_type={chat.type if chat else None}"
    )

    # Impide que cualquier handler posterior
    # procese este update.
    raise ApplicationHandlerStop


def parse_m3u_playlist(
    content: str,
):
    tracks = []

    pending_extinf = None

    for raw_line in content.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF:"):
            try:
                metadata = line.split(",", 1)[1].strip()
            except IndexError:
                metadata = ""

            pending_extinf = metadata or None
            continue

        if line.startswith("#"):
            continue

        artist = None
        track = None

        # ----------------------------------------------
        # 1. PRIORIZAR #EXTINF
        # ----------------------------------------------

        if pending_extinf:
            parts = re.split(
                r"\s+[-–—]\s+",
                pending_extinf,
                maxsplit=1,
            )

            if len(parts) == 2:
                artist = parts[0].strip()
                track = parts[1].strip()

        # ----------------------------------------------
        # 2. FALLBACK: NOMBRE DEL ARCHIVO
        # ----------------------------------------------

        if not artist or not track:
            normalized_path = (
                line.replace("\\", "/")
            )

            filename = (
                normalized_path
                .rsplit("/", 1)[-1]
            )

            lower_filename = (
                filename.casefold()
            )

            for extension in AUDIO_EXTENSIONS:
                if lower_filename.endswith(
                    extension
                ):
                    filename = filename[
                        :-len(extension)
                    ]
                    break

            # Quitar numeración habitual:
            # "01 - Artist - Track"
            filename = re.sub(
                r"^\s*\d+\s*[-_.]\s*",
                "",
                filename,
            )

            parts = re.split(
                r"\s+[-–—]\s+",
                filename,
                maxsplit=1,
            )

            if len(parts) == 2:
                artist = parts[0].strip()
                track = parts[1].strip()

            # ------------------------------------------
            # 3. FALLBACK: ARTISTA DESDE LA RUTA
            #
            # Ejemplo:
            # /music/Prodigy/Breathe.mp3
            #
            # artista = Prodigy
            # track   = Breathe
            #
            # También soporta:
            # /music/Prodigy/Album/Breathe.mp3
            # ------------------------------------------

            else:
                path_parts = [
                    part.strip()
                    for part in normalized_path.split("/")
                    if part.strip()
                ]

                path_folders = path_parts[:-1]

                inferred_artist = None

                # Si encontramos una carpeta raíz
                # "music" o "musica", tomamos como
                # artista la carpeta inmediatamente
                # posterior.
                for index, folder in enumerate(
                    path_folders
                ):
                    if folder.casefold() in (
                        "music",
                        "musica",
                    ):
                        if (
                            index + 1
                            < len(path_folders)
                        ):
                            inferred_artist = (
                                path_folders[
                                    index + 1
                                ].strip()
                            )
                        break

                # Si no existe una raíz reconocible,
                # usamos la carpeta padre inmediata.
                if (
                    not inferred_artist
                    and path_folders
                ):
                    inferred_artist = (
                        path_folders[-1]
                        .strip()
                    )

                if inferred_artist and filename:
                    artist = inferred_artist
                    track = filename.strip()

        pending_extinf = None

        if artist and track:
            tracks.append(
                {
                    "artist": artist,
                    "track": track,
                }
            )

    return tracks


async def api_post(
    path: str,
    payload: dict,
    timeout: int = 60,
    retries: int = 3,
):
    def request():
        last_error = None

        for attempt in range(retries):
            try:
                response = requests.post(
                    f"{API_URL}{path}",
                    json=payload,
                    timeout=timeout,
                )

                response.raise_for_status()
                return response.json()

            except requests.RequestException as error:
                last_error = error

                if attempt < retries - 1:                    
                    time.sleep(2)

        raise last_error

    return await asyncio.to_thread(request)

async def api_get(
    path: str,
    retries: int = 3,
):
    def request():
        last_error = None

        for attempt in range(retries):
            try:
                response = requests.get(
                    f"{API_URL}{path}",
                    timeout=15,
                )

                response.raise_for_status()
                return response.json()

            except requests.RequestException as error:
                last_error = error

                if attempt < retries - 1:                    
                    time.sleep(2)

        raise last_error

    return await asyncio.to_thread(request)

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "🎵 Music bot listo.\n\n"
        "Escribe una canción, por ejemplo:\n"
        "firestarter - prodigy"
    )

def format_duration(
    length: int | None,
) -> str:
    if not length:
        return "--:--"

    length = int(length)

    minutes = length // 60
    seconds = length % 60

    return f"{minutes}:{seconds:02d}"

def format_total_duration(
    total_seconds: int | None,
) -> str:
    if not total_seconds:
        return "0:00"

    total_seconds = int(total_seconds)

    hours = total_seconds // 3600
    minutes = (
        total_seconds % 3600
    ) // 60
    seconds = total_seconds % 60

    if hours:
        return (
            f"{hours}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return (
        f"{minutes}:"
        f"{seconds:02d}"
    )

def format_elapsed_time(
    started_at: datetime,
) -> str:
    elapsed_seconds = max(
        0,
        int(
            (
                datetime.now()
                - started_at
            ).total_seconds()
        ),
    )

    hours = elapsed_seconds // 3600

    minutes = (
        elapsed_seconds % 3600
    ) // 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}"
    )

def format_file_size(
    size_bytes: int,
) -> str:
    size = float(
        size_bytes
    )

    gb = 1024 ** 3
    mb = 1024 ** 2

    if size >= gb:
        return (
            f"{size / gb:.2f} GB"
        )

    return (
        f"{size / mb:.1f} MB"
    )

async def send_failed_tracks(
    application,
    chat_id: int,
    failed_tracks: list,
):
    if not failed_tracks:
        return

    max_length = 3500

    header = (
        f"⚠️ Canciones no descargadas "
        f"({len(failed_tracks)})\n\n"
    )

    chunks = []
    current = header

    for item in failed_tracks:

        if isinstance(item, dict):
            artist = (
                item.get("artist")
                or "Artista desconocido"
            )

            track = (
                item.get("track")
                or "Canción desconocida"
            )

            reason = (
                item.get("error")
                or item.get("reason")
                or item.get("status")
                or "not_found"
            )

            line = (
                f"• {artist} - {track} "
                f"[{reason}]"
            )

        else:
            line = f"• {item}"

        if (
            len(current)
            + len(line)
            + 1
            > max_length
        ):
            chunks.append(current)

            current = (
                "⚠️ Canciones no descargadas "
                "(continuación)\n\n"
                + line
            )

        else:
            current += line + "\n"

    if current.strip():
        chunks.append(current)

    for chunk in chunks:
        await application.bot.send_message(
            chat_id=chat_id,
            text=chunk.strip(),
        )


def format_playlist_location(
    destination: str | None,
) -> str:
    if not destination:
        return "Desconocida"

    path = (
        destination
        .replace("\\", "/")
        .rstrip("/")
    )

    if path.startswith("/downloads/"):
        return path[
            len("/downloads/"):
        ]

    if path.startswith("/library/"):
        return (
            "musica/"
            + path[
                len("/library/"):
            ]
        )

    return path

def format_local_location(
    local_path: str | None,
) -> str:
    if not local_path:
        return "Desconocida"

    path = (
        local_path
        .replace("\\", "/")
        .rstrip("/")
    )

    if path.startswith("/downloads/"):
        relative = path[
            len("/downloads/"):
        ]

        folder = relative.rsplit(
            "/",
            1,
        )[0]

        return folder or "downloads"

    if path.startswith("/library/"):
        relative = path[
            len("/library/"):
        ]

        if "/" not in relative:
            return "musica"

        folder = relative.rsplit(
            "/",
            1,
        )[0]

        return (
            f"musica/{folder}"
            if folder
            else "musica"
        )

    if "/" in path:
        return path.rsplit(
            "/",
            1,
        )[0]

    return path

async def show_explore_results_page(
    message,
    context,
    page: int,
):
    tracks = context.user_data.get(
        "explore_tracks",
        [],
    )

    artists = context.user_data.get(
        "explore_artists",
        [],
    )

    search_query = context.user_data.get(
        "explore_query",
        "",
    )

    total_tracks = len(tracks)

    total_pages = max(
        1,
        (
            total_tracks
            + EXPLORE_TRACKS_PER_PAGE
            - 1
        )
        // EXPLORE_TRACKS_PER_PAGE,
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1,
        ),
    )

    start = (
        page
        * EXPLORE_TRACKS_PER_PAGE
    )

    end = (
        start
        + EXPLORE_TRACKS_PER_PAGE
    )

    page_tracks = tracks[start:end]

    keyboard = []

    # Conservamos los primeros artistas
    # en todas las páginas.
    for index, artist in enumerate(
        artists[:3]
    ):
        name = artist.get(
            "name",
            "Artista",
        )

        disambiguation = artist.get(
            "disambiguation",
            "",
        )

        if disambiguation:
            label = short_button_text(
                f"🎤 {name} · {disambiguation}"
            )
        else:
            label = short_button_text(
                f"🎤 {name}"
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=(
                        f"explore_artist:{index}"
                    ),
                )
            ]
        )

    for local_index, track in enumerate(
        page_tracks
    ):
        real_index = (
            start + local_index
        )

        duration = format_duration(
            track.get("length")
        )

        label = short_button_text(
            f"🎵 {track['artist']} - "
            f"{track['track']} · "
            f"{duration}"
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=(
                        f"explore_track:{real_index}"
                    ),
                )
            ]
        )

    if total_pages > 1:
        navigation = []

        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    "⬅️",
                    callback_data=(
                        f"explore_page:{page - 1}"
                    ),
                )
            )

        navigation.append(
            InlineKeyboardButton(
                f"{page + 1}/{total_pages}",
                callback_data="explore_page:current",
            )
        )

        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    "➡️",
                    callback_data=(
                        f"explore_page:{page + 1}"
                    ),
                )
            )

        keyboard.append(
            navigation
        )

    text = (
        f"🔎 Resultados para “{search_query}”\n\n"
        "🎤 Artistas relacionados\n"
        "🎵 Canciones encontradas\n"
    )

    if total_pages > 1:
        text += (
            f"\nPágina {page + 1}/{total_pages}"
            f" · {total_tracks} canciones"
        )

    await message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )

async def handle_exploratory_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    quality_mode="best",
):
    message = await update.message.reply_text(
        f"🔎 Buscando coincidencias para "
        f"“{text}”..."
    )

    try:
        result = await api_post(
            "/search-options",
            {
                "query": text,
            },
            timeout=60,
            retries=3,
        )

    except Exception:
        logger.exception(
            f"[EXPLORE] Error buscando {text!r}"
        )

        await message.edit_text(
            "⚠️ No he podido realizar "
            "la búsqueda."
        )
        return

    if result.get("status") == (
        "temporarily_unavailable"
    ):
        await message.edit_text(
            "⚠️ MusicBrainz no está disponible "
            "temporalmente."
        )
        return

    tracks = result.get("tracks", [])
    artists = result.get("artists", [])

    if not tracks and not artists:
        await message.edit_text(
            "⚠️ No he encontrado coincidencias."
        )
        return

    for item in tracks + artists:
        item["quality_mode"] = quality_mode

    context.user_data[
        "explore_tracks"
    ] = tracks

    context.user_data[
        "explore_artists"
    ] = artists

    context.user_data[
        "explore_query"
    ] = text

    await show_explore_results_page(
        message,
        context,
        page=0,
    )

async def ask_search_quality(update, context, kind):
    token = secrets.token_hex(8)
    # A newer request replaces an unanswered prompt; buttons are single-use.
    context.user_data["pending_search_quality"] = {
        "token": token, "kind": kind, "update": update,
        "args": list(context.args or []),
    }
    await update.message.reply_text(
        "🎧 ¿Qué formato prefieres?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎵 MP3 320 kbps",
                                  callback_data=f"quality:{token}:mp3_320")],
            [InlineKeyboardButton("💎 Mejor calidad disponible",
                                  callback_data=f"quality:{token}:best")],
        ]),
    )


async def search_quality_callback(update, context):
    query = update.callback_query
    _, token, quality_mode = query.data.split(":")
    pending = context.user_data.get("pending_search_quality")
    if (not pending or pending["token"] != token
            or quality_mode not in ("mp3_320", "best")):
        await query.answer("Esta búsqueda ha caducado.")
        return
    context.user_data.pop("pending_search_quality")
    await query.answer()
    await query.edit_message_text(
        "🎵 MP3 320 kbps" if quality_mode == "mp3_320"
        else "💎 Mejor calidad disponible"
    )
    runners = {"text": run_text_search, "album": run_album_search,
               "m3u": run_m3u_search}
    previous_args = context.args
    context.args = pending["args"]
    try:
        await runners[pending["kind"]](pending["update"], context, quality_mode)
    finally:
        context.args = previous_args


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text.strip()

    final_name_token = context.user_data.get(
        "waiting_playlist_final_name_token"
    )

    if final_name_token:
        final_states = context.user_data.get(
            "playlist_final_states",
            {},
        )

        if final_name_token not in final_states:
            context.user_data.pop(
                "waiting_playlist_final_name_token",
                None,
            )

            await update.message.reply_text(
                "⚠️ Esta playlist pendiente "
                "ha caducado."
            )
            return

        final_name = text.strip()

        if not final_name:
            await update.message.reply_text(
                "⚠️ Escribe un nombre válido."
            )
            return

        if len(final_name) > 120:
            await update.message.reply_text(
                "⚠️ El nombre es demasiado largo. "
                "Usa como máximo 120 caracteres."
            )
            return

        await update.message.reply_text(
            "💾 Guardando playlist..."
        )

        await finalize_playlist_with_name(
            message=update.message,
            context=context,
            final_name=final_name,
            final_token=final_name_token,
        )

        return

    if context.user_data.get(
        "waiting_album_playlist_name"
    ):
        state = context.user_data.get(
            "album_playlist_state"
        )

        if not state:
            context.user_data.pop(
                "waiting_album_playlist_name",
                None,
            )

            await update.message.reply_text(
                "⚠️ El álbum pendiente "
                "ha caducado."
            )
            return

        playlist_name = text.strip()

        if not playlist_name:
            await update.message.reply_text(
                "⚠️ Escribe un nombre válido."
            )
            return

        if len(playlist_name) > 120:
            await update.message.reply_text(
                "⚠️ El nombre es demasiado largo. "
                "Usa como máximo 120 caracteres."
            )
            return

        context.user_data.pop(
            "waiting_album_playlist_name",
            None,
        )

        local_paths = state.get(
            "local_paths",
            [],
        )

        playlist_name = (
            album_playlist_quality_name(
                playlist_name,
                local_paths,
            )
        )

        try:
            result = await api_get(
                "/manual-playlists"
            )

        except Exception:
            logger.exception(
                "[ALBUM PLAYLIST] "
                "Error consultando playlists"
            )

            await update.message.reply_text(
                "❌ No he podido consultar "
                "las playlists."
            )
            return

        existing = {
            item.get(
                "name",
                "",
            ).casefold()
            for item in result.get(
                "playlists",
                [],
            )
        }

        already_exists = (
            playlist_name.casefold()
            in existing
        )

        context.user_data[
            "album_playlist_pending_name"
        ] = playlist_name

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Sí",
                        callback_data=(
                            "albumpl_confirm_yes"
                        ),
                    ),
                    InlineKeyboardButton(
                        "❌ No",
                        callback_data=(
                            "albumpl_confirm_no"
                        ),
                    ),
                ]
            ]
        )

        if already_exists:
            action_text = (
                "Se actualizará esta playlist "
                "con las canciones del álbum."
            )
        else:
            action_text = (
                "Se creará esta playlist "
                "con las canciones del álbum."
            )

        await update.message.reply_text(
            (
                "⚠️ ¿Estás seguro?\n\n"
                f"💿 {state.get('album', 'Álbum')}\n"
                f"👤 {state.get('artist', 'Desconocido')}\n"
                f"📋 {playlist_name}\n"
                f"🎵 {len(local_paths)} canciones\n\n"
                f"{action_text}"
            ),
            reply_markup=keyboard,
        )

        return               

    if context.user_data.get(
        "waiting_new_playlist_name"
    ):
        job_id = context.user_data.get(
            "playlist_track_job_id"
        )

        if not job_id:
            context.user_data.pop(
                "waiting_new_playlist_name",
                None,
            )

            await update.message.reply_text(
                "⚠️ La descarga asociada "
                "ya no está disponible."
            )
            return

        playlist_name = text.strip()

        if not playlist_name:
            await update.message.reply_text(
                "⚠️ Escribe un nombre válido "
                "para la playlist."
            )
            return

        if len(playlist_name) > 120:
            await update.message.reply_text(
                "⚠️ El nombre es demasiado largo. "
                "Usa como máximo 120 caracteres."
            )
            return

        try:
            job = await api_get(
                f"/jobs/{job_id}"
            )

        except Exception:
            logger.exception(
                f"[PLAYLIST CREATE] "
                f"Error obteniendo job={job_id}"
            )

            await update.message.reply_text(
                "❌ No he podido obtener "
                "la información de la canción."
            )
            return

        if job.get("status") != "completed":
            await update.message.reply_text(
                "⚠️ La canción ya no aparece "
                "como completada."
            )
            return

        local_path = job.get(
            "local_path"
        )

        if not local_path:
            await update.message.reply_text(
                "⚠️ No encuentro la ruta local "
                "de esta canción."
            )
            return

        artist = job.get(
            "artist",
            "Desconocido",
        )

        track = job.get(
            "track",
            "Desconocida",
        )

        context.user_data.pop(
            "waiting_new_playlist_name",
            None,
        )

        context.user_data[
            "track_playlist_pending"
        ] = {
            "playlist": playlist_name,
            "local_path": local_path,
            "artist": artist,
            "track": track,
            "create_if_missing": True,
        }

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Sí",
                        callback_data=(
                            "trackpl_confirm_yes"
                        ),
                    ),
                    InlineKeyboardButton(
                        "❌ No",
                        callback_data=(
                            "trackpl_confirm_no"
                        ),
                    ),
                ]
            ]
        )

        await update.message.reply_text(
            (
                "⚠️ ¿Estás seguro?\n\n"
                f"🎵 {track}\n"
                f"👤 {artist}\n"
                f"📋 {playlist_name}\n\n"
                "Se creará o actualizará "
                "esta playlist."
            ),
            reply_markup=keyboard,
        )

        return    

    await ask_search_quality(update, context, "text")


async def run_text_search(update, context, quality_mode):
    text = update.message.text.strip()
    playlist_id = extract_youtube_playlist_id(text)

    if playlist_id:
        context.user_data["youtube_quality_mode"] = quality_mode
        message = await update.message.reply_text(
            "🔎 Leyendo playlist de YouTube..."
        )

        try:
            items = await get_youtube_playlist(
                playlist_id
            )

            if not items:
                await message.edit_text(
                    "⚠️ No he encontrado canciones en la playlist."
                )
                return

            playlist_title = await get_youtube_playlist_title(
                playlist_id
            )

            if not playlist_title:
                playlist_title = "YouTube Playlist"

            context.user_data["youtube_playlist_title"] = playlist_title
            context.user_data["youtube_playlist"] = items

            preview = items[:10]

            lines = [
                "🎵 Playlist de YouTube detectada",
                "",
                f"📁 {playlist_title}",
                f"🎶 {len(items)} canciones",
                "",
            ]

            for index, item in enumerate(
                preview,
                start=1,
            ):
                lines.append(
                    f"{index}. {item['title']}"
                )

            if len(items) > 10:
                lines.append("")
                lines.append(
                    f"... y {len(items) - 10} más"
                )

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔎 Analizar playlist",
                            callback_data="yt_analyze",
                        )
                    ]
                ]
            )

            await message.edit_text(
                "\n".join(lines),
                reply_markup=keyboard,
            )

        except Exception:
            logger.exception(
                "[TELEGRAM] Error leyendo playlist YouTube"
            )

            await message.edit_text(
                "❌ Error leyendo la playlist de YouTube."
            )

        return

    spotify_match = SPOTIFY_PLAYLIST_RE.search(
        text
    )

    if spotify_match:
        context.user_data["spotify_quality_mode"] = quality_mode
        await handle_spotify_playlist(
            update,
            context,
            spotify_match.group(0),
        )
        return

    logger.warning(
    f"[TELEGRAM] mensaje recibido | "
    f"text={text!r} | "
    f"chat_id={update.effective_chat.id if update.effective_chat else None} | "
    f"chat_type={update.effective_chat.type if update.effective_chat else None} | "
    f"user_id={update.effective_user.id if update.effective_user else None} | "
    f"username={update.effective_user.username if update.effective_user else None} | "
    f"message_date={update.message.date.isoformat() if update.message else None}"
    )

    searching_message = await update.message.reply_text(
        "🔎 Buscando..."
    )

    if not re.search(
        r"\s+[-–—]\s+",
        text,
    ):
        await handle_exploratory_search(
            update,
            context,
            text,
            quality_mode=quality_mode,
        )
        return

    try:
        resolved = await api_post(
            "/resolve-track",
            {
                "query": text,
            },
        )

        if resolved.get("status") != "resolved":
            await searching_message.edit_text(
                "⚠️ No he podido identificar "
                "con seguridad la canción."
            )
            return

        artist = resolved["artist"]
        track = resolved["track"]

        await searching_message.edit_text(
            f"🔎 Encontrado:\n"
            f"🎵 {track}\n"
            f"👤 {artist}\n\n"
            f"Buscando la mejor versión..."
        )

        result = await api_post(
            "/search-and-download",
            {
                "artist": artist,
                "track": track,
                "quality_mode": quality_mode,
            },
            timeout=180,
            retries=1,
        )

        status = result.get("status")

        if status == "queued":
            position = result.get("position", "?")
            job_id = result.get("job_id")

            await searching_message.edit_text(
                f"⬇️ Añadido a la cola\n\n"
                f"🎵 {track}\n"
                f"👤 {artist}\n"
                f"📋 Posición: {position}"
            )

            if job_id:
                context.application.create_task(
                    watch_job(
                        application=context.application,
                        chat_id=update.effective_chat.id,
                        job_id=job_id,
                        artist=artist,
                        track=track,
                    )
                )

            return

        if status == "completed":
            download = result.get(
                "download",
                {},
            )

            extension = (
                download.get("extension")
                or ""
            ).upper()

            length = download.get(
                "length"
            )

            duration = format_duration(
                length
            )

            local_path = download.get(
                "local_path"
            )

            local_size = int(
                download.get(
                    "local_size",
                    0,
                )
                or 0
            )

            size_text = (
                format_file_size(
                    local_size
                )
                if local_size > 0
                else "Desconocido"
            )            

            location = (
                format_local_location(
                    local_path
                )
            )

            job_id = result.get(
                "job_id"
            )

            if not extension:
                extension = "Desconocido"

            playlists = (
                await get_track_playlist_memberships(
                    local_path
                )
            )

            lines = [
                "✅ Ya estaba descargada",
                "",
                f"🎵 {track}",
                f"👤 {artist}",
                f"💿 {extension}",
                f"⏱️ {duration}",
                f"💾 Tamaño: {size_text}",
                f"📁 {location}",
                "",
            ]

            if playlists is None:
                lines.append(
                    "📋 No he podido comprobar "
                    "sus playlists."
                )

            elif playlists:
                lines.append(
                    "📋 Está incluida en:"
                )

                lines.extend(
                    f"• {name}"
                    for name in playlists
                )

            else:
                lines.append(
                    "📋 No está incluida "
                    "en ninguna playlist."
                )

            keyboard = None

            if (
                local_path
                and job_id
            ):
                lines.extend(
                    [
                        "",
                        (
                            "¿Quieres añadirla "
                            "a otra playlist?"
                            if playlists
                            else
                            "¿Quieres añadirla "
                            "a una playlist?"
                        ),
                    ]
                )

                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "📋 Playlist existente",
                                callback_data=(
                                    f"trackpl_existing:"
                                    f"{job_id}"
                                ),
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "➕ Crear nueva",
                                callback_data=(
                                    f"trackpl_new:"
                                    f"{job_id}"
                                ),
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "❌ No",
                                callback_data=(
                                    f"trackpl_cancel:"
                                    f"{job_id}"
                                ),
                            )
                        ],
                    ]
                )

            await searching_message.edit_text(
                "\n".join(lines),
                reply_markup=keyboard,
            )

            return

        await searching_message.edit_text(
            f"ℹ️ {track}\n"
            f"👤 {artist}\n\n"
            f"Estado: {status or 'desconocido'}"
        )

    except requests.RequestException:
        logger.exception(
            "[TELEGRAM] Error comunicando con FastAPI"
        )

        await searching_message.edit_text(
            "❌ Error comunicando con el servidor."
        )

    except Exception:
        logger.exception(
            "[TELEGRAM] Error procesando mensaje"
        )

        await searching_message.edit_text(
            "❌ Ha ocurrido un error procesando la búsqueda."
        )

async def spotify_cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    context.user_data.pop(
        "spotify_playlist",
        None,
    )

    await query.edit_message_text(
        "❌ Descarga de playlist cancelada."
    )

async def spotify_download_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    result = context.user_data.get(
        "spotify_playlist"
    )

    if not result:
        await query.edit_message_text(
            "⚠️ La información de la playlist ha caducado."
        )
        return

    playlist_data = result.get(
        "playlist",
        {},
    )

    tracks = result.get(
        "tracks",
        [],
    )

    playlist_name = (
        playlist_data.get("name")
        or "Spotify Playlist"
    )

    if not tracks:
        await query.edit_message_text(
            "⚠️ La playlist no contiene canciones."
        )
        return

    await query.edit_message_text(
        (
            "🎧 Descarga de Spotify iniciada\n\n"
            f"📋 {playlist_name}\n"
            f"🎵 {len(tracks)} canciones"
        )
    )

    context.application.create_task(
    process_playlist_download(
        application=context.application,
        chat_id=update.effective_chat.id,
        tracks=tracks,
        playlist=playlist_name,
        ask_final_name=True,
        user_data=context.user_data,
        quality_mode=context.user_data.get("spotify_quality_mode", "best"),
    )
)       

async def get_track_playlist_memberships(
    local_path: str | None,
) -> list[str] | None:
    if not local_path:
        return []

    try:
        result = await api_post(
            "/manual-playlists/containing-track",
            {
                "local_path": local_path,
            },
            timeout=30,
            retries=1,
        )

    except Exception:
        logger.exception(
            f"[TRACK PLAYLIST LOOKUP] "
            f"local_path={local_path!r}"
        )

        return None

    if result.get("status") != "ok":
        return None

    return [
        item.get("name")
        for item in result.get(
            "playlists",
            [],
        )
        if item.get("name")
    ]         

async def watch_job(
    application,
    chat_id: int,
    job_id: str,
    artist: str,
    track: str,
):    
    while True:
        await asyncio.sleep(3)

        try:
            job = await api_get(
                f"/jobs/{job_id}"
            )
        except Exception:
            logger.exception(
                f"[TELEGRAM] Error consultando job {job_id}"
            )
            continue

        status = job.get("status")
        
        if status == "completed":
            track = job.get("track", "Desconocida")
            artist = job.get("artist", "Desconocido")

            local_path = job.get(
                "local_path"
            )            

            extension = (
                job.get("extension")
                or ""
            ).upper()

            length = job.get("length") or 0

            if length:
                length = int(length)

                minutes = length // 60
                seconds = length % 60
                duration = f"{minutes}:{seconds:02d}"
            else:
                duration = "Desconocida"

            local_size = int(
                job.get(
                    "local_size",
                    0,
                )
                or 0
            )

            if local_size > 0:
                size_text = format_file_size(
                    local_size
                )
            else:
                size_text = "Desconocido"                

            if extension in ("FLAC", "WAV", "ALAC", "AIFF"):
                destination = "lossless"
            elif extension == "MP3":
                destination = "normal"
            else:
                destination = "normal"
                
            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "✅ Descarga completada\n\n"
                    f"🎵 {track}\n"
                    f"👤 {artist}\n"
                    f"💿 {extension or 'Desconocido'}\n"
                    f"⏱️ {duration}\n"
                    f"💾 Tamaño: {size_text}\n"
                    f"📁 {destination}"
                ),
            )

            if local_path:
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "📋 Playlist existente",
                                callback_data=(
                                    f"trackpl_existing:"
                                    f"{job_id}"
                                ),
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "➕ Crear nueva",
                                callback_data=(
                                    f"trackpl_new:"
                                    f"{job_id}"
                                ),
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "❌ No",
                                callback_data=(
                                    f"trackpl_cancel:"
                                    f"{job_id}"
                                ),
                            )
                        ],
                    ]
                )

                await application.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "¿Quieres añadir esta canción "
                        "a una playlist?"
                    ),
                    reply_markup=keyboard,
                )

            return

        if status == "failed":
            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ Descarga fallida\n\n"
                    f"🎵 {track}\n"
                    f"👤 {artist}\n\n"
                    f"{job.get('error') or 'Sin información adicional'}"
                ),
            )
            return

        if status == "cancelled":
            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🚫 Descarga cancelada\n\n"
                    f"🎵 {track}\n"
                    f"👤 {artist}"
                ),
            )
            return

def album_label(album: dict) -> str:
    title = album["title"]
    date = album.get("date") or ""
    year = date[:4] if date else ""

    secondary_types = album.get("secondary_types", [])

    type_suffix = ""

    if "Live" in secondary_types:
        type_suffix = " · Live"
    elif "Remix" in secondary_types:
        type_suffix = " · Remix"
    elif "Soundtrack" in secondary_types:
        type_suffix = " · Soundtrack"
    elif "Compilation" in secondary_types:
        type_suffix = " · Compilation"
    elif "DJ-mix" in secondary_types:
        type_suffix = " · DJ Mix"

    if year:
        return f"{title} · {year}{type_suffix}"

    return f"{title}{type_suffix}"


def build_album_keyboard(
    albums: list[dict],
    page: int,
):
    start = page * ALBUMS_PER_PAGE
    end = start + ALBUMS_PER_PAGE

    page_albums = albums[start:end]

    keyboard = []

    for album in page_albums:
        keyboard.append(
            [
                InlineKeyboardButton(
                    album_label(album),
                    callback_data=f"album:{album['id']}",
                )
            ]
        )

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Anterior",
                callback_data=f"album_page:{page - 1}",
            )
        )

    if end < len(albums):
        navigation.append(
            InlineKeyboardButton(
                "Siguiente ➡️",
                callback_data=f"album_page:{page + 1}",
            )
        )

    if navigation:
        keyboard.append(navigation)

    return InlineKeyboardMarkup(keyboard)
        

async def album_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    artist_query = " ".join(context.args).strip()

    if not artist_query:
        await update.message.reply_text(
            "Usa:\n"
            "/album Daft Punk"
        )
        return

    await ask_search_quality(update, context, "album")


async def run_album_search(update, context, quality_mode):
    artist_query = " ".join(context.args).strip()
    context.user_data["album_quality_mode"] = quality_mode
    message = await update.message.reply_text(
        f"🔎 Buscando álbumes de {artist_query}..."
    )

    try:
        result = await api_post(
            "/albums",
            {
                "artist": artist_query,
            },
        )

        if result.get("status") != "resolved":
            await message.edit_text(
                f"⚠️ No he encontrado álbumes de "
                f"{artist_query}."
            )
            return

        artist = result["artist"]

        albums = [
            album
            for album in result.get("albums", [])
            if "Interview"
            not in album.get("secondary_types", [])
        ]

        if not albums:
            await message.edit_text(
                f"⚠️ No he encontrado álbumes de {artist}."
            )
            return

        context.user_data["album_artist"] = artist
        context.user_data["album_results"] = albums

        await message.edit_text(
            f"💿 {artist}\n\n"
            f"Selecciona un álbum:",
            reply_markup=build_album_keyboard(
                albums,
                page=0,
            ),
        )

    except Exception:
        logger.exception(
            "[TELEGRAM] Error buscando álbumes"
        )

        await message.edit_text(
            "❌ Error buscando la discografía."
        )

async def album_playlist_name_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    state = context.user_data.get(
        "album_playlist_state"
    )

    if not state:
        await query.edit_message_text(
            "⚠️ Esta selección ha caducado."
        )
        return

    choice = query.data.split(
        ":",
        1,
    )[1]

    artist = state["artist"]
    album = state["album"]

    if choice == "album":
        playlist_name = (
            f"{artist} - {album}"
        )

    elif choice == "artist":
        playlist_name = artist

    else:
        return

    await prepare_album_playlist(
        query=query,
        context=context,
        playlist_name=playlist_name,
    )


async def album_playlist_custom_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if not context.user_data.get(
        "album_playlist_state"
    ):
        await query.edit_message_text(
            "⚠️ Esta selección ha caducado."
        )
        return

    context.user_data[
        "waiting_album_playlist_name"
    ] = True

    await query.edit_message_text(
        (
            "✏️ Escribe el nombre que quieres "
            "usar para la playlist."
        )
    )


async def album_playlist_confirm_yes_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    playlist_name = context.user_data.get(
        "album_playlist_pending_name"
    )

    if not playlist_name:
        await query.edit_message_text(
            "⚠️ Esta selección ha caducado."
        )
        return

    await save_album_playlist(
        query=query,
        context=context,
        playlist_name=playlist_name,
    )


async def album_playlist_confirm_no_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    state = context.user_data.get(
        "album_playlist_state"
    )

    context.user_data.pop(
        "album_playlist_pending_name",
        None,
    )

    if not state:
        await query.edit_message_text(
            "⚠️ Esta selección ha caducado."
        )
        return

    artist = state.get(
        "artist",
        "Artista",
    )

    album = state.get(
        "album",
        "Álbum",
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    short_button_text(
                        f"💿 {artist} - {album}"
                    ),
                    callback_data=(
                        "albumpl_name:album"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    short_button_text(
                        f"👤 {artist}"
                    ),
                    callback_data=(
                        "albumpl_name:artist"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ Otro nombre",
                    callback_data=(
                        "albumpl_custom"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ No crear playlist",
                    callback_data=(
                        "albumpl_no"
                    ),
                )
            ],
        ]
    )

    await query.edit_message_text(
        (
            "📋 Elige cómo quieres guardar "
            "las canciones del álbum:"
        ),
        reply_markup=keyboard,
    )


async def album_playlist_no_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    context.user_data.pop(
        "album_playlist_state",
        None,
    )

    context.user_data.pop(
        "album_playlist_pending_name",
        None,
    )

    context.user_data.pop(
        "waiting_album_playlist_name",
        None,
    )

    await query.edit_message_text(
        "✅ No se creará ninguna playlist "
        "para este álbum."
    )

async def album_page_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    albums = context.user_data.get(
        "album_results",
        [],
    )

    artist = context.user_data.get(
        "album_artist",
        "",
    )

    if not albums:
        await query.edit_message_text(
            "⚠️ Esta búsqueda ha caducado. "
            "Usa /album de nuevo."
        )
        return

    page = int(
        query.data.split(":", 1)[1]
    )

    await query.edit_message_text(
        f"💿 {artist}\n\n"
        f"Selecciona un álbum:",
        reply_markup=build_album_keyboard(
            albums,
            page=page,
        ),
    )


async def album_select_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    logger.warning(
        f"[TELEGRAM] album callback: {query.data}"
    )

    try:
        album_id = query.data.split(":", 1)[1]

        albums = context.user_data.get("album_results", [])
        artist = context.user_data.get("album_artist")

        selected = next(
            (album for album in albums if album["id"] == album_id),
            None,
        )

        logger.warning(f"[TELEGRAM] albums guardados={len(albums)}")
        logger.warning(f"[TELEGRAM] selected={selected}")

        if selected is None:
            await query.edit_message_text(
                "No he podido encontrar ese álbum en memoria."
            )
            return

        context.user_data["selected_album"] = selected

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬇️ Descargar álbum",
                    callback_data=f"albumdl:{album_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancelar",
                    callback_data="albumcancel",
                )
            ],
        ])

        await query.edit_message_text(
            text=(
                f"🎵 {selected['title']}\n"
                f"👤 {artist}\n"
                f"📅 {selected.get('date') or 'Fecha desconocida'}\n\n"
                f"¿Qué quieres hacer?"
            ),
            reply_markup=keyboard,
        )

        logger.warning(
            f"[TELEGRAM] album seleccionado: {selected['title']}"
        )

    except Exception:
        logger.exception("[TELEGRAM] Error en album_select_callback")
        await query.edit_message_text(
            "Error seleccionando el álbum."
        )


async def album_cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    context.user_data.pop(
        "selected_album",
        None,
    )

    await query.edit_message_text(
        "❌ Descarga de álbum cancelada."
    )

def build_album_progress_text(
    total_tracks: int,
    started_at: datetime,
    downloaded_count: int,
    downloading_count: int,
    waiting_count: int,
    failed_count: int,
):
    lines = [
        "⏳ Descargando álbum...",
        "",
        f"🎵 {total_tracks} canciones",
        (
            f"🕒 Inicio: "
            f"{started_at.strftime('%H:%M')}"
        ),
        "",
        (
            f"💯 {downloaded_count} "
            f"descargadas"
        ),
        (
            f"🔄 {downloading_count} "
            f"descargando"
        ),
        (
            f"⬇️ {waiting_count} "
            f"en espera"
        ),
    ]

    if failed_count:
        lines.append(
            f"⚠️ {failed_count} con error"
        )

    lines.extend(
        [
            "",
            (
                f"📊 Progreso: "
                f"{downloaded_count + failed_count}/"
                f"{total_tracks}"
            ),
        ]
    )

    return "\n".join(lines)    

async def process_album_download(
    application,
    chat_id: int,
    artist: str,
    album: str,
    user_data=None,
    status_message_id: int | None = None,
    quality_mode: str = "best",
):
    started_at = datetime.now()

    try:
        # ----------------------------------------------
        # 1. CREAR TAREA DE ÁLBUM EN EL BACKEND
        # ----------------------------------------------

        result = await api_post(
            "/album-download-tasks",
            {
                "artist": artist,
                "album": album,
                "quality_mode": quality_mode,
            },
            timeout=30,
            retries=1,
        )

        if result.get("status") != "started":
            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "❌ No he podido iniciar "
                    "el procesamiento del álbum.\n\n"
                    f"💿 {album}\n"
                    f"👤 {artist}"
                ),
            )
            return

        task_id = result.get(
            "task_id"
        )

        if not task_id:
            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "❌ El backend no ha devuelto "
                    "un identificador para el álbum."
                ),
            )
            return

        logger.warning(
            f"[ALBUM TASK START] "
            f"task_id={task_id!r} | "
            f"artist={artist!r} | "
            f"album={album!r}"
        )

        last_progress_text = None

        # ----------------------------------------------
        # 2. CONSULTAR PROGRESO HASTA QUE TERMINE
        # ----------------------------------------------

        while True:
            await asyncio.sleep(4)

            try:
                task = await api_get(
                    f"/album-download-tasks/{task_id}"
                )

            except Exception:
                logger.exception(
                    f"[ALBUM TASK] "
                    f"Error consultando "
                    f"task_id={task_id}"
                )
                continue

            status = task.get(
                "status"
            )

            # ------------------------------------------
            # MusicBrainz todavía está obteniendo
            # las canciones del álbum.
            # ------------------------------------------

            if status == "analyzing":
                analyzing_text = (
                    "🔎 Procesando álbum...\n\n"
                    f"💿 {album}\n"
                    f"👤 {artist}\n\n"
                    "🎵 Obteniendo canciones "
                    "del álbum..."
                )

                if (
                    status_message_id is not None
                    and analyzing_text
                    != last_progress_text
                ):
                    try:
                        await application.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_message_id,
                            text=analyzing_text,
                        )

                        last_progress_text = (
                            analyzing_text
                        )

                    except Exception:
                        logger.exception(
                            "[ALBUM PROGRESS] "
                            "Error actualizando "
                            "fase de análisis"
                        )

                continue

            # ------------------------------------------
            # Fallo obteniendo/procesando el álbum.
            # ------------------------------------------

            if status == "failed":
                error = (
                    task.get("error")
                    or "Error desconocido"
                )

                error_text = (
                    "❌ Error procesando el álbum\n\n"
                    f"💿 {album}\n"
                    f"👤 {artist}\n\n"
                    f"{error}"
                )

                if status_message_id is not None:
                    try:
                        await application.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_message_id,
                            text=error_text,
                        )
                    except Exception:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text=error_text,
                        )
                else:
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=error_text,
                    )

                return

            if status == "not_found":
                text = (
                    "⚠️ No he encontrado canciones "
                    "para este álbum.\n\n"
                    f"💿 {album}\n"
                    f"👤 {artist}"
                )

                if status_message_id is not None:
                    try:
                        await application.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_message_id,
                            text=text,
                        )
                    except Exception:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                        )
                else:
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                    )

                return

            # ------------------------------------------
            # 3. PROGRESO REAL
            # ------------------------------------------

            total_tracks = int(
                task.get("tracks_found")
                or 0
            )

            downloaded_count = int(
                task.get("downloaded")
                or 0
            )

            downloading_count = int(
                task.get("downloading")
                or 0
            )

            waiting_count = int(
                task.get("waiting")
                or 0
            )

            failed_count = int(
                task.get("failed")
                or 0
            )

            finished_count = int(
                task.get("finished")
                or (
                    downloaded_count
                    + failed_count
                )
            )

            if total_tracks > 0:
                lines = [
                    "⏳ Descargando álbum...",
                    "",
                    f"🎵 {total_tracks} canciones",
                    (
                        f"🕒 Inicio: "
                        f"{started_at.strftime('%H:%M')}"
                    ),
                    "",
                    (
                        f"💯 {downloaded_count} "
                        f"descargadas"
                    ),
                    (
                        f"🔄 {downloading_count} "
                        f"descargando"
                    ),
                    (
                        f"⬇️ {waiting_count} "
                        f"en espera"
                    ),
                ]

                if failed_count:
                    lines.append(
                        f"⚠️ {failed_count} con error"
                    )

                lines.extend(
                    [
                        "",
                        (
                            f"📊 Progreso: "
                            f"{finished_count}/"
                            f"{total_tracks}"
                        ),
                    ]
                )

                progress_text = "\n".join(
                    lines
                )

                if (
                    status_message_id is not None
                    and progress_text
                    != last_progress_text
                ):
                    try:
                        await application.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=status_message_id,
                            text=progress_text,
                        )

                        last_progress_text = (
                            progress_text
                        )

                    except Exception:
                        logger.exception(
                            "[ALBUM PROGRESS] "
                            "Error actualizando "
                            "progreso"
                        )

            # ------------------------------------------
            # 4. TODAS LAS CANCIONES TERMINARON
            # ------------------------------------------

            if not task.get(
                "done",
                False,
            ):
                continue

            completed_tracks = (
                task.get(
                    "completed_tracks",
                    [],
                )
                or []
            )

            failed_tracks = (
                task.get(
                    "failed_tracks",
                    [],
                )
                or []
            )

            logger.warning(
                f"[ALBUM TASK DONE] "
                f"task_id={task_id!r} | "
                f"album={album!r} | "
                f"completed="
                f"{len(completed_tracks)} | "
                f"failed="
                f"{len(failed_tracks)}"
            )

            await finish_album_download(
                application=application,
                chat_id=chat_id,
                artist=artist,
                album=album,
                completed_tracks=completed_tracks,
                failed_tracks=failed_tracks,
                user_data=user_data,
            )

            return

    except Exception:
        logger.exception(
            f"[TELEGRAM] "
            f"Error procesando álbum "
            f"{artist} - {album}"
        )

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ Error procesando el álbum\n\n"
                f"💿 {album}\n"
                f"👤 {artist}"
            ),
        )


async def album_download_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    album_id = query.data.split(":", 1)[1]

    albums = context.user_data.get(
        "album_results",
        [],
    )

    artist = context.user_data.get(
        "album_artist",
    )

    selected = next(
        (
            album
            for album in albums
            if album["id"] == album_id
        ),
        None,
    )

    if selected is None or not artist:
        await query.edit_message_text(
            "⚠️ Esta selección ha caducado. "
            "Usa /album de nuevo."
        )
        return

    album = selected["title"]

    await query.edit_message_text(
        f"🔎 Procesando álbum...\n\n"
        f"💿 {album}\n"
        f"👤 {artist}\n\n"
        f"Buscando la mejor calidad disponible "
        f"para cada canción."
    )

    context.application.create_task(
        process_album_download(
            application=context.application,
            chat_id=update.effective_chat.id,
            artist=artist,
            album=album,
            user_data=context.user_data,
        quality_mode=context.user_data.get("album_quality_mode", "best"),
            status_message_id=(
                query.message.message_id
            ),
        )
    )

async def finish_album_download(
    application,
    chat_id: int,
    artist: str,
    album: str,
    completed_tracks: list[dict],
    failed_tracks: list[str],
    user_data=None,
):
    # Eliminar posibles duplicados de local_path.
    unique_tracks = []
    seen_paths = set()

    for item in completed_tracks:
        local_path = item.get(
            "local_path"
        )

        if not local_path:
            continue

        normalized = (
            local_path
            .replace("\\", "/")
            .casefold()
        )

        if normalized in seen_paths:
            continue

        seen_paths.add(
            normalized
        )

        unique_tracks.append(
            item
        )

    completed_tracks = (
        unique_tracks
    )

    total_duration = sum(
        int(item.get("length") or 0)
        for item in completed_tracks
    )

    total_size = sum(
        int(item.get("local_size") or 0)
        for item in completed_tracks
    )

    duration_text = (
        format_playlist_duration(
            total_duration
        )
    )

    size_text = format_file_size(
        total_size
    )    

    if failed_tracks:
        lines = [
            "⚠️ Álbum terminado con errores",
            "",
            f"💿 {album}",
            f"👤 {artist}",
            "",
            (
                f"✅ {len(completed_tracks)} "
                f"canciones completadas"
            ),
            (
                f"❌ {len(failed_tracks)} "
                f"canciones no completadas:"
            ),
        ]

        lines.extend(
            f"• {track}"
            for track in failed_tracks
        )

    else:
        lines = [
            "✅ Álbum completado",
            "",
            f"💿 {album}",
            f"👤 {artist}",
            "",
            (
                f"🎵 {len(completed_tracks)} "
                f"canciones completadas"
            ),
        ]

    if completed_tracks:
        lines.extend(
            [
                "",
                (
                    f"⏱ Duración: "
                    f"{duration_text}"
                ),
                (
                    f"💾 Tamaño: "
                    f"{size_text}"
                ),
            ]
        )        

    await application.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
    )

    # Si ninguna canción está disponible,
    # no tiene sentido crear un M3U.
    if not completed_tracks:
        return

    if user_data is None:
        logger.error(
            f"[ALBUM PLAYLIST] "
            f"user_data ausente | "
            f"artist={artist!r} | "
            f"album={album!r}"
        )
        return

    local_paths = [
        item["local_path"]
        for item in completed_tracks
        if item.get("local_path")
    ]

    user_data[
        "album_playlist_state"
    ] = {
        "artist": artist,
        "album": album,
        "local_paths": local_paths,
    }

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    short_button_text(
                        f"💿 {artist} - {album}"
                    ),
                    callback_data=(
                        "albumpl_name:album"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    short_button_text(
                        f"👤 {artist}"
                    ),
                    callback_data=(
                        "albumpl_name:artist"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "✏️ Otro nombre",
                    callback_data=(
                        "albumpl_custom"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ No",
                    callback_data=(
                        "albumpl_no"
                    ),
                )
            ],
        ]
    )

    await application.bot.send_message(
        chat_id=chat_id,
        text=(
            "¿Quieres guardar las canciones "
            "completadas en una playlist?"
        ),
        reply_markup=keyboard,
    )  

def album_playlist_quality_name(
    playlist_name: str,
    local_paths: list[str],
) -> str:
    lossless_count = 0
    mp3_count = 0

    for local_path in local_paths:
        normalized = (
            str(local_path)
            .strip()
            .casefold()
        )

        if normalized.endswith((".flac", ".wav", ".alac", ".aiff")):
            lossless_count += 1

        elif normalized.endswith(".mp3"):
            mp3_count += 1

    total = (
        lossless_count
        + mp3_count
    )

    if total == 0:
        return playlist_name

    is_lossless = (
        lossless_count / total
        > 0.5
    )

    if not is_lossless:
        return playlist_name

    # Evitar:
    # "Nombre - lossless - lossless"
    if playlist_name.casefold().endswith(
        " - lossless"
    ):
        return playlist_name

    return (
        f"{playlist_name} - lossless"
    )    

async def prepare_album_playlist(
    query,
    context,
    playlist_name: str,
):
    state = context.user_data.get(
        "album_playlist_state"
    )

    if not state:
        await query.edit_message_text(
            "⚠️ Esta selección ha caducado."
        )
        return

    local_paths = state.get(
        "local_paths",
        [],
    )

    if not local_paths:
        await query.edit_message_text(
            "⚠️ No hay canciones disponibles "
            "para añadir."
        )
        return

    playlist_name = (
        album_playlist_quality_name(
            playlist_name,
            local_paths,
        )
    )

    try:
        result = await api_get(
            "/manual-playlists"
        )

    except Exception:
        logger.exception(
            "[ALBUM PLAYLIST] "
            "Error consultando playlists"
        )

        await query.edit_message_text(
            "❌ No he podido consultar "
            "las playlists."
        )
        return

    existing = {
        item.get(
            "name",
            "",
        ).casefold()
        for item in result.get(
            "playlists",
            [],
        )
    }

    already_exists = (
        playlist_name.casefold()
        in existing
    )

    context.user_data[
        "album_playlist_pending_name"
    ] = playlist_name

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Sí",
                    callback_data=(
                        "albumpl_confirm_yes"
                    ),
                ),
                InlineKeyboardButton(
                    "❌ No",
                    callback_data=(
                        "albumpl_confirm_no"
                    ),
                ),
            ]
        ]
    )

    if already_exists:
        action_text = (
            "Se actualizará esta playlist "
            "con las canciones del álbum."
        )
    else:
        action_text = (
            "Se creará esta playlist "
            "con las canciones del álbum."
        )

    await query.edit_message_text(
        (
            "⚠️ ¿Estás seguro?\n\n"
            f"💿 {state.get('album', 'Álbum')}\n"
            f"👤 {state.get('artist', 'Desconocido')}\n"
            f"📋 {playlist_name}\n"
            f"🎵 {len(local_paths)} canciones\n\n"
            f"{action_text}"
        ),
        reply_markup=keyboard,
    )

async def save_album_playlist(
    query,
    context,
    playlist_name: str,
):
    state = context.user_data.get(
        "album_playlist_state"
    )

    if not state:
        await query.edit_message_text(
            "⚠️ Esta selección ha caducado."
        )
        return

    local_paths = state.get(
        "local_paths",
        [],
    )

    try:
        result = await api_post(
            "/manual-playlists/add-tracks",
            {
                "playlist": playlist_name,
                "local_paths": local_paths,
                "create_if_missing": True,
            },
        )

    except Exception:
        logger.exception(
            f"[ALBUM PLAYLIST] "
            f"Error guardando "
            f"{playlist_name!r}"
        )

        await query.edit_message_text(
            "❌ No he podido guardar "
            "la playlist."
        )
        return

    status = result.get(
        "status"
    )

    if status in (
        "updated",
        "unchanged",
    ):
        added = int(
            result.get(
                "added",
                0,
            )
            or 0
        )

        duplicates = int(
            result.get(
                "duplicates",
                0,
            )
            or 0
        )

        total_tracks = int(
            result.get(
                "track_count",
                0,
            )
            or 0
        )

        duration = (
            format_playlist_duration(
                result.get(
                    "duration_seconds"
                )
            )
        )

        size = format_file_size(
            int(
                result.get(
                    "size_bytes",
                    0,
                )
                or 0
            )
        )        

        if status == "updated":
            title = (
                "✅ Playlist actualizada"
            )
        else:
            title = (
                "ℹ️ La playlist ya contenía "
                "estas canciones"
            )

        await query.edit_message_text(
            (
                f"{title}\n\n"
                f"📋 {playlist_name}\n"
                f"➕ {added} canciones añadidas\n"
                f"♻️ {duplicates} ya estaban\n\n"
                f"🎶 Total: "
                f"{total_tracks} canciones\n"
                f"⏱ Duración: {duration}"
                f"💾 Tamaño: {size}"
            )
        )

        context.user_data.pop(
            "album_playlist_state",
            None,
        )

        context.user_data.pop(
            "album_playlist_pending_name",
            None,
        )

        context.user_data.pop(
            "waiting_album_playlist_name",
            None,
        )

        return    

    await query.edit_message_text(
        (
            "❌ No he podido guardar "
            "la playlist.\n\n"
            f"Estado: "
            f"{status or 'desconocido'}"
        )
    )          

async def watch_album_jobs(
    application,
    chat_id: int,
    artist: str,
    album: str,
    job_ids: list[str],
    completed_tracks: list[dict],
    failed_tracks: list[str],
    user_data=None,
    total_tracks: int = 0,
    started_at: datetime | None = None,
    status_message_id: int | None = None,
    initial_completed_count: int = 0,
    job_statuses=None,
):
    pending = set(
        job_ids
    )

    completed_count = (
        initial_completed_count
    )

    job_statuses = dict(
        job_statuses or {}
    )

    if started_at is None:
        started_at = datetime.now()

    last_progress_text = None    

    while pending:
        await asyncio.sleep(4)

        finished = set()

        for job_id in list(
            pending
        ):
            try:
                job = await api_get(
                    f"/jobs/{job_id}"
                )

            except Exception:
                logger.exception(
                    f"[TELEGRAM] "
                    f"Error consultando job "
                    f"{job_id}"
                )
                continue

            status = job.get(
                "status"
            )

            job_statuses[
                job_id
            ] = status            

            if status == "completed":
                finished.add(
                    job_id
                )

                completed_count += 1                

                local_path = job.get(
                    "local_path"
                )

                if local_path:
                    completed_tracks.append(
                        {
                            "artist": (
                                job.get("artist")
                                or artist
                            ),
                            "track": (
                                job.get("track")
                                or "Desconocida"
                            ),
                            "local_path": (
                                local_path
                            ),
                        }
                    )

            elif status in (
                "failed",
                "cancelled",
            ):
                finished.add(
                    job_id
                )

                failed_tracks.append(
                    job.get(
                        "track",
                        "Desconocida",
                    )
                )

        pending -= finished

        downloading_count = sum(
            1
            for job_id in pending
            if job_statuses.get(
                job_id
            ) == "downloading"
        )

        waiting_count = sum(
            1
            for job_id in pending
            if job_statuses.get(
                job_id
            ) in (
                "queued",
                "starting",
                "waiting_remote",
            )
        )

        progress_text = (
            build_album_progress_text(
                total_tracks=total_tracks,
                started_at=started_at,
                downloaded_count=(
                    completed_count
                ),
                downloading_count=(
                    downloading_count
                ),
                waiting_count=(
                    waiting_count
                ),
                failed_count=len(
                    failed_tracks
                ),
            )
        )

        if (
            status_message_id is not None
            and progress_text
            != last_progress_text
        ):
            try:
                await application.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=progress_text,
                )

                last_progress_text = (
                    progress_text
                )

            except Exception:
                logger.exception(
                    "[ALBUM PROGRESS] "
                    "Error actualizando progreso"
                )        

    await finish_album_download(
        application=application,
        chat_id=chat_id,
        artist=artist,
        album=album,
        completed_tracks=completed_tracks,
        failed_tracks=failed_tracks,
        user_data=user_data,
    )

async def handle_spotify_playlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
):
    message = await update.message.reply_text(
        "🎧 Analizando playlist de Spotify..."
    )

    try:
        result = await api_post(
            "/spotify-playlist",
            {
                "url": url,
            },
            timeout=60,
            retries=1,
        )

    except Exception:
        logger.exception(
            "[SPOTIFY] Error analizando playlist"
        )

        await message.edit_text(
            "⚠️ No he podido leer la playlist de Spotify."
        )
        return

    if result.get("status") != "ok":
        await message.edit_text(
            "⚠️ No he podido leer la playlist de Spotify."
        )
        return

    playlist = result.get(
        "playlist",
        {},
    )

    tracks = result.get(
        "tracks",
        [],
    )

    playlist_name = (
        playlist.get("name")
        or "Spotify Playlist"
    )

    context.user_data[
        "spotify_playlist"
    ] = result

    await message.edit_text(
        (
            "🎧 Playlist de Spotify detectada\n\n"
            f"📋 {playlist_name}\n"
            f"🎵 {len(tracks)} canciones\n\n"
            "¿Quieres descargarla?"
        ),
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "⬇️ Descargar playlist",
                        callback_data=(
                            "spotify_download"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancelar",
                        callback_data=(
                            "spotify_cancel"
                        ),
                    )
                ],
            ]
        ),
    )    


def extract_youtube_playlist_id(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        playlist_ids = params.get("list", [])

        if playlist_ids:
            return playlist_ids[0]

    except Exception:
        pass

    return None


async def get_youtube_playlist(playlist_id: str):
    def request():
        items = []
        page_token = None

        while True:
            params = {
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": 50,                
            }

            if page_token:
                params["pageToken"] = page_token

            response = requests.get(
                "https://www.googleapis.com/youtube/v3/playlistItems",
                params=params,
                headers={
                    "x-goog-api-key": YOUTUBE_API_KEY,
                },
                timeout=30,
            )

            if not response.ok:
                logger.error(
                    f"[YOUTUBE] API error | "
                    f"status={response.status_code} | "
                    f"playlist_id={playlist_id} | "
                    f"body={response.text}"
                )

            response.raise_for_status()

            data = response.json()

            for item in data.get("items", []):
                snippet = item.get("snippet", {})

                title = snippet.get("title")

                if not title:
                    continue

                if title in (
                    "Deleted video",
                    "Private video",
                ):
                    continue

                items.append(
                    {
                        "title": title,
                        "video_id": (
                            snippet
                            .get("resourceId", {})
                            .get("videoId")
                        ),
                        "channel": snippet.get(
                            "videoOwnerChannelTitle",
                            "",
                        ),
                    }
                )

            page_token = data.get("nextPageToken")

            if not page_token:
                break

        return items

    return await asyncio.to_thread(request)                


def get_youtube_playlist_title(playlist_id: str) -> str | None:
    if not YOUTUBE_API_KEY:
        return None

    response = requests.get(
        "https://www.googleapis.com/youtube/v3/playlists",
        params={
            "part": "snippet",
            "id": playlist_id,
        },
        headers={
            "x-goog-api-key": YOUTUBE_API_KEY,
        },
        timeout=30,
    )

    if not response.ok:
        logger.error(
            f"[YOUTUBE] playlist title error | "
            f"status={response.status_code} | "
            f"playlist_id={playlist_id} | "
            f"body={response.text}"
        )
        return None

    data = response.json()
    items = data.get("items", [])

    if not items:
        return None

    return (
        items[0]
        .get("snippet", {})
        .get("title")
    )


def clean_youtube_title(title: str) -> str:
    cleaned = title.strip()

    cleaned = (
        cleaned
        .replace("–", " - ")
        .replace("—", " - ")
    )

    noise_patterns = [
        r"\(\s*official\s+music\s+video\s*\)",
        r"\[\s*official\s+music\s+video\s*\]",
        r"\(\s*official\s+video\s*\)",
        r"\[\s*official\s+video\s*\]",
        r"\(\s*official\s+audio\s*\)",
        r"\[\s*official\s+audio\s*\]",
        r"\(\s*lyric\s+video\s*\)",
        r"\[\s*lyric\s+video\s*\]",
        r"\(\s*lyrics?\s*\)",
        r"\[\s*lyrics?\s*\]",
        r"\(\s*visuali[sz]er\s*\)",
        r"\[\s*visuali[sz]er\s*\]",
        r"\(\s*dumb\.video\s*\)",
        r"\[\s*dumb\.video\s*\]",
    ]

    for pattern in noise_patterns:
        cleaned = re.sub(
            pattern,
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned,
    )

    cleaned = re.sub(
        r"\s*-\s*",
        " - ",
        cleaned,
    )

    return cleaned.strip(" -")


def clean_youtube_channel(channel: str) -> str:
    channel = channel.strip()

    channel = re.sub(
        r"\s*-\s*Topic$",
        "",
        channel,
        flags=re.IGNORECASE,
    )

    channel = re.sub(
        r"\s+",
        " ",
        channel,
    )

    return channel.strip()


def guess_youtube_track(
    title: str,
    channel: str,
) -> tuple[str | None, str | None]:

    cleaned_title = clean_youtube_title(title)
    cleaned_channel = clean_youtube_channel(channel)

    parts = re.split(
        r"\s+[-–—]\s+",
        cleaned_title,
        maxsplit=1,
    )

    # Si el propio título contiene "artista - canción",
    # nos fiamos más del título que del canal.
    if len(parts) == 2:
        artist = parts[0].strip()
        track = parts[1].strip()

        if artist and track:
            return artist, track

    # Si YouTube solo proporciona el título,
    # usamos el canal como artista.
    if cleaned_channel and cleaned_title:
        return cleaned_channel, cleaned_title

    return None, None


async def analyze_youtube_playlist(
    application,
    chat_id: int,
    items: list[dict],
    user_data: dict,
    quality_mode: str = "best",
):
    resolved_tracks = []
    unresolved_tracks = []

    total = len(items)

    playlist_title = user_data.get(
        "youtube_playlist_title",
        "YouTube Playlist",
    )

    message = await application.bot.send_message(
        chat_id=chat_id,
        text=(
            "🔎 Analizando playlist...\n\n"
            f"0/{total} canciones"
        ),
    )

    # --------------------------------------------------
    # COLA IDENTIFICACIÓN → SOULSEEK
    # --------------------------------------------------

    track_queue = asyncio.Queue()

    # process_playlist_download empieza YA.
    #
    # Al principio la cola estará vacía.
    # Conforme identifiquemos canciones,
    # las iremos introduciendo.
    application.create_task(
    process_playlist_download(
        application=application,
        chat_id=chat_id,
        tracks=None,
        playlist=playlist_title,
        track_queue=track_queue,
        total_tracks=total,
        ask_final_name=True,
        user_data=user_data,
        quality_mode=quality_mode,
    )
)

    for index, item in enumerate(
        items,
        start=1,
    ):
        original_title = item["title"]

        cleaned_title = clean_youtube_title(
            original_title
        )

        channel = clean_youtube_channel(
            item.get("channel", "")
        )

        resolved_item = None

        # --------------------------------------------------
        # 1. RESOLUCIÓN LOCAL
        # --------------------------------------------------
        #
        # Si YouTube ya dice:
        #
        # Artista - Canción
        #
        # no preguntamos a MusicBrainz.
        # --------------------------------------------------

        # --------------------------------------------------
        # 1. RESOLUCIÓN DIRECTA DESDE YOUTUBE
        # --------------------------------------------------
        #
        # Prioridad:
        #
        #   "Artist - Track"
        #       → usamos el propio título.
        #
        #   channel + title
        #       → usamos channel como artista.
        #
        # MusicBrainz queda únicamente como fallback
        # cuando YouTube no nos proporciona información
        # suficiente.
        # --------------------------------------------------

        (
            guessed_artist,
            guessed_track,
        ) = guess_youtube_track(
            original_title,
            item.get(
                "channel",
                "",
            ),
        )

        if (
            guessed_artist
            and guessed_track
        ):
            resolved_item = {
                "original_title": (
                    original_title
                ),
                "cleaned_title": (
                    cleaned_title
                ),
                "video_id": item.get(
                    "video_id"
                ),
                "artist": (
                    guessed_artist
                ),
                "track": (
                    guessed_track
                ),
                "resolution": (
                    "youtube"
                ),
            }

            logger.warning(
                f"[YOUTUBE DIRECT] "
                f"title={original_title!r} | "
                f"channel={channel!r} | "
                f"artist={guessed_artist!r} | "
                f"track={guessed_track!r}"
            )

        # --------------------------------------------------
        # 2. MUSICBRAINZ SOLO COMO FALLBACK
        # --------------------------------------------------

        if resolved_item is None:

            if channel:
                resolve_query = (
                    f"{channel} - "
                    f"{cleaned_title}"
                )
            else:
                resolve_query = (
                    cleaned_title
                )

            logger.warning(
                f"[YOUTUBE MUSICBRAINZ FALLBACK] "
                f"title={original_title!r} | "
                f"channel={channel!r} | "
                f"query={resolve_query!r}"
            )

            try:
                result = await api_post(
                    "/resolve-track",
                    {
                        "query": (
                            resolve_query
                        ),
                    },
                    timeout=30,
                    retries=3,
                )

                logger.warning(
                    f"[YOUTUBE RESOLVE] "
                    f"query={resolve_query!r} | "
                    f"status="
                    f"{result.get('status')} | "
                    f"artist="
                    f"{result.get('artist')!r} | "
                    f"track="
                    f"{result.get('track')!r} | "
                    f"score="
                    f"{result.get('score')!r}"
                )

                if (
                    result.get("status")
                    == "resolved"
                ):
                    resolved_item = {
                        "original_title": (
                            original_title
                        ),
                        "cleaned_title": (
                            cleaned_title
                        ),
                        "video_id": (
                            item.get(
                                "video_id"
                            )
                        ),
                        "artist": (
                            result["artist"]
                        ),
                        "track": (
                            result["track"]
                        ),
                        "resolution": (
                            "musicbrainz"
                        ),
                    }

            except Exception:
                logger.exception(
                    f"[YOUTUBE] "
                    f"Error resolviendo: "
                    f"{original_title}"
                )

            # Solo esperamos cuando realmente
            # hemos usado MusicBrainz.
            await asyncio.sleep(1.2)

        # --------------------------------------------------
        # ENVIAR INMEDIATAMENTE A SOULSEEK
        # --------------------------------------------------

        if resolved_item is not None:

            resolved_tracks.append(
                resolved_item
            )

            await track_queue.put(
                resolved_item
            )

        else:
            unresolved_item = {
                "original_title": (
                    original_title
                ),
                "cleaned_title": (
                    cleaned_title
                ),
                "video_id": item.get(
                    "video_id"
                ),
            }

            unresolved_tracks.append(
                unresolved_item
            )

            # También lo enviamos al pipeline,
            # pero marcado como fallo previo.
            await track_queue.put(
                {
                    "artist": (
                        channel
                        or "YouTube"
                    ),
                    "track": original_title,
                    "_pre_error": (
                        "not_identified"
                    ),
                }
            )

        # --------------------------------------------------
        # PROGRESO DE IDENTIFICACIÓN
        # --------------------------------------------------

        if (
            index == 1
            or index % 5 == 0
            or index == total
        ):
            await message.edit_text(
                "🔎 Analizando playlist...\n\n"
                f"{index}/{total} canciones\n\n"
                f"✅ "
                f"{len(resolved_tracks)} "
                f"identificadas\n"
                f"⚠️ "
                f"{len(unresolved_tracks)} "
                f"sin identificar\n\n"
                "⬇️ Las canciones identificadas "
                "ya se están buscando y "
                "descargando."
            )

    # --------------------------------------------------
    # FIN DEL STREAM
    # --------------------------------------------------
    #
    # process_playlist_download tiene 2 consumidores.
    # Enviamos una señal de finalización a cada uno.
    # --------------------------------------------------

    await track_queue.put(None)
    await track_queue.put(None)

    user_data[
        "youtube_resolved"
    ] = resolved_tracks

    user_data[
        "youtube_unresolved"
    ] = unresolved_tracks

    lines = [
        "🎵 Playlist analizada",
        "",
        f"📋 {total} elementos",
        (
            f"✅ {len(resolved_tracks)} "
            f"identificados"
        ),
    ]

    if unresolved_tracks:
        lines.append(
            f"⚠️ "
            f"{len(unresolved_tracks)} "
            f"sin identificar"
        )

    lines.extend(
        [
            "",
            (
                "⬇️ La búsqueda y descarga "
                "ya está en curso."
            ),
        ]
    )

    await message.edit_text(
        "\n".join(lines)
    )

    logger.warning(
        f"[YOUTUBE] playlist analizada | "
        f"total={total} | "
        f"resolved="
        f"{len(resolved_tracks)} | "
        f"unresolved="
        f"{len(unresolved_tracks)}"
    )


async def youtube_analyze_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    logger.warning(
        f"[TELEGRAM] YouTube callback: {query.data}"
    )

    await query.answer()

    items = context.user_data.get(
        "youtube_playlist",
        [],
    )

    if not items:
        await query.edit_message_text(
            "⚠️ Esta playlist ha caducado. "
            "Pega la URL de nuevo."
        )
        return

    await query.edit_message_text(
        f"🎵 Playlist de YouTube\n\n"
        f"📋 {len(items)} elementos\n\n"
        f"Iniciando análisis..."
    )

    context.application.create_task(
        analyze_youtube_playlist(
            application=context.application,
            chat_id=update.effective_chat.id,
            items=items,
            user_data=context.user_data,
        quality_mode=context.user_data.get("youtube_quality_mode", "best"),
        )
    )


async def youtube_download_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    tracks = context.user_data.get(
        "youtube_resolved",
        [],
    )    

    playlist_title = context.user_data.get(
        "youtube_playlist_title",
        "YouTube Playlist",    
    )

    

    if not tracks:
        await query.edit_message_text(
            "⚠️ Esta playlist ha caducado. "
            "Pega la URL de nuevo."
        )
        return

    await query.edit_message_text(
        f"⬇️ Procesando playlist...\n\n"
        f"🎵 {len(tracks)} canciones"
    )

    context.application.create_task(
    process_playlist_download(
        application=context.application,
        chat_id=update.effective_chat.id,
        tracks=tracks,
        playlist=playlist_title,
        ask_final_name=True,
        user_data=context.user_data,
        quality_mode=context.user_data.get("youtube_quality_mode", "best"),
    )
)

async def process_playlist_download(
    application,
    chat_id: int,
    tracks,
    playlist: str,
    track_queue=None,
    total_tracks=None,
    ask_final_name: bool = False,
    user_data=None,
    quality_mode: str = "best",
):
    started_at = datetime.now()

    if total_tracks is not None:
        total = total_tracks
    else:
        total = len(tracks or [])

    queued = 0
    completed = 0
    active = 0
    failed = 0
    processed = 0

    # Cada job puede corresponder a una o varias
    # entradas de la playlist.
    job_tracks = {}

    completed_tracks = []
    failed_tracks = []

    # X búsquedas simultáneas como máximo.
    search_semaphore = asyncio.Semaphore(2)

    # Evita que dos tareas intenten actualizar
    # contadores/mensaje al mismo tiempo.
    progress_lock = asyncio.Lock()

    progress_message = (
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔎 Preparando playlist...\n\n"
                f"0/{total} canciones"
            ),
        )
    )

    producer_done = asyncio.Event()

    watcher_task = asyncio.create_task(
        watch_playlist_jobs(
            application=application,
            chat_id=chat_id,
            job_tracks=job_tracks,
            playlist=playlist,
            total_tracks=total,
            completed_tracks=completed_tracks,
            failed_tracks=failed_tracks,
            progress_message=progress_message,
            started_at=started_at,
            producer_done=producer_done,
            ask_final_name=ask_final_name,
            user_data=user_data,
        )
    )   

    async def process_track(
        item: dict,
    ):
        nonlocal queued
        nonlocal completed
        nonlocal active
        nonlocal failed
        nonlocal processed

        if item.get("_pre_error"):
            artist = item.get(
                "artist",
                "YouTube",
            )

            track = item.get(
                "track",
                "Desconocida",
            )

            async with progress_lock:
                failed += 1
                processed += 1

                failed_tracks.append(
                    {
                        "artist": artist,
                        "track": track,
                        "status": item["_pre_error"],
                        "error": item["_pre_error"],
                    }
                )

            return

        artist = item["artist"]
        track = item["track"]

        result = None
        error_info = None

        try:
            async with search_semaphore:
                result = await api_post(
                    "/search-and-download",
                    {
                        "artist": artist,
                        "track": track,
                        "playlist": playlist,
                        "quality_mode": quality_mode,
                    },
                    timeout=180,
                    retries=1,
                )

        except Exception as error:
            logger.exception(
                f"[PLAYLIST] Error procesando "
                f"{artist} - {track}"
            )

            error_info = {
                "artist": artist,
                "track": track,
                "status": "communication_error",
                "error": str(error),
            }

        async with progress_lock:
            if error_info is not None:
                failed += 1
                failed_tracks.append(
                    error_info
                )

            else:
                status = result.get(
                    "status"
                )

                job_id = result.get(
                    "job_id"
                )

                if status == "completed":
                    completed += 1

                    download = result.get(
                        "download",
                        {},
                    )

                    completed_tracks.append(
                        {
                            "artist": artist,
                            "track": track,
                            "new_download": False,
                            "length": (
                                download.get(
                                    "length"
                                )
                                or item.get(
                                    "length"
                                )
                            ),
                            "extension": (
                                download.get(
                                    "extension"
                                )
                            ),
                            "local_path": (
                                download.get(
                                    "local_path"
                                )
                            ),
                            "local_size": (
                                download.get(
                                    "local_size"
                                )
                            ),
                        }
                    )

                elif status == "queued":
                    if result.get(
                        "already_exists",
                        False,
                    ):
                        active += 1
                    else:
                        queued += 1

                    if job_id:
                        job_tracks.setdefault(
                            job_id,
                            [],
                        ).append(
                            {
                                "artist": artist,
                                "track": track,
                                "new_download": (
                                    not result.get(
                                        "already_exists",
                                        False,
                                    )
                                ),
                            }
                        )

                    else:
                        failed += 1

                        failed_tracks.append(
                            {
                                "artist": artist,
                                "track": track,
                                "status": (
                                    "queued_without_job_id"
                                ),
                                "error": (
                                    "queued_without_job_id"
                                ),
                            }
                        )

                elif status in (
                    "starting",
                    "waiting_remote",
                    "downloading",
                ):
                    active += 1

                    if job_id:
                        job_tracks.setdefault(
                            job_id,
                            [],
                        ).append(
                            {
                                "artist": artist,
                                "track": track,
                                "new_download": False,
                            }
                        )

                    else:
                        failed += 1

                        failed_tracks.append(
                            {
                                "artist": artist,
                                "track": track,
                                "status": status,
                                "error": (
                                    "active_without_job_id"
                                ),
                            }
                        )

                else:
                    failed += 1

                    error_value = (
                        result.get("error")
                        or status
                        or "desconocido"
                    )

                    failed_tracks.append(
                        {
                            "artist": artist,
                            "track": track,
                            "status": (
                                status
                                or "desconocido"
                            ),
                            "error": error_value,
                        }
                    )

                    logger.warning(
                        f"[PLAYLIST DOWNLOAD FAILED] "
                        f"artist={artist!r} | "
                        f"track={track!r} | "
                        f"status={status!r} | "
                        f"result={result!r}"
                    )

            processed += 1

    if track_queue is None:

        # Modo normal.
        # Spotify y llamadas que ya entregan
        # la lista completa.
        tasks = [
            asyncio.create_task(
                process_track(item)
            )
            for item in (tracks or [])
        ]

        await asyncio.gather(
            *tasks
        )

    else:

        # Modo streaming.
        # YouTube introduce canciones
        # conforme las identifica.
        async def stream_worker():
            while True:
                item = await track_queue.get()

                try:
                    if item is None:
                        return

                    await process_track(
                        item
                    )

                finally:
                    track_queue.task_done()

        stream_workers = [
            asyncio.create_task(
                stream_worker()
            )
            for _ in range(2)
        ]

        await asyncio.gather(
            *stream_workers
        )

    producer_done.set()

    await watcher_task


async def watch_playlist_jobs(
    application,
    chat_id: int,
    job_tracks: dict,
    playlist: str,
    total_tracks: int,
    completed_tracks: list[dict],
    failed_tracks: list[dict],
    progress_message,
    started_at: datetime,
    producer_done: asyncio.Event,
    ask_final_name: bool = False,
    user_data=None,
):
    pending = set()
    finished_jobs = set()

    logger.warning(
        f"[PLAYLIST WATCH START] "
        f"playlist={playlist!r} | "
        f"total={total_tracks}"
    )

    last_progress_text = (
        progress_message.text
        or ""
    )

    while True:
        await asyncio.sleep(4)

        # ---------------------------------------------
        # Incorporar nuevos jobs creados mientras
        # YouTube sigue identificando canciones.
        # ---------------------------------------------

        new_job_ids = (
            set(job_tracks.keys())
            - finished_jobs
        )

        pending.update(
            new_job_ids
        )

        finished = set()
        downloading_count = 0
        waiting_count = 0

        for job_id in list(pending):
            try:
                job = await api_get(
                    f"/jobs/{job_id}"
                )

            except Exception:
                logger.exception(
                    f"[PLAYLIST] "
                    f"Error consultando job "
                    f"{job_id}"
                )
                continue

            status = job.get(
                "status"
            )

            if status == "downloading":
                downloading_count += 1

            elif status in (
                "queued",
                "starting",
                "waiting_remote",
            ):
                waiting_count += 1

            requested_tracks = (
                job_tracks.get(
                    job_id,
                    [],
                )
            )

            if status == "completed":
                finished.add(
                    job_id
                )

                for requested in requested_tracks:
                    completed_tracks.append(
                        {
                            "artist": (
                                requested["artist"]
                            ),
                            "track": (
                                requested["track"]
                            ),
                            "new_download": (
                                requested.get(
                                    "new_download",
                                    False,
                                )
                            ),
                            "length": (
                                job.get("length")
                            ),
                            "extension": (
                                job.get("extension")
                            ),
                            "local_path": (
                                job.get("local_path")
                            ),
                            "local_size": (
                                job.get("local_size")
                            ),
                        }
                    )

            elif status in (
                "failed",
                "cancelled",
            ):
                finished.add(
                    job_id
                )

                error_value = (
                    job.get("error")
                    or status
                )

                for requested in requested_tracks:
                    failed_tracks.append(
                        {
                            "artist": (
                                requested["artist"]
                            ),
                            "track": (
                                requested["track"]
                            ),
                            "status": status,
                            "error": error_value,
                        }
                    )

        pending -= finished
        finished_jobs.update(
            finished
        )

        downloaded_count = len(
            completed_tracks
        )

        failed_count = len(
            failed_tracks
        )

        finished_count = (
            downloaded_count
            + failed_count
        )

        lines = [
            "⏳ Descargando playlist...",
            "",
            f"📁 {playlist}",
            f"🎵 {total_tracks} canciones",
            (
                f"🕒 Inicio: "
                f"{started_at.strftime('%H:%M')}"
            ),
            "",
            (
                f"💯 {downloaded_count} "
                f"descargadas"
            ),
            (
                f"🔄 {downloading_count} "
                f"descargando"
            ),
            (
                f"⬇️ {waiting_count} "
                f"en espera"
            ),
        ]

        if failed_count:
            lines.append(
                f"⚠️ {failed_count} con error"
            )

        lines.extend(
            [
                "",
                (
                    f"📊 Progreso: "
                    f"{finished_count}/"
                    f"{total_tracks}"
                ),
            ]
        )

        new_progress_text = "\n".join(
            lines
        )

        if (
            new_progress_text
            != last_progress_text
        ):
            try:
                await progress_message.edit_text(
                    new_progress_text
                )

                last_progress_text = (
                    new_progress_text
                )

            except Exception:
                logger.exception(
                    f"[PLAYLIST PROGRESS ERROR] "
                    f"playlist={playlist!r}"
                )

        # ---------------------------------------------
        # Solo terminamos cuando:
        #
        # 1. YouTube/Spotify ya no producirá más jobs.
        # 2. No queda ningún job pendiente.
        # ---------------------------------------------

        if producer_done.is_set():
            remaining_jobs = (
                set(job_tracks.keys())
                - finished_jobs
            )

            pending.update(
                remaining_jobs
            )

            if not pending:
                break

    # =================================================
    # TODOS LOS TRACKS PRODUCIDOS Y TODOS LOS JOBS
    # TERMINADOS.
    # =================================================

    downloaded_count = len(
        completed_tracks
    )

    new_downloaded_count = sum(
        1
        for item in completed_tracks
        if item.get(
            "new_download",
            False,
        )
    )

    already_existing_count = (
        downloaded_count
        - new_downloaded_count
    )

    failed_count = len(
        failed_tracks
    )

    total_duration = sum(
        int(item.get("length") or 0)
        for item in completed_tracks
    )

    total_size = sum(
        int(item.get("local_size") or 0)
        for item in completed_tracks
    )

    size_text = format_file_size(
        total_size
    )

    destination = None
    final_track_count = None
    final_duration_seconds = None
    final_size_bytes = None

    if (
        downloaded_count > 0
        and not ask_final_name
    ):
        try:
            finalize_result = await api_post(
                "/finalize-playlist",
                {
                    "playlist": playlist,
                },
                timeout=30,
                retries=1,
            )

            logger.warning(
                f"[PLAYLIST FINALIZE] "
                f"playlist={playlist!r} | "
                f"result={finalize_result!r}"
            )

            if (
                finalize_result.get(
                    "status"
                )
                == "completed"
            ):
                destination = (
                    finalize_result.get(
                        "destination"
                    )
                )

                final_track_count = (
                    finalize_result.get(
                        "track_count"
                    )
                )

                final_duration_seconds = (
                    finalize_result.get(
                        "duration_seconds"
                    )
                )

                final_size_bytes = (
                    finalize_result.get(
                        "size_bytes"
                    )
                )                

        except Exception:
            logger.exception(
                f"[PLAYLIST] "
                f"Error finalizando "
                f"playlist {playlist!r}"
            )

            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ Las descargas han "
                    "terminado, pero no se ha "
                    "podido organizar la "
                    "playlist.\n\n"
                    f"📁 {playlist}"
                ),
            )
            return

    location = format_playlist_location(
        destination
    )

    duration_text = format_total_duration(
        total_duration
    )

    elapsed_text = format_elapsed_time(
        started_at
    )

    if ask_final_name:
        lines = [
            (
                "✅ Descarga de playlist completada"
                if downloaded_count > 0
                else "⚠️ Playlist no descargada"
            ),
            "",
            f"📄 Archivo importado: {playlist}",
        ]

    else:
        lines = [
            (
                "✅ Playlist completada"
                if downloaded_count > 0
                else "⚠️ Playlist no descargada"
            ),
            "",
            f"📁 {playlist}",
        ]

    if destination:
        lines.append(
            f"📍 {location}"
        )

    lines.append(
        (
            f"🕒 Inicio: "
            f"{started_at.strftime('%H:%M')}"
        )
    )

    lines.append(
        (
            f"⏱️ Tiempo transcurrido: "
            f"{elapsed_text}"
        )
    )

    lines.append(
        (
            f"🎶 Duración Playlist: "
            f"{duration_text}"
        )
    )

    lines.append(
        (
            f"💾 Tamaño Playlist: "
            f"{size_text}"
        )
    )

    lines.append("")

    lines.append(
        (
            f"🆕 {new_downloaded_count} "
            f"descargadas esta vez"
        )
    )

    lines.append(
        (
            f"♻️ {already_existing_count} "
            f"ya estaban descargadas"
        )
    )

    if failed_count:
        lines.append(
            f"❌ {failed_count} con error"
        )

    if (
        final_track_count is not None
        and final_duration_seconds
        is not None
    ):
        lines.append("")

        lines.append(
            (
                f"🎶 Total: "
                f"{int(final_track_count)} "
                f"canciones"
            )
        )

        lines.append(
            (
                f"⏱ Duración total: "
                f"{format_playlist_duration(
                    final_duration_seconds
                )}"
            )
        )

        if final_size_bytes is not None:
            lines.append(
                (
                    f"💾 Tamaño total: "
                    f"{format_file_size(
                        int(final_size_bytes)
                    )}"
                )
            )        

    lines.append("")

    if (
        downloaded_count
        == total_tracks
        and not failed_tracks
    ):
        lines.append(
            "🎵 Todas las canciones "
            "descargadas"
        )

    else:
        lines.append(
            f"🎵 {downloaded_count}/"
            f"{total_tracks} canciones "
            f"descargadas"
        )

        if failed_tracks:
            lines.append("")
            lines.append(
                f"⚠️ {len(failed_tracks)} "
                f"canciones no descargadas"
            )

    # Mensaje NUEVO para generar notificación.
    await application.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
    )

    # Lista completa de fallos.
    if failed_tracks:
        failed_lines = [
            (
                f"⚠️ Canciones no descargadas "
                f"({len(failed_tracks)})"
            ),
            "",
        ]

        for item in failed_tracks:
            artist = item.get(
                "artist",
                "Desconocido",
            )

            track = item.get(
                "track",
                "Desconocida",
            )

            error = (
                item.get("error")
                or item.get("status")
                or "desconocido"
            )

            failed_lines.append(
                f"• {artist} - {track} "
                f"[{error}]"
            )

        current_message = ""

        for line in failed_lines:
            new_line = line + "\n"

            if (
                len(current_message)
                + len(new_line)
                > 3500
            ):
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=current_message.rstrip(),
                )

                current_message = (
                    "⚠️ Canciones no descargadas "
                    "(continuación)\n\n"
                )

            current_message += new_line

        if current_message.strip():
            await application.bot.send_message(
                chat_id=chat_id,
                text=current_message.rstrip(),
            )   

    if (
        ask_final_name
        and downloaded_count > 0
    ):
        if user_data is None:
            logger.error(
                f"[PLAYLIST FINAL NAME] "
                f"user_data ausente | "
                f"playlist={playlist!r}"
            )

            await application.bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ La playlist está descargada, "
                    "pero no puedo solicitar "
                    "el nombre final."
                ),
            )
            return

        final_token = secrets.token_hex(4)

        final_states = user_data.setdefault(
            "playlist_final_states",
            {},
        )

        final_states[
            final_token
        ] = {
            "playlist": playlist,
            "suggested_name": playlist,
            "downloaded_count": (
                downloaded_count
            ),
            "new_downloaded_count": (
                new_downloaded_count
            ),
            "already_existing_count": (
                already_existing_count
            ),
            "failed_count": (
                failed_count
            ),
        }

        button_name = short_button_text(
            f'✅ Usar "{playlist}"'
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        button_name,
                        callback_data=(
                            f"plfinal_use:{final_token}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✏️ Otro nombre",
                        callback_data=(
                            f"plfinal_custom:{final_token}"
                        ),
                    )
                ],
            ]
        )

        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "💾 ¿Cómo quieres guardar "
                "esta playlist?"
            ),
            reply_markup=keyboard,
        )

        return

    user_data[
        "waiting_m3u_final_name"
    ] = {
        "playlist": playlist,
        "downloaded_count": downloaded_count,
    }

    await application.bot.send_message(
        chat_id=chat_id,
        text=(
            "✏️ ¿Con qué nombre quieres "
            "guardar esta playlist?\n\n"
            "Escribe el nombre que quieras."
        ),
    )            


async def get_youtube_playlist_title(
    playlist_id: str,
) -> str | None:
    if not YOUTUBE_API_KEY:
        return None

    def request_playlist_title():
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/playlists",
            params={
                "part": "snippet",
                "id": playlist_id,
            },
            headers={
                "x-goog-api-key": YOUTUBE_API_KEY,
            },
            timeout=30,
        )

        if not response.ok:
            logger.error(
                f"[YOUTUBE] playlist title error | "
                f"status={response.status_code} | "
                f"playlist_id={playlist_id} | "
                f"body={response.text}"
            )
            return None

        data = response.json()
        items = data.get("items", [])

        if not items:
            return None

        return (
            items[0]
            .get("snippet", {})
            .get("title")
        )

    return await asyncio.to_thread(
        request_playlist_title
    )

async def download_selected_track(
    query,
    context,
    selected: dict,
    chat_id: int,
):
    artist = selected["artist"]
    track = selected["track"]

    duration = format_duration(
        selected.get("length")
    )

    await query.edit_message_text(
        (
            "🎵 Canción seleccionada\n\n"
            f"👤 {artist}\n"
            f"🎵 {track}\n"
            f"⏱️ {duration}\n\n"
            "🔎 Buscando la mejor calidad..."
        )
    )

    try:
        result = await api_post(
            "/search-and-download",
            {
                "artist": artist,
                "track": track,
                "quality_mode": selected.get("quality_mode", "best"),
            },
            timeout=180,
            retries=1,
        )

    except Exception:
        logger.exception(
            f"[TRACK DOWNLOAD] "
            f"{artist} - {track}"
        )

        await context.application.bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ Ha ocurrido un error "
                "durante la búsqueda."
            ),
        )
        return

    status = result.get("status")

    if status == "completed":
        download = result.get(
            "download",
            {},
        )

        extension = (
            download.get("extension")
            or selected.get("extension")
            or ""
        ).upper()

        length = download.get(
            "length"
        )

        if length is None:
            length = selected.get(
                "length"
            )

        real_duration = (
            format_duration(
                length
            )
        )

        local_path = download.get(
            "local_path"
        )

        local_size = int(
            download.get(
                "local_size",
                0,
            )
            or 0
        )

        size_text = (
            format_file_size(
                local_size
            )
            if local_size > 0
            else "Desconocido"
        )        

        location = (
            format_local_location(
                local_path
            )
        )

        job_id = result.get(
            "job_id"
        )

        if not extension:
            extension = "Desconocido"

        playlists = (
            await get_track_playlist_memberships(
                local_path
            )
        )

        lines = [
            "✅ Ya estaba descargada",
            "",
            f"🎵 {track}",
            f"👤 {artist}",
            f"💿 {extension}",
            f"⏱️ {real_duration}",
            f"💾 Tamaño: {size_text}",
            f"📁 {location}",
            "",
        ]

        if playlists is None:
            lines.append(
                "📋 No he podido comprobar "
                "sus playlists."
            )

        elif playlists:
            lines.append(
                "📋 Está incluida en:"
            )

            lines.extend(
                f"• {name}"
                for name in playlists
            )

        else:
            lines.append(
                "📋 No está incluida "
                "en ninguna playlist."
            )

        keyboard = None

        if (
            local_path
            and job_id
        ):
            lines.extend(
                [
                    "",
                    (
                        "¿Quieres añadirla "
                        "a otra playlist?"
                        if playlists
                        else
                        "¿Quieres añadirla "
                        "a una playlist?"
                    ),
                ]
            )

            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📋 Playlist existente",
                            callback_data=(
                                f"trackpl_existing:"
                                f"{job_id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "➕ Crear nueva",
                            callback_data=(
                                f"trackpl_new:"
                                f"{job_id}"
                            ),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ No",
                            callback_data=(
                                f"trackpl_cancel:"
                                f"{job_id}"
                            ),
                        )
                    ],
                ]
            )

        await context.application.bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            reply_markup=keyboard,
        )

        return    

    if status == "queued":
        job_id = result.get("job_id")
        position = result.get("position")

        lines = [
            "⬇️ Añadida a la cola",
            "",
            f"🎵 {track}",
            f"👤 {artist}",
            f"⏱️ {duration}",
        ]

        if position is not None:
            lines.append(
                f"📋 Posición: {position}"
            )

        await context.application.bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
        )

        if job_id:
            context.application.create_task(
                watch_job(
                    application=context.application,
                    chat_id=chat_id,
                    job_id=job_id,
                    artist=artist,
                    track=track,
                )
            )
        else:
            logger.error(
                f"[TRACK DOWNLOAD] "
                f"queued sin job_id | "
                f"artist={artist!r} | "
                f"track={track!r}"
            )

        return

    await context.application.bot.send_message(
        chat_id=chat_id,
        text=(
            "⚠️ No he encontrado una versión "
            "adecuada para descargar.\n\n"
            f"🎵 {track}\n"
            f"👤 {artist}"
        ),
    )

async def explore_track_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        index = int(
            query.data.split(":", 1)[1]
        )
    except (ValueError, IndexError):
        return

    tracks = context.user_data.get(
        "explore_tracks",
        [],
    )

    if (
        index < 0
        or index >= len(tracks)
    ):
        await query.edit_message_text(
            "⚠️ Esta búsqueda ha caducado."
        )
        return

    selected = tracks[index]

    await download_selected_track(
        query=query,
        context=context,
        selected=selected,
        chat_id=update.effective_chat.id,
    )

def format_duration(
    length: int | None,
) -> str:
    if not length:
        return "--:--"

    length = int(length)

    minutes = length // 60
    seconds = length % 60

    return f"{minutes}:{seconds:02d}"


def short_button_text(
    text: str,
    max_length: int = 58,
) -> str:
    if len(text) <= max_length:
        return text

    return text[:max_length - 1] + "…"

async def show_artist_tracks_page(
    query,
    context,
    page: int,
):
    tracks = context.user_data.get(
        "artist_explore_tracks",
        [],
    )

    artist_name = context.user_data.get(
        "artist_explore_name",
        "Artista",
    )

    if not tracks:
        await query.edit_message_text(
            "⚠️ No hay canciones disponibles."
        )
        return

    total_tracks = len(tracks)

    total_pages = (
        total_tracks
        + ARTIST_TRACKS_PER_PAGE
        - 1
    ) // ARTIST_TRACKS_PER_PAGE

    page = max(
        0,
        min(
            page,
            total_pages - 1,
        ),
    )

    start = (
        page
        * ARTIST_TRACKS_PER_PAGE
    )

    end = (
        start
        + ARTIST_TRACKS_PER_PAGE
    )

    page_tracks = tracks[
        start:end
    ]

    keyboard = []

    for local_index, track in enumerate(
        page_tracks
    ):
        real_index = (
            start + local_index
        )

        duration = format_duration(
            track.get("length")
        )

        label = short_button_text(
            f"🎵 {track['track']} · "
            f"{duration}"
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=(
                        f"artist_track:"
                        f"{real_index}"
                    ),
                )
            ]
        )

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"artist_page:"
                    f"{page - 1}"
                ),
            )
        )

    navigation.append(
        InlineKeyboardButton(
            f"{page + 1}/{total_pages}",
            callback_data="artist_page_current",
        )
    )

    if page < total_pages - 1:
        navigation.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"artist_page:"
                    f"{page + 1}"
                ),
            )
        )

    keyboard.append(
        navigation
    )

    await query.edit_message_text(
        (
            f"🎤 {artist_name}\n\n"
            f"🎵 {total_tracks} canciones encontradas\n"
            f"Página {page + 1}/{total_pages}\n\n"
            "Selecciona una canción:"
        ),
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )    

async def explore_artist_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    try:
        index = int(
            query.data.split(":", 1)[1]
        )
    except (ValueError, IndexError):
        return

    artists = context.user_data.get(
        "explore_artists",
        [],
    )

    if (
        index < 0
        or index >= len(artists)
    ):
        await query.edit_message_text(
            "⚠️ Esta búsqueda ha caducado."
        )
        return

    artist = artists[index]

    await query.edit_message_text(
        f"🔎 Buscando canciones de "
        f"{artist['name']}..."
    )

    try:
        result = await api_post(
            "/artist-tracks",
            {
                "artist_id": artist["id"],
            },
            timeout=60,
            retries=3,
        )

    except Exception:
        logger.exception(
            f"[EXPLORE ARTIST] "
            f"{artist['name']}"
        )

        await query.edit_message_text(
            "⚠️ No he podido buscar "
            "las canciones del artista."
        )
        return

    tracks = result.get("tracks", [])

    if not tracks:
        await query.edit_message_text(
            f"⚠️ No he encontrado canciones "
            f"de {artist['name']}."
        )
        return

    for item in tracks:
        item["quality_mode"] = artist.get("quality_mode", "best")

    context.user_data[
        "artist_explore_tracks"
    ] = tracks

    context.user_data[
        "artist_explore_name"
    ] = artist["name"]

    await show_artist_tracks_page(
        query,
        context,
        page=0,
    )

async def artist_track_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        index = int(
            query.data.split(":", 1)[1]
        )
    except (ValueError, IndexError):
        return

    tracks = context.user_data.get(
        "artist_explore_tracks",
        [],
    )

    if (
        index < 0
        or index >= len(tracks)
    ):
        await query.edit_message_text(
            "⚠️ Esta búsqueda ha caducado."
        )
        return

    selected = tracks[index]

    await download_selected_track(
        query=query,
        context=context,
        selected=selected,
        chat_id=update.effective_chat.id,
    )                       

async def artist_page_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if query.data == "artist_page_current":
        return

    try:
        page = int(
            query.data.split(
                ":",
                1,
            )[1]
        )
    except (
        ValueError,
        IndexError,
    ):
        return

    await show_artist_tracks_page(
        query,
        context,
        page=page,
    )

async def explore_page_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    value = query.data.split(
        ":",
        1,
    )[1]

    if value == "current":
        return

    try:
        page = int(value)
    except ValueError:
        return

    await show_explore_results_page(
        query.message,
        context,
        page=page,
    )   

async def m3u_playlist_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.message

    if not message or not message.document:
        return

    await ask_search_quality(update, context, "m3u")


async def run_m3u_search(update, context, quality_mode):
    message = update.message
    document = message.document

    filename = (
        document.file_name
        or "playlist.m3u"
    )

    playlist_name = re.sub(
        r"\.(?:m3u8?|M3U8?)$",
        "",
        filename,
    ).strip()

    if not playlist_name:
        playlist_name = "Imported Playlist"

    await message.reply_text(
        f"📄 Procesando playlist:\n"
        f"{playlist_name}"
    )

    try:
        telegram_file = (
            await context.bot.get_file(
                document.file_id
            )
        )

        file_bytes = (
            await telegram_file.download_as_bytearray()
        )

        # UTF-8 primero; latin-1 como fallback
        # para playlists antiguas.
        try:
            content = bytes(
                file_bytes
            ).decode(
                "utf-8-sig"
            )

        except UnicodeDecodeError:
            content = bytes(
                file_bytes
            ).decode(
                "latin-1"
            )

        tracks = parse_m3u_playlist(
            content
        )

        if not tracks:
            await message.reply_text(
                "❌ No he podido identificar "
                "ninguna canción en ese .m3u."
            )
            return

        logger.warning(
            f"[M3U IMPORT] "
            f"playlist={playlist_name!r} | "
            f"tracks={len(tracks)}"
        )

        await message.reply_text(
            f"🎵 {len(tracks)} canciones identificadas.\n"
            f"⬇️ Iniciando búsqueda y descarga..."
        )

        application = (
            context.application
        )

        application.create_task(
            process_playlist_download(
                application=application,
                chat_id=message.chat_id,
                tracks=tracks,
                playlist=playlist_name,
                ask_final_name=True,
                user_data=context.user_data,
                quality_mode=quality_mode,
            )
        )

    except Exception:
        logger.exception(
            f"[M3U IMPORT ERROR] "
            f"filename={filename!r}"
        )

        await message.reply_text(
            "❌ Error procesando el archivo .m3u."
        ) 

async def show_track_playlist_page(
    query,
    context,
    page: int,
):
    playlists = context.user_data.get(
        "manual_playlists",
        [],
    )

    job_id = context.user_data.get(
        "playlist_track_job_id"
    )

    if not job_id:
        await query.edit_message_text(
            "⚠️ La selección ha caducado."
        )
        return

    total = len(playlists)

    if total == 0:
        await query.edit_message_text(
            "⚠️ No hay playlists disponibles."
        )
        return

    total_pages = max(
        1,
        (
            total
            + MANUAL_PLAYLISTS_PER_PAGE
            - 1
        )
        // MANUAL_PLAYLISTS_PER_PAGE,
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1,
        ),
    )

    start = (
        page
        * MANUAL_PLAYLISTS_PER_PAGE
    )

    end = (
        start
        + MANUAL_PLAYLISTS_PER_PAGE
    )

    page_playlists = playlists[
        start:end
    ]

    keyboard = []

    for local_index, playlist in enumerate(
        page_playlists
    ):
        real_index = (
            start + local_index
        )

        name = playlist.get(
            "name",
            "Playlist",
        )

        keyboard.append(
            [
                InlineKeyboardButton(
                    short_button_text(
                        f"🎵 {name}"
                    ),
                    callback_data=(
                        f"trackpl_select:"
                        f"{real_index}"
                    ),
                )
            ]
        )

    if total_pages > 1:
        navigation = []

        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    "⬅️",
                    callback_data=(
                        f"trackpl_page:"
                        f"{page - 1}"
                    ),
                )
            )

        navigation.append(
            InlineKeyboardButton(
                f"{page + 1}/{total_pages}",
                callback_data=(
                    "trackpl_page:current"
                ),
            )
        )

        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    "➡️",
                    callback_data=(
                        f"trackpl_page:"
                        f"{page + 1}"
                    ),
                )
            )

        keyboard.append(
            navigation
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "❌ Cancelar",
                callback_data=(
                    "trackpl_cancel:"
                    f"{job_id}"
                ),
            )
        ]
    )

    await query.edit_message_text(
        (
            "📋 Selecciona la playlist "
            "a la que quieres añadir "
            "la canción:"
        ),
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )       

def format_playlist_duration(
    total_seconds: int | None,
) -> str:
    try:
        total_seconds = int(
            total_seconds or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        total_seconds = 0

    if total_seconds <= 0:
        return "Desconocida"

    hours, remainder = divmod(
        total_seconds,
        3600,
    )

    minutes = remainder // 60

    if hours:
        return (
            f"{hours} h "
            f"{minutes} min"
        )

    return f"{minutes} min"       

async def track_playlist_existing_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        job_id = query.data.split(
            ":",
            1,
        )[1]

    except IndexError:
        return

    context.user_data[
        "playlist_track_job_id"
    ] = job_id

    try:
        result = await api_get(
            "/manual-playlists"
        )

    except Exception:
        logger.exception(
            "[PLAYLIST SELECT] "
            "Error obteniendo playlists"
        )

        await query.edit_message_text(
            "❌ No he podido obtener "
            "las playlists."
        )
        return

    playlists = result.get(
        "playlists",
        [],
    )

    if not playlists:
        await query.edit_message_text(
            "⚠️ No hay playlists disponibles."
        )
        return

    context.user_data[
        "manual_playlists"
    ] = playlists

    await show_track_playlist_page(
        query=query,
        context=context,
        page=0,
    )

async def track_playlist_page_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    value = query.data.split(
        ":",
        1,
    )[1]

    if value == "current":
        return

    try:
        page = int(value)

    except ValueError:
        return

    await show_track_playlist_page(
        query=query,
        context=context,
        page=page,
    ) 

async def track_playlist_select_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        index = int(
            query.data.split(
                ":",
                1,
            )[1]
        )

    except (
        ValueError,
        IndexError,
    ):
        return

    playlists = context.user_data.get(
        "manual_playlists",
        [],
    )

    if (
        index < 0
        or index >= len(playlists)
    ):
        await query.edit_message_text(
            "⚠️ La selección ha caducado."
        )
        return

    job_id = context.user_data.get(
        "playlist_track_job_id"
    )

    if not job_id:
        await query.edit_message_text(
            "⚠️ La descarga asociada "
            "ya no está disponible."
        )
        return

    playlist = playlists[index]

    playlist_name = playlist.get(
        "name"
    )

    if not playlist_name:
        await query.edit_message_text(
            "⚠️ Playlist no válida."
        )
        return

    # Obtener la ruta real de la canción
    # desde el backend.
    try:
        job = await api_get(
            f"/jobs/{job_id}"
        )

    except Exception:
        logger.exception(
            f"[PLAYLIST ADD] "
            f"Error obteniendo job={job_id}"
        )

        await query.edit_message_text(
            "❌ No he podido obtener "
            "la información de la canción."
        )
        return

    if job.get("status") != "completed":
        await query.edit_message_text(
            "⚠️ La canción ya no aparece "
            "como completada."
        )
        return

    local_path = job.get(
        "local_path"
    )

    if not local_path:
        await query.edit_message_text(
            "⚠️ No encuentro la ruta local "
            "de esta canción."
        )
        return

    artist = job.get(
        "artist",
        "Desconocido",
    )

    track = job.get(
        "track",
        "Desconocida",
    )

    context.user_data[
        "track_playlist_pending"
    ] = {
        "playlist": playlist_name,
        "local_path": local_path,
        "artist": artist,
        "track": track,
        "create_if_missing": False,
    }

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Sí",
                    callback_data=(
                        "trackpl_confirm_yes"
                    ),
                ),
                InlineKeyboardButton(
                    "❌ No",
                    callback_data=(
                        "trackpl_confirm_no"
                    ),
                ),
            ]
        ]
    )

    await query.edit_message_text(
        (
            "⚠️ ¿Estás seguro?\n\n"
            f"🎵 {track}\n"
            f"👤 {artist}\n"
            f"📋 {playlist_name}\n\n"
            "La canción se añadirá "
            "a esta playlist."
        ),
        reply_markup=keyboard,
    )    

async def track_playlist_confirm_yes_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    state = context.user_data.get(
        "track_playlist_pending"
    )

    if not state:
        await query.edit_message_text(
            "⚠️ Esta selección ha caducado."
        )
        return

    playlist_name = state.get(
        "playlist"
    )

    local_path = state.get(
        "local_path"
    )

    artist = state.get(
        "artist",
        "Desconocido",
    )

    track = state.get(
        "track",
        "Desconocida",
    )

    create_if_missing = bool(
        state.get(
            "create_if_missing",
            False,
        )
    )

    if (
        not playlist_name
        or not local_path
    ):
        await query.edit_message_text(
            "⚠️ Faltan datos para "
            "actualizar la playlist."
        )
        return

    await query.edit_message_text(
        "💾 Actualizando playlist..."
    )

    try:
        result = await api_post(
            "/manual-playlists/add-tracks",
            {
                "playlist": playlist_name,
                "local_paths": [
                    local_path,
                ],
                "create_if_missing": (
                    create_if_missing
                ),
            },
        )

    except Exception:
        logger.exception(
            f"[PLAYLIST CONFIRM] "
            f"Error añadiendo "
            f"{artist!r} - {track!r} "
            f"a {playlist_name!r}"
        )

        await query.edit_message_text(
            "❌ No he podido actualizar "
            "la playlist."
        )
        return

    status = result.get(
        "status"
    )

    if status in (
        "updated",
        "unchanged",
    ):
        total_tracks = int(
            result.get(
                "track_count",
                0,
            )
            or 0
        )

        duration = (
            format_playlist_duration(
                result.get(
                    "duration_seconds"
                )
            )
        )

        size = format_file_size(
            int(
                result.get(
                    "size_bytes",
                    0,
                )
                or 0
            )
        )        

        added = int(
            result.get(
                "added",
                0,
            )
            or 0
        )

        duplicates = int(
            result.get(
                "duplicates",
                0,
            )
            or 0
        )

        if status == "updated":
            title = (
                "✅ Playlist actualizada"
            )
        else:
            title = (
                "ℹ️ La canción ya estaba "
                "en la playlist"
            )

        await query.edit_message_text(
            (
                f"{title}\n\n"
                f"📋 {playlist_name}\n"
                f"🎵 {track}\n"
                f"👤 {artist}\n\n"
                f"➕ {added} añadidas\n"
                f"♻️ {duplicates} ya estaban\n\n"
                f"🎶 Total: "
                f"{total_tracks} canciones\n"
                f"⏱ Duración: {duration}"
                f"💾 Tamaño: {size}"
            )
        )

    elif status == "not_found":
        await query.edit_message_text(
            (
                "⚠️ La playlist ya no existe:\n\n"
                f"📋 {playlist_name}"
            )
        )

        return

    else:
        await query.edit_message_text(
            (
                "❌ No he podido añadir "
                "la canción.\n\n"
                f"Estado: "
                f"{status or 'desconocido'}"
            )
        )

        return

    context.user_data.pop(
        "track_playlist_pending",
        None,
    )

    context.user_data.pop(
        "playlist_track_job_id",
        None,
    )

    context.user_data.pop(
        "manual_playlists",
        None,
    )


async def track_playlist_confirm_no_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    state = context.user_data.pop(
        "track_playlist_pending",
        None,
    )

    if not state:
        await query.edit_message_text(
            "⚠️ Esta selección ha caducado."
        )
        return

    create_if_missing = bool(
        state.get(
            "create_if_missing",
            False,
        )
    )

    # --------------------------------------------------
    # VENÍAMOS DE SELECCIONAR UNA PLAYLIST EXISTENTE
    #
    # "No" significa:
    # volver a la lista para elegir otra.
    # --------------------------------------------------

    if not create_if_missing:
        job_id = context.user_data.get(
            "playlist_track_job_id"
        )

        if not job_id:
            await query.edit_message_text(
                "⚠️ La descarga asociada "
                "ya no está disponible."
            )
            return

        playlists = context.user_data.get(
            "manual_playlists",
            [],
        )

        # Por seguridad, si la lista ya no está
        # en memoria, la volvemos a consultar.
        if not playlists:
            try:
                result = await api_get(
                    "/manual-playlists"
                )

                playlists = result.get(
                    "playlists",
                    [],
                )

                context.user_data[
                    "manual_playlists"
                ] = playlists

            except Exception:
                logger.exception(
                    "[PLAYLIST CONFIRM NO] "
                    "Error recuperando playlists"
                )

                await query.edit_message_text(
                    "❌ No he podido volver "
                    "a cargar las playlists."
                )
                return

        if not playlists:
            await query.edit_message_text(
                "⚠️ No hay playlists disponibles."
            )
            return

        await show_track_playlist_page(
            query=query,
            context=context,
            page=0,
        )

        return

    # --------------------------------------------------
    # VENÍAMOS DE CREAR UNA PLAYLIST NUEVA
    #
    # Aquí simplemente descartamos ese nombre,
    # pero mantenemos viva la canción para poder
    # elegir qué hacer después.
    # --------------------------------------------------

    context.user_data.pop(
        "waiting_new_playlist_name",
        None,
    )

    job_id = context.user_data.get(
        "playlist_track_job_id"
    )

    if not job_id:
        await query.edit_message_text(
            "✅ Operación cancelada."
        )
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📋 Playlist existente",
                    callback_data=(
                        f"trackpl_existing:"
                        f"{job_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "➕ Crear nueva",
                    callback_data=(
                        f"trackpl_new:"
                        f"{job_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancelar",
                    callback_data=(
                        f"trackpl_cancel:"
                        f"{job_id}"
                    ),
                )
            ],
        ]
    )

    await query.edit_message_text(
        "¿Qué quieres hacer con la canción?",
        reply_markup=keyboard,
    )      


async def track_playlist_new_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        job_id = query.data.split(
            ":",
            1,
        )[1]

    except IndexError:
        return

    context.user_data[
        "playlist_track_job_id"
    ] = job_id

    context.user_data[
        "waiting_new_playlist_name"
    ] = True

    await query.edit_message_text(
        (
            "✏️ Escribe el nombre de la "
            "nueva playlist.\n\n"
            "Por ejemplo:\n"
            "Funk favoritos"
        )
    )


async def track_playlist_cancel_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    context.user_data.pop(
        "playlist_track_job_id",
        None,
    )

    context.user_data.pop(
        "manual_playlists",
        None,
    )

    context.user_data.pop(
        "waiting_new_playlist_name",
        None,
    )

    await query.edit_message_text(
        "✅ No se añadirá la canción "
        "a ninguna playlist."
    )   

async def finalize_playlist_with_name(
    message,
    context,
    final_name: str,
    final_token: str,
):
    final_states = context.user_data.get(
        "playlist_final_states",
        {},
    )

    state = final_states.get(
        final_token
    )

    if not state:
        await message.reply_text(
            "⚠️ Esta playlist pendiente "
            "ha caducado."
        )
        return False

    new_downloaded_count = int(
        state.get(
            "new_downloaded_count",
            0,
        )
        or 0
    )

    already_existing_count = int(
        state.get(
            "already_existing_count",
            0,
        )
        or 0
    )

    failed_count = int(
        state.get(
            "failed_count",
            0,
        )
        or 0
    )

    temporary_playlist = state.get(
        "playlist"
    )

    if not temporary_playlist:
        await message.reply_text(
            "⚠️ No encuentro la playlist "
            "pendiente."
        )
        return False

    final_name = final_name.strip()

    if not final_name:
        await message.reply_text(
            "⚠️ Escribe un nombre válido."
        )
        return False

    if len(final_name) > 120:
        await message.reply_text(
            "⚠️ El nombre es demasiado largo. "
            "Usa como máximo 120 caracteres."
        )
        return False

    try:
        result = await api_post(
            "/finalize-playlist",
            {
                "playlist": temporary_playlist,
                "final_name": final_name,
            },
            timeout=30,
            retries=1,
        )

    except Exception:
        logger.exception(
            f"[PLAYLIST FINAL NAME] "
            f"temporary={temporary_playlist!r} | "
            f"final={final_name!r}"
        )

        await message.reply_text(
            "❌ No he podido guardar "
            "la playlist."
        )
        return False

    if result.get("status") != "completed":
        await message.reply_text(
            (
                "❌ No he podido finalizar "
                "la playlist.\n\n"
                f"Estado: "
                f"{result.get('status', 'desconocido')}"
            )
        )
        return False

    destination = result.get(
        "destination"
    )

    saved_name = (
        result.get("final_name")
        or final_name
    )

    total_tracks = int(
        result.get(
            "track_count",
            0,
        )
        or 0
    )

    total_duration = (
        format_playlist_duration(
            result.get(
                "duration_seconds"
            )
        )
    )

    total_size = format_file_size(
        int(
            result.get(
                "size_bytes",
                0,
            )
            or 0
        )
    )    

    location = format_playlist_location(
        destination
    )

    normalized_destination = (
        destination or ""
    ).replace(
        "\\",
        "/",
    )

    if "/lossless/" in normalized_destination:
        m3u_filename = (
            f"{saved_name} - lossless.m3u"
        )
    else:
        m3u_filename = (
            f"{saved_name}.m3u"
        )

    result_lines = [
        "✅ Playlist guardada",
        "",
        f"📁 {saved_name}",
        f"🎵 {m3u_filename}",
        f"📍 {location}",
        "",
        (
            f"🆕 {new_downloaded_count} "
            f"descargadas esta vez"
        ),
        (
            f"♻️ {already_existing_count} "
            f"ya estaban descargadas"
        ),
    ]

    if failed_count:
        result_lines.append(
            f"❌ {failed_count} con error"
        )

    result_lines.extend(
        [
            "",
            (
                f"🎶 Total: "
                f"{total_tracks} canciones"
            ),
            (
                f"⏱ Duración: "
                f"{total_duration}"
            ),
            (
                f"💾 Tamaño: "
                f"{total_size}"
            ),
        ]
    )

    await message.reply_text(
        "\n".join(
            result_lines
        )
    )

    # Eliminamos SOLO esta playlist pendiente.
    final_states.pop(
        final_token,
        None,
    )

    if not final_states:
        context.user_data.pop(
            "playlist_final_states",
            None,
        )

    waiting_token = context.user_data.get(
        "waiting_playlist_final_name_token"
    )

    if waiting_token == final_token:
        context.user_data.pop(
            "waiting_playlist_final_name_token",
            None,
        )

    return True      

async def playlist_final_use_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        final_token = query.data.split(
            ":",
            1,
        )[1]

    except IndexError:
        return

    final_states = context.user_data.get(
        "playlist_final_states",
        {},
    )

    state = final_states.get(
        final_token
    )

    if not state:
        await query.edit_message_text(
            "⚠️ Esta playlist pendiente "
            "ha caducado."
        )
        return

    suggested_name = state.get(
        "suggested_name"
    )

    if not suggested_name:
        await query.edit_message_text(
            "⚠️ No encuentro el nombre "
            "de la playlist."
        )
        return

    await query.edit_message_text(
        "💾 Guardando playlist..."
    )

    await finalize_playlist_with_name(
        message=query.message,
        context=context,
        final_name=suggested_name,
        final_token=final_token,
    )


async def playlist_final_custom_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    try:
        final_token = query.data.split(
            ":",
            1,
        )[1]

    except IndexError:
        return

    final_states = context.user_data.get(
        "playlist_final_states",
        {},
    )

    if final_token not in final_states:
        await query.edit_message_text(
            "⚠️ Esta playlist pendiente "
            "ha caducado."
        )
        return

    context.user_data[
        "waiting_playlist_final_name_token"
    ] = final_token

    await query.edit_message_text(
        (
            "✏️ Escribe el nombre con el que "
            "quieres guardar la playlist."
        )
    )   

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        TypeHandler(
            Update,
            authorized_user_guard,
        ),
        group=-100,
    )    

    app.add_handler(CallbackQueryHandler(
        search_quality_callback, pattern=r"^quality:[a-f0-9]{16}:(?:mp3_320|best)$",
    ))

    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    app.add_handler(
        CommandHandler(
            "album",
            album_command,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            album_page_callback,
            pattern=r"^album_page:",
        ),
        group=1,
    )

    app.add_handler(
        CallbackQueryHandler(
            album_select_callback,
            pattern=r"^album:",
        ),
        group=1,
    )

    app.add_handler(
        CallbackQueryHandler(
            album_download_callback,
            pattern=r"^albumdl:",
        ),
        group=1,
    )

    app.add_handler(
        CallbackQueryHandler(
            album_cancel_callback,
            pattern=r"^albumcancel$",
        ),
        group=1,
    )

    app.add_handler(
        CallbackQueryHandler(
            youtube_analyze_callback,
            pattern=r"^yt_analyze$",
        ),
        group=2,
    )

    app.add_handler(
        CallbackQueryHandler(
            youtube_download_callback,
            pattern=r"^yt_download$",
        ),
        group=2,
    )

    app.add_handler(
        MessageHandler(
            filters.Document.FileExtension(
                "m3u"
            )
            | filters.Document.FileExtension(
                "m3u8"
            ),
            m3u_playlist_handler,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            explore_track_callback,
            pattern=r"^explore_track:\d+$",
        ),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(
            explore_artist_callback,
            pattern=r"^explore_artist:\d+$",
        ),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(
            artist_track_callback,
            pattern=r"^artist_track:\d+$",
        ),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(
            artist_page_callback,
            pattern=r"^artist_page:",
        ),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(
            explore_page_callback,
            pattern=r"^explore_page:",
        ),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(
            spotify_download_callback,
            pattern=r"^spotify_download$",
        ),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(
            spotify_cancel_callback,
            pattern=r"^spotify_cancel$",
        ),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(
            track_playlist_existing_callback,
            pattern=r"^trackpl_existing:",
        ),
        group=4,
    )

    app.add_handler(
        CallbackQueryHandler(
            track_playlist_page_callback,
            pattern=r"^trackpl_page:",
        ),
        group=4,
    )

    app.add_handler(
        CallbackQueryHandler(
            track_playlist_select_callback,
            pattern=r"^trackpl_select:",
        ),
        group=4,
    )

    app.add_handler(
        CallbackQueryHandler(
            track_playlist_confirm_yes_callback,
            pattern=r"^trackpl_confirm_yes$",
        ),
        group=4,
    )

    app.add_handler(
        CallbackQueryHandler(
            track_playlist_confirm_no_callback,
            pattern=r"^trackpl_confirm_no$",
        ),
        group=4,
    )    

    app.add_handler(
        CallbackQueryHandler(
            track_playlist_new_callback,
            pattern=r"^trackpl_new:",
        ),
        group=4,
    )

    app.add_handler(
        CallbackQueryHandler(
            track_playlist_cancel_callback,
            pattern=r"^trackpl_cancel:",
        ),
        group=4,
    )    

    app.add_handler(
        CallbackQueryHandler(
            album_playlist_name_callback,
            pattern=r"^albumpl_name:",
        ),
        group=5,
    )

    app.add_handler(
        CallbackQueryHandler(
            album_playlist_custom_callback,
            pattern=r"^albumpl_custom$",
        ),
        group=5,
    )

    app.add_handler(
        CallbackQueryHandler(
            album_playlist_confirm_yes_callback,
            pattern=r"^albumpl_confirm_yes$",
        ),
        group=5,
    )

    app.add_handler(
        CallbackQueryHandler(
            album_playlist_confirm_no_callback,
            pattern=r"^albumpl_confirm_no$",
        ),
        group=5,
    )

    app.add_handler(
        CallbackQueryHandler(
            album_playlist_no_callback,
            pattern=r"^albumpl_no$",
        ),
        group=5,
    )    

    app.add_handler(
        CallbackQueryHandler(
            playlist_final_use_callback,
            pattern=r"^plfinal_use:",
        ),
        group=6,
    )

    app.add_handler(
        CallbackQueryHandler(
            playlist_final_custom_callback,
            pattern=r"^plfinal_custom:",
        ),
        group=6,
    )    

    logger.warning("[TELEGRAM] Bot iniciado")

    app.run_polling(
    allowed_updates=[
        "message",
        "callback_query",
    ]
)

if __name__ == "__main__":
    main()    