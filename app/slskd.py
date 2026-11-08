import requests
import time
import threading
from .ranking import score_file
from .entities import SearchResult


from .config import (
    SLSKD_URL,
    SLSKD_USERNAME,
    SLSKD_PASSWORD,
)

_SEARCH_START_LOCK = threading.Lock()
_LAST_SEARCH_START = 0.0

SEARCH_START_INTERVAL = 1.25
SEARCH_POST_RETRIES = 4
DOWNLOAD_POST_RETRIES = 3


class SlskdClient:

    def __init__(self):
        self.session = requests.Session()
        self.token = None

    def login(self):

        response = self.session.post(
            f"{SLSKD_URL}/api/v0/session",
            json={
                "username": SLSKD_USERNAME,
                "password": SLSKD_PASSWORD,
            },
        )

        response.raise_for_status()

        data = response.json()

        self.token = data["token"]

        self.session.headers.update({
            "Authorization": f"Bearer {self.token}"
        })

        return data   # <-- ESTE RETURN FALTABA

    def get_downloads(self):

        response = self.session.get(
            (
                f"{SLSKD_URL}/api/v0/"
                f"transfers/downloads"
            ),
            timeout=15,
        )

        response.raise_for_status()

        return response.json()


    def search(self, artist, track, album=None, search_track=None):
        terms = []

        if artist:
            terms.append(artist)

        query_track = search_track or track

        if query_track:
            terms.append(query_track)

        search_text = " ".join(terms)

        print(
            f"[SLSKD SEARCH] artist={artist!r} | "
            f"track={track!r} | "
            f"album={album!r} | "
            f"search_track={query_track!r} | "
            f"search_text={search_text!r}",
            flush=True,
        )

        global _LAST_SEARCH_START

        with _SEARCH_START_LOCK:
            # Evitamos crear dos búsquedas en slskd
            # prácticamente en el mismo instante.
            elapsed = (
                time.monotonic()
                - _LAST_SEARCH_START
            )

            wait_before_start = (
                SEARCH_START_INTERVAL
                - elapsed
            )

            if wait_before_start > 0:
                time.sleep(
                    wait_before_start
                )

            response = None

            for attempt in range(
                SEARCH_POST_RETRIES
            ):
                response = self.session.post(
                    f"{SLSKD_URL}/api/v0/searches",
                    json={
                        "searchText": search_text
                    },
                    timeout=15,
                )

                _LAST_SEARCH_START = (
                    time.monotonic()
                )

                if response.status_code != 429:
                    break

                retry_after = response.headers.get(
                    "Retry-After"
                )

                try:
                    retry_wait = float(
                        retry_after
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    retry_wait = (
                        2 * (attempt + 1)
                    )

                # Como mínimo esperamos un segundo.
                retry_wait = max(
                    retry_wait,
                    1.0,
                )

                print(
                    f"[SLSKD 429] "
                    f"search_text={search_text!r} | "
                    f"attempt={attempt + 1}/"
                    f"{SEARCH_POST_RETRIES} | "
                    f"retry_in={retry_wait:.1f}s",
                    flush=True,
                )

                time.sleep(
                    retry_wait
                )

            response.raise_for_status()

        search = response.json()
        search_id = search.get("id")

        # Soulseek devuelve resultados progresivamente.
        # Esperamos hasta que la cantidad de archivos se estabilice
        # o hasta alcanzar el tiempo máximo.
        max_wait = 60
        min_wait = 20
        poll_interval = 2
        stable_polls_required = 5

        responses = []
        previous_file_count = -1
        stable_polls = 0

        for elapsed in range(
            poll_interval,
            max_wait + 1,
            poll_interval,
        ):
            time.sleep(poll_interval)

            response = self.session.get(
                f"{SLSKD_URL}/api/v0/searches/{search_id}/responses"
            )

            response.raise_for_status()
            current_responses = response.json()

            file_count = sum(
                len(user.get("files", []))
                for user in current_responses
            )

            print(
                f"[SLSKD POLL] "
                f"elapsed={elapsed}s | "
                f"responses={len(current_responses)} | "
                f"files={file_count}",
                flush=True,
            )

            responses = current_responses

            if file_count > 0:
                if file_count == previous_file_count:
                    stable_polls += 1
                else:
                    stable_polls = 0

                if (
                    elapsed >= min_wait
                    and stable_polls >= stable_polls_required
                ):
                    break

            previous_file_count = file_count

        results = []

        for user in responses:
            username = user.get("username")
            queue = user.get("queueLength", 999)
            speed = user.get("uploadSpeed", 0)
            free_slot = user.get("hasFreeUploadSlot", False)

            for file in user.get("files", []):
                enriched_file = {
                    **file,
                    "queueLength": queue,
                    "uploadSpeed": speed,
                    "hasFreeUploadSlot": free_slot,
                }

                ranking = score_file(
                    file=enriched_file,
                    artist=artist,
                    track=track,
                    album=album,
                )

                results.append(
                    SearchResult(
                        username=username,
                        filename=enriched_file["filename"],
                        size=enriched_file["size"],
                        score=ranking["score"],
                        reasons=ranking["reasons"],
                        code=enriched_file.get("code", 0),
                        extension=enriched_file.get("extension", ""),
                        length=enriched_file.get("length", 0),
                        bitDepth=enriched_file.get("bitDepth", 0),
                        bitRate=enriched_file.get("bitRate", 0),
                        sampleRate=enriched_file.get("sampleRate", 0),
                        isLocked=enriched_file.get("isLocked", False),
                        queueLength=enriched_file.get("queueLength", 999),
                        uploadSpeed=enriched_file.get("uploadSpeed", 0),
                        hasFreeUploadSlot=enriched_file.get(
                            "hasFreeUploadSlot",
                            False,
                        ),
                    )
                )

        results.sort(
            key=lambda x: x.score,
            reverse=True,
        )

        return results

    def download(
        self,
        username,
        filename,
        size,
    ):
        response = None

        for attempt in range(
            DOWNLOAD_POST_RETRIES
        ):
            response = self.session.post(
                (
                    f"{SLSKD_URL}/api/v0/"
                    f"transfers/downloads/{username}"
                ),
                json=[
                    {
                        "filename": filename,
                        "size": size,
                    }
                ],
                timeout=20,
            )

            # Descarga aceptada.
            if response.status_code < 400:
                return response.json()

            # Solo reintentamos errores que pueden
            # ser temporales.
            if response.status_code not in (
                429,
                500,
                502,
                503,
                504,
            ):
                response.raise_for_status()

            retry_after = response.headers.get(
                "Retry-After"
            )

            try:
                wait_seconds = float(
                    retry_after
                )
            except (
                TypeError,
                ValueError,
            ):
                wait_seconds = (
                    2 * (attempt + 1)
                )

            print(
                f"[SLSKD DOWNLOAD RETRY] "
                f"user={username!r} | "
                f"status={response.status_code} | "
                f"attempt={attempt + 1}/"
                f"{DOWNLOAD_POST_RETRIES} | "
                f"retry_in={wait_seconds:.1f}s",
                flush=True,
            )

            # No esperamos después del último intento.
            if (
                attempt
                < DOWNLOAD_POST_RETRIES - 1
            ):
                time.sleep(wait_seconds)

        # Han fallado todos los intentos.
        response.raise_for_status()

    def get_download(
        self,
        download_id,
    ):

        downloads = self.get_downloads()

        for user in downloads:
            for directory in user.get(
                "directories",
                [],
            ):
                for file in directory.get(
                    "files",
                    [],
                ):
                    if (
                        file.get("id")
                        == download_id
                    ):
                        return file

        return None

    def cancel_download(
        self,
        username: str,
        download_id: str,
    ) -> bool:

        response = self.session.delete(
            f"{SLSKD_URL}/api/v0/transfers/downloads/"
            f"{username}/{download_id}"
        )

        if response.status_code == 404:
            return False

        response.raise_for_status()

        return True

    def get_user_presence(
        self,
        username: str,
    ) -> str | None:
        try:
            response = self.session.get(
                (
                    f"{SLSKD_URL}/api/v0/"
                    f"users/{username}/status"
                ),
                timeout=10,
            )

            response.raise_for_status()

            data = response.json()

            if isinstance(data, dict):
                presence = data.get(
                    "presence"
                )
            else:
                presence = data

            if isinstance(
                presence,
                str,
            ):
                return presence.casefold()

            return None

        except Exception as exc:
            print(
                f"[USER STATUS ERROR] "
                f"user={username!r} | "
                f"error={exc!r}",
                flush=True,
            )

            return None