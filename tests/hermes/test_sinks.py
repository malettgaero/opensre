"""Tests for :mod:`app.hermes.sinks`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.hermes.incident import HermesIncident, IncidentSeverity, LogLevel, LogRecord
from app.hermes.sinks import TelegramSink, TelegramSinkConfig, make_telegram_sink
from app.watch_dog.alarms import AlarmCredentials, AlarmDispatcher

_TS = datetime(2026, 5, 12, 0, 0, 0)


def _record(level: LogLevel, logger_name: str, message: str) -> LogRecord:
    raw = f"{_TS.isoformat()} {level.value} {logger_name}: {message}"
    return LogRecord(timestamp=_TS, level=level, logger=logger_name, message=message, raw=raw)


def _incident(
    *,
    rule: str = "error_severity",
    severity: IncidentSeverity = IncidentSeverity.HIGH,
    logger_name: str = "gateway.platforms.telegram",
    title: str = "ERROR from gateway.platforms.telegram",
    fingerprint: str = "deadbeef00000001",
    records: tuple[LogRecord, ...] | None = None,
    run_id: str | None = None,
) -> HermesIncident:
    if records is None:
        records = (_record(LogLevel.ERROR, logger_name, "boom"),)
    return HermesIncident(
        rule=rule,
        severity=severity,
        title=title,
        detected_at=_TS,
        logger=logger_name,
        fingerprint=fingerprint,
        records=records,
        run_id=run_id,
    )


def _capture_telegram(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _fake_post(chat_id: str, text: str, bot_token: str) -> tuple[bool, str, str]:
        calls.append({"chat_id": chat_id, "text": text, "bot_token": bot_token})
        return True, "", "1"

    monkeypatch.setattr("app.watch_dog.alarms.post_telegram_message", _fake_post)
    return calls


def _dispatcher(monkeypatch: pytest.MonkeyPatch) -> tuple[AlarmDispatcher, list[dict[str, Any]]]:
    calls = _capture_telegram(monkeypatch)
    creds = AlarmCredentials(bot_token="tok", chat_id="chat-1")
    return AlarmDispatcher(creds, cooldown_seconds=300.0), calls


class TestFormatting:
    def test_message_contains_core_incident_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)

        sink(_incident(run_id="run-xyz"))

        assert len(calls) == 1
        text = calls[0]["text"]
        # Each field the operator scans for at a glance.
        for needle in (
            "Hermes incident: ERROR from gateway.platforms.telegram",
            "severity: HIGH",
            "rule: error_severity",
            "logger: gateway.platforms.telegram",
            "fingerprint: deadbeef00000001",
            "run_id: run-xyz",
            "recent log records:",
        ):
            assert needle in text, f"missing {needle!r} in:\n{text}"

    def test_message_truncates_long_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher, config=TelegramSinkConfig(max_record_chars=50))

        long_msg = "x" * 500
        sink(_incident(records=(_record(LogLevel.ERROR, "noisy", long_msg),)))

        text = calls[0]["text"]
        # The raw record line should have been collapsed with the
        # ellipsis suffix, not pasted in full.
        assert long_msg not in text
        assert "…" in text

    def test_message_inlines_at_most_max_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher, config=TelegramSinkConfig(max_inlined_records=2))

        records = tuple(_record(LogLevel.ERROR, "noisy", f"line-{i}") for i in range(5))
        sink(_incident(records=records))

        text = calls[0]["text"]
        assert "line-0" in text
        assert "line-1" in text
        assert "line-4" not in text  # trimmed
        assert "3 more records omitted" in text


class TestSeverityRouting:
    def test_high_incident_triggers_investigation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "root cause: redis is down"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge)
        sink(_incident(severity=IncidentSeverity.HIGH))

        assert len(bridge_calls) == 1
        assert "investigation summary:" in calls[0]["text"]
        assert "root cause: redis is down" in calls[0]["text"]

    def test_critical_incident_triggers_investigation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "root cause: oom kill"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge)
        sink(_incident(severity=IncidentSeverity.CRITICAL))

        assert len(bridge_calls) == 1
        assert "root cause: oom kill" in calls[0]["text"]

    def test_medium_incident_skips_investigation_and_marks_notify_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "should not appear"

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge)
        sink(_incident(severity=IncidentSeverity.MEDIUM, rule="warning_burst"))

        assert bridge_calls == []
        text = calls[0]["text"]
        assert "investigation summary:" not in text
        assert "notify only" in text

    def test_bridge_returning_none_omits_summary_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)

        def _bridge(_incident: HermesIncident) -> str | None:
            return None

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge)
        sink(_incident(severity=IncidentSeverity.CRITICAL))

        assert "investigation summary:" not in calls[0]["text"]

    def test_bridge_exception_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)

        def _bridge(_incident: HermesIncident) -> str | None:
            raise RuntimeError("LLM unreachable")

        sink = TelegramSink(dispatcher, investigation_bridge=_bridge)
        # Must not raise — a broken investigation pipeline cannot block
        # notification delivery.
        sink(_incident(severity=IncidentSeverity.HIGH))

        assert len(calls) == 1
        assert "investigation summary:" not in calls[0]["text"]


class TestDispatcherIntegration:
    def test_duplicate_fingerprint_is_suppressed_by_cooldown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        # Freeze monotonic time so the second dispatch falls inside the
        # default 300-second cooldown.
        monkeypatch.setattr(AlarmDispatcher, "_now", staticmethod(lambda: 1000.0))

        sink = TelegramSink(dispatcher)
        sink(_incident(fingerprint="same-fp"))
        sink(_incident(fingerprint="same-fp"))

        assert len(calls) == 1

    def test_different_fingerprints_both_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        sink = TelegramSink(dispatcher)

        sink(_incident(fingerprint="fp-a"))
        sink(_incident(fingerprint="fp-b"))

        assert len(calls) == 2

    def test_make_telegram_sink_factory_returns_callable_with_bridge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispatcher, calls = _dispatcher(monkeypatch)
        bridge_calls: list[HermesIncident] = []

        def _bridge(incident: HermesIncident) -> str | None:
            bridge_calls.append(incident)
            return "RCA"

        sink = make_telegram_sink(dispatcher, investigation_bridge=_bridge)
        sink(_incident(severity=IncidentSeverity.HIGH))

        assert callable(sink)
        assert len(calls) == 1
        assert len(bridge_calls) == 1
