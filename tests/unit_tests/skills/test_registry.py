from pathlib import Path

from agent_teams.skills.discovery import SkillsDirectory
from agent_teams.skills.registry import SkillRegistry


def test_get_toolset_tools_builds_skill_tools_without_annotation_errors() -> None:
    registry = SkillRegistry(directory=SkillsDirectory(base_dir=Path('.agent_teams/skills')))

    tools = registry.get_toolset_tools(('time',))

    names = {tool.name for tool in tools}
    assert names == {
        'list_skills',
        'load_skill',
        'read_skill_resource',
        'run_skill_script',
    }
