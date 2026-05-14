# REPL watchdog demo (E2E-style)

Automated proof (runs in `make test-cov`):

```bash
uv run pytest tests/cli/interactive_shell/test_watchdog_repl_e2e_demo.py -v --tb=short
```

## Manual transcript (real TTY)

Prerequisites: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_DEFAULT_CHAT_ID` set if you want Telegram alarms on threshold breach.

1. Start the shell: `uv run opensre` (TTY).
2. Enable trust (skips elevated prompts): `/trust on`
3. Watch the current interpreter PID (example uses self-PID from the shell process): `/watch <pid> --max-cpu 95 --interval 2s`
4. Confirm the task id line: expect `task … started.`
5. List watchdog rows: `/watches` (id, pid, kind, status, thresholds, last sample).
6. Confirm the same task in the general list: `/tasks`
7. Stop the watcher: `/unwatch <task_id>` (or `/cancel <task_id>`).
8. Run `/watches` again: status should show `cancelled` for that id.

Optional: use a lower `--max-cpu` against a busy PID to trip an alarm; the REPL prints one line when Telegram delivery succeeds.
