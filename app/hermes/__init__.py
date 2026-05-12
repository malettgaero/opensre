"""Hermes agent: incident identification by polling Hermes log files.

The Hermes agent watches a Hermes log file (default
``~/.hermes/logs/errors.log``) and emits structured incidents whenever it
detects an ``ERROR``/``CRITICAL`` line, a Python traceback, or a burst of
warnings from the same logger. See :class:`HermesAgent` for the public
entrypoint.
"""

from __future__ import annotations

from app.hermes.agent import DEFAULT_LOG_PATH, HermesAgent, IncidentSink
from app.hermes.classifier import (
    DEFAULT_TRACEBACK_FOLLOWUP_S,
    DEFAULT_WARNING_BURST_THRESHOLD,
    DEFAULT_WARNING_BURST_WINDOW_S,
    IncidentClassifier,
    classify_all,
)
from app.hermes.incident import HermesIncident, IncidentSeverity, LogLevel, LogRecord
from app.hermes.parser import parse_log_line
from app.hermes.tailer import FileTailer

__all__ = [
    "DEFAULT_LOG_PATH",
    "DEFAULT_TRACEBACK_FOLLOWUP_S",
    "DEFAULT_WARNING_BURST_THRESHOLD",
    "DEFAULT_WARNING_BURST_WINDOW_S",
    "FileTailer",
    "HermesAgent",
    "HermesIncident",
    "IncidentClassifier",
    "IncidentSeverity",
    "IncidentSink",
    "LogLevel",
    "LogRecord",
    "classify_all",
    "parse_log_line",
]
