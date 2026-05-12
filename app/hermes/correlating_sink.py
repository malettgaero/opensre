"""Wrap an :class:`IncidentSink` with correlator-driven dedup & routing.

The :class:`IncidentCorrelator` decides *whether* and *where* an
incident should go; this module adapts that decision into the existing
sink contract (a callable that takes a :class:`HermesIncident`).

Usage::

    correlator = IncidentCorrelator()
    telegram = make_telegram_sink(dispatcher)
    sink = CorrelatingSink(
        correlator=correlator,
        routes={
            RouteDestination.TELEGRAM: telegram,
            RouteDestination.TELEGRAM_WITH_RCA: telegram,
        },
    )
    agent = HermesAgent(..., incident_sink=sink)

Incidents bound for :attr:`RouteDestination.DROP` are counted but
never forwarded. Routes that aren't registered are logged at INFO
once per (rule, destination) pair so misconfiguration is visible
without flooding the logs.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.hermes.correlator import (
    CorrelatorDecision,
    IncidentCorrelator,
    RouteDestination,
)
from app.hermes.incident import HermesIncident

logger = logging.getLogger(__name__)

IncidentSinkFn = Callable[[HermesIncident], None]

__all__ = ["CorrelatingSink"]


class CorrelatingSink:
    """Sink wrapper that consults a correlator before dispatching."""

    __slots__ = (
        "_correlator",
        "_routes",
        "_default_route",
        "_missing_warned",
        "_lock",
        "_metrics",
    )

    def __init__(
        self,
        *,
        correlator: IncidentCorrelator,
        routes: dict[RouteDestination, IncidentSinkFn],
        default_route: IncidentSinkFn | None = None,
    ) -> None:
        self._correlator = correlator
        self._routes: dict[RouteDestination, IncidentSinkFn] = dict(routes)
        self._default_route = default_route
        self._missing_warned: set[tuple[str, RouteDestination]] = set()
        self._lock = threading.Lock()
        self._metrics: dict[str, int] = {
            "delivered": 0,
            "suppressed": 0,
            "escalated": 0,
            "dropped": 0,
            "unrouted": 0,
        }

    def __call__(self, incident: HermesIncident) -> None:
        decision = self._correlator.correlate(incident)
        self._record(decision)
        if decision.suppressed or decision.destination is RouteDestination.DROP:
            return
        sink_fn = self._routes.get(decision.destination, self._default_route)
        if sink_fn is None:
            self._warn_missing_route(incident.rule, decision.destination)
            with self._lock:
                self._metrics["unrouted"] += 1
            return
        try:
            sink_fn(decision.deliver)
        except Exception:  # noqa: BLE001 — sinks must never crash the agent
            logger.exception(
                "downstream sink raised for incident rule=%s destination=%s",
                incident.rule,
                decision.destination.value,
            )

    def metrics_snapshot(self) -> dict[str, int]:
        """Return a copy of the running counters. Useful for ops dashboards."""
        with self._lock:
            return dict(self._metrics)

    def _record(self, decision: CorrelatorDecision) -> None:
        with self._lock:
            if decision.suppressed:
                self._metrics["suppressed"] += 1
            elif decision.destination is RouteDestination.DROP:
                self._metrics["dropped"] += 1
            else:
                self._metrics["delivered"] += 1
            if decision.escalated_from is not None:
                self._metrics["escalated"] += 1

    def _warn_missing_route(self, rule: str, destination: RouteDestination) -> None:
        key = (rule, destination)
        with self._lock:
            if key in self._missing_warned:
                return
            self._missing_warned.add(key)
        logger.info(
            "no sink registered for destination=%s (rule=%s); dropping",
            destination.value,
            rule,
        )
