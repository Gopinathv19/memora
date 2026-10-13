"""Standalone job worker: `python -m app.worker`.

Runs the durable job queue (jobs table) without the API. Use it with
JOB_QUEUE_MODE=durable and JOB_WORKER_ENABLED=false on the API, to scale
background work separately from request handling.
"""

import logging
import signal
import threading

from app.services import job_service


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    job_service.start_workers(stop)
    stop.wait()


if __name__ == "__main__":
    main()
