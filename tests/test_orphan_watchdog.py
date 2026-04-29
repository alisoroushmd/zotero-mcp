"""Tests for the orphan-process watchdog.

These exercise the gating logic only — we never let the watchdog thread
actually fire its ``os._exit(0)`` path, since that would kill the pytest
runner.
"""

from __future__ import annotations

import threading

import zotero_mcp._orphan_watchdog as watchdog


def _reset_installed_flag() -> None:
    watchdog._INSTALLED = False


def _watchdog_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "orphan-watchdog"]


def test_install_is_idempotent(monkeypatch):
    """Calling install() twice should only ever spawn one watchdog thread."""
    _reset_installed_flag()
    monkeypatch.delenv("PARENT_WATCHDOG_DISABLE", raising=False)
    monkeypatch.setattr(watchdog.os, "getppid", lambda: 12345)

    before = len(_watchdog_threads())
    watchdog.install(poll_interval=3600.0)
    watchdog.install(poll_interval=3600.0)
    after = len(_watchdog_threads())

    assert after - before == 1
    assert watchdog._INSTALLED is True


def test_install_disabled_via_env(monkeypatch):
    """PARENT_WATCHDOG_DISABLE=1 should short-circuit before any thread starts."""
    _reset_installed_flag()
    monkeypatch.setenv("PARENT_WATCHDOG_DISABLE", "1")

    before = len(_watchdog_threads())
    watchdog.install(poll_interval=3600.0)
    after = len(_watchdog_threads())

    assert after == before
    assert watchdog._INSTALLED is False


def test_install_skips_when_already_pid1_child(monkeypatch):
    """If our parent is already PID 1, the watchdog has nothing to watch."""
    _reset_installed_flag()
    monkeypatch.delenv("PARENT_WATCHDOG_DISABLE", raising=False)
    monkeypatch.setattr(watchdog.os, "getppid", lambda: 1)

    before = len(_watchdog_threads())
    watchdog.install(poll_interval=3600.0)
    after = len(_watchdog_threads())

    assert after == before
    # The flag is set before the ppid check, but no thread is spawned.
    assert watchdog._INSTALLED is True


def test_spawned_thread_is_daemon(monkeypatch):
    """Watchdog must be a daemon so it doesn't block process shutdown."""
    _reset_installed_flag()
    monkeypatch.delenv("PARENT_WATCHDOG_DISABLE", raising=False)
    monkeypatch.setattr(watchdog.os, "getppid", lambda: 12345)

    watchdog.install(poll_interval=3600.0)

    threads = _watchdog_threads()
    assert threads, "expected watchdog thread to be running"
    assert all(t.daemon for t in threads)
