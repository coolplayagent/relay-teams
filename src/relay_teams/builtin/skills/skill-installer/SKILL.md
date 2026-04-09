---
name: skill-installer
description: Search and install Agent Teams skills. Use ClawHub search for keyword or slug lookups, and install into ~/.relay-teams/skills by default, or the configured app skills dir under RELAY_TEAMS_CONFIG_DIR, from ClawHub, a curated list, a GitHub repo/path, or a SkillsMP skill page URL.
---
Install skills with the helper scripts in this skill directory.

Use `python` with the absolute script paths returned by `load_skill`.

Search ClawHub when the user asks to search or find a skill by keyword, topic, or slug.

- Search ClawHub:
  `python "<search-clawhub-skills.py>" <query>`
- JSON output:
  `python "<search-clawhub-skills.py>" <query> --format json`

Install from ClawHub when the user names a ClawHub slug, or after they pick one from search results.

- Install from ClawHub:
  `python "<install-clawhub-skill.py>" <slug>`
- Install a specific version:
  `python "<install-clawhub-skill.py>" <slug> --version <version>`
- JSON output:
  `python "<install-clawhub-skill.py>" <slug> --format json`

When the user asks to both search and install a specific ClawHub skill in the same turn, use the combined script instead of stopping after search.

- Search and install in one step:
  `python "<search-and-install-clawhub-skill.py>" --query <query> --slug <slug>`
- JSON output:
  `python "<search-and-install-clawhub-skill.py>" --query <query> --slug <slug> --format json`

List curated skills when the user asks what is available, or when they invoke this skill without saying what to install.

- Default curated list:
  `python "<list-skills.py>" --repo openai/skills --path skills/.curated`
- JSON output:
  `python "<list-skills.py>" --repo openai/skills --path skills/.curated --format json`
- Experimental list:
  `python "<list-skills.py>" --repo openai/skills --path skills/.experimental`

Install a curated skill when the user gives a skill name.

- Curated install:
  `python "<install-skill-from-github.py>" --repo openai/skills --path skills/.curated/<skill-name>`
- Experimental install:
  `python "<install-skill-from-github.py>" --repo openai/skills --path skills/.experimental/<skill-name>`

Install from another repo when the user gives a GitHub repo/path or a GitHub tree URL.

- Repo/path form:
  `python "<install-skill-from-github.py>" --repo <owner>/<repo> --path <path/to/skill> [<path/to/skill> ...]`
- URL form:
  `python "<install-skill-from-github.py>" --url https://github.com/<owner>/<repo>/tree/<ref>/<path>`

Install from a SkillsMP page when the user provides a skillsmp.com/zh skill page URL.

- SkillsMP page:
  `python "<install-skill-from-github.py>" --url <skillsmp-page-url>`

After a skill is installed, bind it to one or more roles with the separate binding script.

- Bind to the current running role, or `MainAgent` if no role context is available:
  `python "<bind-skill-to-role.py>" --skill <skill-name>`
- Bind to specific roles:
  `python "<bind-skill-to-role.py>" --skill <skill-name> --role MainAgent --role Crafter`

Communication rules:

- When searching ClawHub, respond in this shape:
  `ClawHub search results for "{query}":`
  `<slug> - <title>`
  `Which one would you like installed?`
- If the user already named the ClawHub slug to install, do not stop after search. Run the install in the same turn.
- After a successful ClawHub install, mention the runtime name and canonical ref if they differ from the slug.
- When listing skills, respond in this shape:
  `Skills from {repo}:`
  `<skill-1>`
  `<skill-2> (already installed)`
  `Which ones would you like installed?`
- If the user asked for experimental skills, label the source as experimental.
- After a successful bind, mention which roles were updated to include the skill.
- If the runtime server is already open, you may also tell them they can reload skills from the existing settings/API flow.
- If a script fails, surface the full error text, including timeout, HTTP, network, or git command details. Do not replace it with a generic summary.

Behavior:

- Default install destination is `~/.relay-teams/skills/<skill-name>`, or the configured app skills dir under `RELAY_TEAMS_CONFIG_DIR`.
- ClawHub installs must use the Agent Teams app config dir as `--workdir`, so runtime-visible skills land under the correct app `skills/` directory.
- Treat the runtime `name` from `SKILL.md` as the source of truth. The ClawHub slug may differ.
- Binding defaults to the current running role.
- If no runtime role context is available during binding, fall back to `MainAgent`.
- Listing annotates already-installed skills from the current effective Agent Teams skill registry.
- Public repos default to direct download.
- If direct download fails with auth or permission errors, fall back to git sparse checkout.
- Abort if the destination skill directory already exists.
- Multiple `--path` values install multiple skills in one run.
- Install options: `--ref <ref>`, `--dest <path>`, `--method auto|download|git`, `--name <skill-name>`.
- Bind options: `--skill <skill-name>` and optional `--role <role-id>`.
- Use `skills/.curated` by default for curated skills and `skills/.experimental` for experimental skills.

## Scripts
- search-clawhub-skills: Search ClawHub skills by keyword or slug. (scripts/search-clawhub-skills.py)
- install-clawhub-skill: Install a ClawHub skill into the Agent Teams app skills directory and report the runtime identity. (scripts/install-clawhub-skill.py)
- search-and-install-clawhub-skill: Search ClawHub and then install the requested slug in the same turn. (scripts/search-and-install-clawhub-skill.py)
- list-skills: List remote skills with installed annotations. (scripts/list-skills.py)
- install-skill-from-github: Install one or more skills from GitHub or a SkillsMP page URL. (scripts/install-skill-from-github.py)
- bind-skill-to-role: Bind one or more installed skills to one or more roles. (scripts/bind-skill-to-role.py)
