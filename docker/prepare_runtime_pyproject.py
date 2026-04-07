from __future__ import annotations

from pathlib import Path


def main() -> None:
    pyproject_path = Path("/build/pyproject.toml")
    text = pyproject_path.read_text(encoding="utf-8")
    eval_entrypoint = 'relay-teams-evals = "agent_teams_evals.run:app"\n'

    if eval_entrypoint not in text:
        raise SystemExit("expected eval entrypoint not found in pyproject.toml")

    pyproject_path.write_text(
        text.replace(eval_entrypoint, ""),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
