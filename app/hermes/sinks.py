"""Incident sinks for the Hermes agent.

The Hermes agent emits :class:`HermesIncident` objects to a pluggable
``IncidentSink`` callable. This module provides the concrete sinks used
in production:

* :class:`TelegramSink` — formats an incident into a human-readable
  Telegram message and routes it through :class:`AlarmDispatcher` so
  duplicate incidents respect the per-fingerprint cooldown. For
  ``HIGH``/``CRITICAL`` incidents it can optionally trigger the OpenSRE
  investigation pipeline and append the resulting root-cause summary to
  the Telegram message before delivery.
* :func:`make_telegram_sink` — convenience factory returning an
  :data:`IncidentSink` callable bound to an existing
  :class:`AlarmDispatcher` (and optional investigation bridge).

The sink is intentionally *defensive*: any exception raised by the
investigation bridge or by Telegram delivery is logged but does not
re-raise. A buggy bridge must never silently disable incident
notifications.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.hermes.agent import IncidentSink
from app.hermes.incident import HermesIncident, IncidentSeverity, LogRecord
from app.watch_dog.alarms import AlarmDispatcher

logger = logging.getLogger(__name__)

# Severities that trigger a full RCA investigation. MEDIUM (warning
# bursts) intentionally short-circuits to a lighter-weight notification:
# bursts are noisy and the marginal investigation rarely surfaces a true
# root cause for them.
_INVESTIGATION_SEVERITIES: Final[frozenset[IncidentSeverity]] = frozenset(
    {IncidentSeverity.HIGH, IncidentSeverity.CRITICAL}
)

# Soft cap on how many raw log records we inline into the Telegram body.
# AlarmDispatcher truncates the final payload at the Telegram 4096 char
# limit, but trimming here keeps the message useful instead of having
# half the records cut off mid-traceback.
_MAX_INLINED_RECORDS: Final[int] = 8
_MAX_RECORD_CHARS: Final[int] = 280
_MAX_SUMMARY_CHARS: Final[int] = 1200

_SEVERITY_EMOJI: Final[dict[IncidentSeverity, str]] = {
    IncidentSeverity.LOW: "🟢",
    IncidentSeverity.MEDIUM: "🟡",
    IncidentSeverity.HIGH: "🟠",
    IncidentSeverity.CRITICAL: "🔴",
}


# An investigation bridge is any callable that, given an incident,
# returns a human-readable RCA summary (or ``None`` if it could not
# produce one). Implementations typically wrap ``run_investigation`` and
# extract ``state["summary"]``/``state["root_cause"]``. Returning
# ``None`` rather than raising is the documented contract — the sink
# treats exceptions and ``None`` identically (no RCA appended) but the
# former is logged at WARNING.
InvestigationBridge = Callable[[HermesIncident], str | None]


@dataclass(frozen=True, slots=True)
class TelegramSinkConfig:
    """Optional knobs for :class:`TelegramSink`.

    Defaults match the values used in production. The dataclass is
    frozen so tests can pass a config instance into the sink without
    worrying about cross-test mutation.
    """

    max_inlined_records: int = _MAX_INLINED_RECORDS
    max_record_chars: int = _MAX_RECORD_CHARS
    max_summary_chars: int = _MAX_SUMMARY_CHARS


class TelegramSink:
    """Format Hermes incidents and dispatch them to Telegram.

    Parameters
    ----------
    dispatcher:
        Pre-constructed :class:`AlarmDispatcher`. The sink uses
        ``dispatch(threshold_name=incident.fingerprint, message=...)`` so
        duplicate incidents (same fingerprint) are suppressed by the
        dispatcher's cooldown window.
    investigation_bridge:
        Optional callable invoked for ``HIGH``/``CRITICAL`` incidents.
        When provided, its return value is appended to the Telegram
        message before dispatch. Exceptions are caught and logged.
    config:
        Optional :class:`TelegramSinkConfig` overriding the inline
        truncation knobs.
    """

    __slots__ = ("_dispatcher", "_investigation_bridge", "_config")

    def __init__(
        self,
        dispatcher: AlarmDispatcher,
        *,
        investigation_bridge: InvestigationBridge | None = None,
        config: TelegramSinkConfig | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._investigation_bridge = investigation_bridge
        self._config = config if config is not None else TelegramSinkConfig()

    def __call__(self, incident: HermesIncident) -> None:
        """Format the incident and dispatch it. Never raises."""
        try:
            rca_summary = self._maybe_investigate(incident)
            message = self._format_message(incident, rca_summary=rca_summary)
            self._dispatcher.dispatch(incident.fingerprint, message)
        except Exception:
            # The Hermes agent already guards sink exceptions in its own
            # dispatch loop, but logging here gives the operator the
            # incident metadata that the agent's logger does not have.
            logger.exception(
                "telegram sink failed: rule=%s severity=%s fingerprint=%s",
                incident.rule,
                incident.severity.value,
                incident.fingerprint,
            )

    # ------------------------------------------------------------------
    # Investigation bridge

    def _maybe_investigate(self, incident: HermesIncident) -> str | None:
        bridge = self._investigation_bridge
        if bridge is None or incident.severity not in _INVESTIGATION_SEVERITIES:
            return None
        try:
            summary = bridge(incident)
        except Exception:
            logger.warning(
                "hermes investigation bridge raised: rule=%s fingerprint=%s",
                incident.rule,
                incident.fingerprint,
                exc_info=True,
            )
            return None
        if not summary:
            return None
        return _truncate(summary.strip(), self._config.max_summary_chars)

    # ------------------------------------------------------------------
    # Message formatting

    def _format_message(
        self,
        incident: HermesIncident,
        *,
        rca_summary: str | None,
    ) -> str:
        emoji = _SEVERITY_EMOJI.get(incident.severity, "⚠️")
        header = (
            f"{emoji} Hermes incident: {incident.title}\n"
            f"severity: {incident.severity.value.upper()}  "
            f"rule: {incident.rule}\n"
            f"logger: {incident.logger or '<unknown>'}\n"
            f"detected_at: {incident.detected_at.isoformat()}\n"
            f"fingerprint: {incident.fingerprint}"
        )
        if incident.run_id:
            header += f"\nrun_id: {incident.run_id}"

        body_parts: list[str] = [header]

        records_block = self._format_records(incident.records)
        if records_block:
            body_parts.append("recent log records:\n" + records_block)

        if rca_summary:
            body_parts.append("investigation summary:\n" + rca_summary)
        elif incident.severity == IncidentSeverity.MEDIUM:
            # Explicitly mark notify-only severity so the operator knows
            # an RCA was not attempted (vs. attempted and produced no
            # summary, which the branch above represents).
            body_parts.append("note: warning-burst severity — notify only, no investigation run.")

        return "\n\n".join(body_parts)

    def _format_records(self, records: tuple[LogRecord, ...]) -> str:
        if not records:
            return ""
        inlined = records[: self._config.max_inlined_records]
        omitted = len(records) - len(inlined)
        lines = [_truncate(record.raw, self._config.max_record_chars) for record in inlined]
        if omitted > 0:
            lines.append(f"… ({omitted} more record{'s' if omitted != 1 else ''} omitted)")
        return "\n".join(lines)


def make_telegram_sink(
    dispatcher: AlarmDispatcher,
    *,
    investigation_bridge: InvestigationBridge | None = None,
    config: TelegramSinkConfig | None = None,
) -> IncidentSink:
    """Build an :data:`IncidentSink` callable bound to ``dispatcher``."""
    sink = TelegramSink(
        dispatcher,
        investigation_bridge=investigation_bridge,
        config=config,
    )
    return sink


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


__all__ = [
    "InvestigationBridge",
    "TelegramSink",
    "TelegramSinkConfig",
    "make_telegram_sink",
]
