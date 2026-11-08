import threading
import time
import logging
from datetime import datetime
from .slskd import SlskdClient

logger = logging.getLogger(__name__)

from .services.queue_service import dequeue, persist, requeue
from .services.download_service import download
from .services.search_service import search_best

MAX_RETRIES = 2
WORKER_COUNT = 2


def worker():

    logger.warning(
        "[WORKER] Hilo ejecutándose"
    )

    while True:

        job = None

        try:

            job = dequeue()

            if not job:
                time.sleep(0.5)
                continue

            if job:
                logger.warning(
                    f"[WORKER DEQUEUE] "
                    f"worker={threading.current_thread().name} | "
                    f"job_id={job.job_id} | "
                    f"artist={job.artist!r} | "
                    f"track={job.track!r} | "
                    f"retry={job.retry_count}"
                )

            if job:

                job.status = "starting"
                job.started_at = datetime.now()
                persist()

                if job.retry_count > 0:

                    logger.warning(
                        f"[WORKER RETRY SEARCH] "
                        f"artist={job.artist!r} | "
                        f"track={job.track!r} | "
                        f"retry={job.retry_count} | "
                        f"excluded={job.failed_sources!r}"
                    )

                    best = search_best(
                        artist=job.artist,
                        track=job.track,
                        album=job.album,
                        excluded_sources=job.failed_sources,
                    )

                    if job.status == "cancelled":
                        continue

                    if best is None:
                        job.status = "failed"
                        job.error = "No se encontró una fuente alternativa"
                        job.finished_at = datetime.now()
                        persist()
                        continue

                    job.username = best.username
                    job.filename = best.filename
                    job.size = best.size
                    job.score = best.score
                    job.extension = best.extension
                    job.length = best.length
                    job.download_id = None
                    job.error = None

                    persist()

                    logger.warning(
                        f"[WORKER] Retry {job.retry_count}: "
                        f"nueva fuente={job.username} | "
                        f"archivo={job.filename}"
                    )

                logger.warning(
                    f"[WORKER] Descargando: {job.filename}"
                )                

                response = download(job)

                logger.warning(
                    f"[WORKER DOWNLOAD POST OK] "
                    f"artist={job.artist!r} | "
                    f"track={job.track!r} | "
                    f"user={job.username!r}"
                )

                enqueued = response.get("enqueued", [])

                if enqueued:

                    job.download_id = enqueued[0]["id"]

                    if job.status == "cancelled":

                        client = SlskdClient()
                        client.login()

                        remote_cancelled = client.cancel_download(
                            username=job.username,
                            download_id=job.download_id,
                        )

                        persist()

                    else:

                        job.status = "waiting_remote"
                        persist()

                else:

                    if job.status != "cancelled":
                        job.status = "failed"
                        job.error = "slskd no devolvió ninguna descarga"
                        job.finished_at = datetime.now()

                    persist()

        except Exception as e:

            logger.exception(
                "[WORKER] ERROR"
            )

            if (
                job
                and job.status != "cancelled"
            ):
                failed_source = None

                if job.username and job.filename:
                    failed_source = (
                        f"{job.username}|"
                        f"{job.filename}"
                    )

                elif job.username:
                    failed_source = job.username


                if (
                    failed_source
                    and failed_source
                    not in job.failed_sources
                ):
                    job.failed_sources.append(
                        failed_source
                    )

                if (
                    job.retry_count
                    < MAX_RETRIES
                ):
                    job.retry_count += 1

                    job.status = "queued"
                    job.error = str(e)
                    job.download_id = None
                    job.started_at = None
                    job.finished_at = None

                    persist()

                    logger.warning(
                        f"[WORKER] "
                        f"Reintentando con otra fuente | "
                        f"retry={job.retry_count}/"
                        f"{MAX_RETRIES} | "
                        f"failed_source="
                        f"{failed_source!r}"
                    )

                    requeue(job)

                else:
                    job.status = "failed"
                    job.error = str(e)
                    job.finished_at = (
                        datetime.now()
                    )

                    persist()

                    logger.error(
                        f"[WORKER] "
                        f"Retries agotados | "
                        f"artist={job.artist!r} | "
                        f"track={job.track!r}"
                    )


def start_worker():

    for index in range(
        WORKER_COUNT
    ):

        thread = threading.Thread(
            target=worker,
            daemon=True,
            name=(
                f"music-download-worker-"
                f"{index + 1}"
            ),
        )

        thread.start()

        logger.warning(
            f"[WORKER] Iniciado | "
            f"alive={thread.is_alive()} | "
            f"name={thread.name}"
        )