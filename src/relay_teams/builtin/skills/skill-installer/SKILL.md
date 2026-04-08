---
name: skill-installer
description: Install Agent Teams skills into ~/.agent-teams/skills from a curated list, a GitHub repo/path, or a SkillsMP skill page URL. Use when a user asks what skills are available, wants to install a curated or experimental skill, provides a GitHub skill path, or shares a https://skillsmp.com/zh skill page.
---
Install skills with the helper scripts in this skill directory.

Use `python` with the absolute script paths returned by `load_skill`.

List skills when the user asks what is available, or when they invoke this skill without saying what to install.

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

- Default install destination is `~/.agent-teams/skills/<skill-name>`.
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
- list-skills: List remote skills with installed annotations. (scripts/list-skills.py)
- install-skill-from-github: Install one or more skills from GitHub or a SkillsMP page URL. (scripts/install-skill-from-github.py)
- bind-skill-to-role: Bind one or more installed skills to one or more roles. (scripts/bind-skill-to-role.py)
