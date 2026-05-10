"""Unit tests for the action planner facade."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app.cli.interactive_shell.orchestration.action_planner as action_planner_module
from app.cli.interactive_shell.orchestration.action_planner import (
    plan_actions_with_unhandled,
    plan_cli_actions,
    plan_terminal_tasks,
)


def test_plan_cli_actions_health_and_list() -> None:
    msg = "check opensre health and show connected services"
    assert plan_cli_actions(msg) == ["/health", "/list integrations"]


def test_plan_actions_with_unhandled_all_handled() -> None:
    msg = "check opensre health and show connected services"
    actions, unhandled = plan_actions_with_unhandled(msg)
    assert not unhandled
    assert [a.kind for a in actions] == ["slash", "slash"]


def test_plan_terminal_tasks_returns_kinds() -> None:
    msg = "check opensre health and show connected services"
    assert plan_terminal_tasks(msg) == ["slash", "slash"]


def test_plan_synthetic_test_without_scenario_uses_default() -> None:
    msg = "run a single synthetic test"
    with patch(
        "app.cli.interactive_shell.orchestration.action_planner.resolve_synthetic_scenario_with_llm",
        return_value=None,
    ):
        actions, unhandled = plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:001-replication-lag")
    ]


def test_plan_synthetic_test_with_explicit_scenario_id() -> None:
    msg = "run synthetic test 005-failover"
    actions, unhandled = plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:005-failover")
    ]
    assert plan_terminal_tasks(msg) == ["synthetic_test"]
    assert plan_cli_actions(msg) == []


def test_plan_typoed_synthetic_test_with_explicit_scenario_id() -> None:
    msg = "rnu syntehtic tset 002-connection-exhaustion"
    actions, unhandled = plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:002-connection-exhaustion")
    ]
    assert plan_terminal_tasks(msg) == ["synthetic_test"]
    assert plan_cli_actions(msg) == []


# ─────────────────────────────────────────────────────────────────────────────
# Ambiguous synthetic scenario text → LLM resolver (mocked)
#
# Canonical IDs like ``005-failover`` are matched deterministically. Bare
# numbers ("003") and descriptive phrases require
# ``resolve_synthetic_scenario_with_llm``; these tests pin planner wiring by
# mocking that helper rather than calling a live model.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def _clear_scenario_cache() -> None:
    """Drop the lru_cache so each test sees a fresh scenario list snapshot."""
    action_planner_module._list_rds_postgres_scenarios.cache_clear()


@pytest.mark.parametrize(
    "message,resolved_id,expected_content",
    [
        (
            "run synthetic test 002",
            "002-connection-exhaustion",
            "rds_postgres:002-connection-exhaustion",
        ),
        ("run synthetic test 003", "003-storage-full", "rds_postgres:003-storage-full"),
        ("launch synthetic test 005", "005-failover", "rds_postgres:005-failover"),
        ("run synthetic test 003.", "003-storage-full", "rds_postgres:003-storage-full"),
        ("rnu syntehtic tset 003", "003-storage-full", "rds_postgres:003-storage-full"),
    ],
)
def test_plan_synthetic_test_uses_llm_resolver_when_no_full_scenario_id(
    message: str,
    resolved_id: str,
    expected_content: str,
    _clear_scenario_cache: None,
) -> None:
    with patch(
        "app.cli.interactive_shell.orchestration.action_planner.resolve_synthetic_scenario_with_llm",
        return_value=resolved_id,
    ):
        actions, unhandled = plan_actions_with_unhandled(message)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [("synthetic_test", expected_content)]
    assert plan_terminal_tasks(message) == ["synthetic_test"]
    assert plan_cli_actions(message) == []


def test_plan_synthetic_test_unknown_bare_number_falls_back_to_default(
    _clear_scenario_cache: None,
) -> None:
    """When the LLM resolver declines (``None``), use the default scenario."""
    msg = "run synthetic test 999"
    with patch(
        "app.cli.interactive_shell.orchestration.action_planner.resolve_synthetic_scenario_with_llm",
        return_value=None,
    ):
        actions, unhandled = plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:001-replication-lag")
    ]


def test_plan_synthetic_test_bare_number_does_not_clobber_full_id(
    _clear_scenario_cache: None,
) -> None:
    """A canonical full scenario slug wins without consulting the LLM resolver."""
    msg = "run synthetic test 003-storage-full"
    with patch(
        "app.cli.interactive_shell.orchestration.action_planner.resolve_synthetic_scenario_with_llm",
    ) as mock_resolve:
        actions, unhandled = plan_actions_with_unhandled(msg)

    mock_resolve.assert_not_called()
    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [
        ("synthetic_test", "rds_postgres:003-storage-full")
    ]


def test_plan_terminal_tasks_returns_implementation_action() -> None:
    msg = "please implement process auto-discovery"
    actions, unhandled = plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [("implementation", "process auto-discovery")]
    assert plan_terminal_tasks(msg) == ["implementation"]
    assert plan_cli_actions(msg) == []


def test_plan_task_cancel_before_shell_kill() -> None:
    msg = "kill the syntehtic_test because it is running way too long"
    actions, unhandled = plan_actions_with_unhandled(msg)

    assert not unhandled
    assert [(a.kind, a.content) for a in actions] == [("task_cancel", "synthetic_test")]
    assert plan_terminal_tasks(msg) == ["task_cancel"]
    assert plan_cli_actions(msg) == []


def test_stop_process_prompt_is_not_task_cancel() -> None:
    msg = "stop the process of auto-investigation and give me a manual runbook"
    actions, unhandled = plan_actions_with_unhandled(msg)

    assert actions == []
    assert unhandled is True


def test_plan_cli_actions_remote_deployment_inventory_questions() -> None:
    messages = (
        "Which remote deployments are connected?",
        "Which remote's deployments are connected?",
        "What remote deployments are connected?",
        "show remote deployments",
        "list remote deployments",
    )

    for message in messages:
        assert plan_cli_actions(message) == ["/remote"]
