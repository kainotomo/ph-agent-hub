# =============================================================================
# PH Agent Hub — Resume cursor resolution
# =============================================================================
# `?since_seq=` / `Last-Event-ID` are per-run cursors.  A cursor from a
# previous run of the same session must be ignored: replaying run B's start
# only after run A's last sequence would silently drop B's first events.
# =============================================================================

from types import SimpleNamespace

from starlette.requests import Request

from src.api.chat import _resume_cursor


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [
        (key.lower().encode(), value.encode())
        for key, value in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/chat/session/s1/stream",
        "headers": raw,
    })


def _lease(run_id: str) -> SimpleNamespace:
    # _resume_cursor only reads run_id.
    return SimpleNamespace(run_id=run_id)


def test_explicit_cursor_is_used():
    cursor = _resume_cursor(_request(), 12, None, _lease("run-a"))
    assert cursor == 12


def test_explicit_cursor_for_same_run_is_used():
    cursor = _resume_cursor(_request(), 12, "run-a", _lease("run-a"))
    assert cursor == 12


def test_explicit_cursor_for_previous_run_is_ignored():
    cursor = _resume_cursor(_request(), 12, "run-b", _lease("run-a"))
    assert cursor is None


def test_zero_cursor_replays_from_the_start():
    assert _resume_cursor(_request(), 0, "run-a", _lease("run-a")) == 0


def test_last_event_id_header_is_parsed():
    request = _request({"Last-Event-ID": "run-a:7"})
    assert _resume_cursor(request, None, None, _lease("run-a")) == 7


def test_last_event_id_for_another_run_is_ignored():
    request = _request({"Last-Event-ID": "run-b:7"})
    assert _resume_cursor(request, None, None, _lease("run-a")) is None


def test_malformed_last_event_id_is_ignored():
    request = _request({"Last-Event-ID": "not-a-cursor"})
    assert _resume_cursor(request, None, None, _lease("run-a")) is None


def test_missing_cursor_replays_everything():
    assert _resume_cursor(_request(), None, None, _lease("run-a")) is None
