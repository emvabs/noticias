"""The separate fetch worker and who runs the scheduler."""
import asyncio
import signal
import threading

import pytest

from app import config, main, worker


def test_fetches_at_once_then_on_the_interval_until_stopped():
    stop = threading.Event()
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) == 3:
            stop.set()
        return {"skipped": True}

    worker.run(stop, 0.01, fetch)
    assert len(calls) == 3


def test_first_fetch_does_not_wait_for_the_interval():
    stop = threading.Event()
    calls = []

    def fetch():
        calls.append(1)
        stop.set()
        return {"skipped": True}

    t = threading.Thread(target=worker.run, args=(stop, 3600, fetch))
    t.start()
    t.join(2)
    assert calls == [1] and not t.is_alive()


def test_a_failed_fetch_does_not_end_the_worker():
    stop = threading.Event()
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("network down")
        stop.set()
        return {"skipped": True}

    worker.run(stop, 0, fetch)
    assert len(calls) == 2


def test_runs_never_overlap():
    stop = threading.Event()
    active, overlaps, calls = [0], [], []

    def fetch():
        active[0] += 1
        overlaps.append(active[0] > 1)
        calls.append(1)
        if len(calls) == 5:
            stop.set()
        active[0] -= 1
        return {"skipped": True}

    worker.run(stop, 0, fetch)
    assert not any(overlaps)


def test_first_signal_stops_after_the_fetch_second_stops_now():
    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        stop = threading.Event()
        worker.install_signal_handlers(stop)
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)
        assert stop.is_set()
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, old[0])
        signal.signal(signal.SIGINT, old[1])


def test_summary_names_failing_sources():
    result = {"total": 10, "sources": {
        "a": {"name": "A", "new": 2, "ok": True},
        "b": {"name": "B", "new": 0, "ok": False}}}
    assert worker.summarize(result) == "2 new, 10 stored, 1/2 sources ok (problems: B)"


class FakeScheduler:
    started = []

    def __init__(self, **kwargs):
        pass

    def add_job(self, *args, **kwargs):
        pass

    def start(self):
        FakeScheduler.started.append(self)

    def shutdown(self, wait=True):
        pass


def run_lifespan():
    async def go():
        async with main.lifespan(main.app):
            pass
    asyncio.run(go())


@pytest.mark.parametrize("mode,schedulers", [("embedded", 1), ("separate", 0)])
def test_web_server_runs_the_scheduler_only_when_embedded(monkeypatch, mode, schedulers):
    FakeScheduler.started = []
    monkeypatch.setattr(main, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(config, "load_config", lambda: {"worker": mode, "fetch": {}})
    run_lifespan()
    assert len(FakeScheduler.started) == schedulers


def test_embedded_is_the_default(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {})
    assert config.worker_mode() == "embedded"


def test_unknown_worker_mode_is_an_error(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {"worker": "both"})
    with pytest.raises(ValueError):
        config.worker_mode()
