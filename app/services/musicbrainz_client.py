import logging
import threading
import time

import requests


logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "music-search/1.0 "
        "(https://github.com/javikobcn/music-search)"
    )
}

MIN_REQUEST_INTERVAL = 1.1
MAX_ATTEMPTS = 3

RETRYABLE_STATUS = (
    429,
    502,
    503,
    504,
)

_request_lock = threading.Lock()
_next_request_at = 0.0


def musicbrainz_request(
    url: str,
    *,
    params: dict,
    timeout: int = 15,
) -> requests.Response:

    global _next_request_at

    for attempt in range(MAX_ATTEMPTS):

        with _request_lock:

            delay = (
                _next_request_at
                - time.monotonic()
            )

            if delay > 0:
                time.sleep(delay)

            _next_request_at = (
                time.monotonic()
                + MIN_REQUEST_INTERVAL
            )

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=timeout,
            )

            if (
                response.status_code
                not in RETRYABLE_STATUS
            ):
                return response

            if attempt == MAX_ATTEMPTS - 1:
                return response

            wait_seconds = 2 ** (attempt + 1)

            _next_request_at = max(
                _next_request_at,
                time.monotonic() + wait_seconds,
            )

            logger.warning(
                f"[MUSICBRAINZ RETRY] "
                f"status={response.status_code} | "
                f"attempt={attempt + 1}/{MAX_ATTEMPTS} | "
                f"wait={wait_seconds}s | "
                f"url={url}"
            )