import sys

from guild_manager_bench import cli


def test_run_accepts_extended_reasoning_effort(monkeypatch) -> None:
    captured = {}

    def fake_run(args) -> None:
        captured["reasoning_effort"] = args.reasoning_effort

    monkeypatch.setattr(cli, "_run", fake_run)

    for reasoning_effort in ("ultra", "max"):
        monkeypatch.setattr(
            sys,
            "argv",
            ["guild-manager", "run", "--reasoning-effort", reasoning_effort],
        )

        cli.main()

        assert captured["reasoning_effort"] == reasoning_effort
