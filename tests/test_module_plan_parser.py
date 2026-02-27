import pytest

from agent_teams.workflow.runtime_graph import parse_module_plan


def test_parse_module_plan_from_json_block() -> None:
    text = """
Design output

```json
{
  "modules": [
    {"module_id": "cli", "files": ["src/app.py"], "complexity": "M", "scope": "entry"},
    {"module_id": "core", "files": ["src/core.py"], "complexity": "L", "scope": "logic"}
  ]
}
```
"""
    items = parse_module_plan(text)
    assert len(items) == 2
    assert items[0].module_id == 'cli'
    assert items[1].complexity == 'L'


def test_parse_module_plan_requires_modules_block() -> None:
    with pytest.raises(ValueError):
        parse_module_plan('No module json here')
