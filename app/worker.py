"""Scheduled fetching in its own process:  python -m app.worker

With ``worker: separate`` in config.yaml the web server runs no scheduler, so
several web processes do not each fetch the same feeds. This process fetches
once at startup, then every ``fetch.interval_minutes``, one run at a time
(``ingest.fetch_all`` also refuses to overlap with a web-triggered refresh).

SIGINT/SIGTERM let the current fetch finish, then exit; a second signal exits at once.
"""
import logging
import signal
import threading
import time

from . import config, ingest

log = logging.getLogger("noticias.worker")


def summarize(result):
    if result.get("skipped"):
        return "skipped: another fetch is running"
    sources = result["sources"].values()
    failed = [s["name"] for s in sources if not s["ok"]]
    line = (f"{sum(s['new'] for s in sources)} new, {result['total']} stored,"
            f" {len(sources) - len(failed)}/{len(sources)} sources ok")
    return line + (f" (problems: {', '.join(failed)})" if failed else "")


def run(stop, interval_seconds, fetch=ingest.fetch_all):
    """Fetch now, then every interval, until ``stop`` is set. Runs never overlap."""
    while not stop.is_set():
        started = time.monotonic()
        try:
            log.info("fetch done: %s", summarize(fetch()))
        except Exception:  # a failed run must not end the worker
            log.exception("fetch failed")
        # Interval measured from the start of the run, like the embedded scheduler.
        stop.wait(max(0.0, interval_seconds - (time.monotonic() - started)))


def install_signal_handlers(stop):
    def handle(signum, frame):
        if stop.is_set():
            raise KeyboardInterrupt
        log.info("%s received: stopping after the current fetch (again to stop now)",
                 signal.Signals(signum).name)
        stop.set()
    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    minutes = config.load_config()["fetch"].get("interval_minutes", 20)
    if config.worker_mode() == "embedded":
        log.warning("worker: embedded in config.yaml, so the web server fetches too;"
                    " runs will not overlap, but set worker: separate to leave it to this process")
    stop = threading.Event()
    install_signal_handlers(stop)
    log.info("fetching every %s min (Ctrl+C to stop)", minutes)
    try:
        run(stop, minutes * 60)
    except KeyboardInterrupt:   # second signal: do not wait for the fetch
        pass
    log.info("stopped")


if __name__ == "__main__":
    main()
