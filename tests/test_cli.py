import sys

from guild_manager_bench import cli


def test_run_accepts_max_reasoning_effort(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(sys, "argv", ["guild-manager", "run", "--reasoning-effort", "max"])
    monkeypatch.setattr(cli, "_run", lambda args: captured.setdefault("args", args))

    cli.main()

    assert captured["args"].reasoning_effort == "max"
