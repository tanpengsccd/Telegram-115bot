# Repository Guidelines

## Project Structure & Module Organization
Runtime code lives in `app/`: `core/` wraps the 115 OpenAPI plus schedulers, `handlers/` maps every Telegram command, `utils/` centralizes shared helpers, and `115bot.py` is the entry point executed inside Docker. Configuration templates sit in `config/` (copy `config.yaml.example` plus optional YAML such as `crawling_strategy.yaml`). Root-level `test_*.py` scripts cover rate limiting and parsers, while Docker assets (`Dockerfile*`, `docker-compose.yaml`, `build.sh`) support container workflows.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate`: Provision a Python 3.12+ environment.
- `pip install -r requirements.txt`: Install runtime and tooling dependencies.
- `python app/115bot.py`: Launch the bot locally using the active `config/config.yaml`.
- `bash build.sh`: Build both Docker images (`115bot:base`, then `115bot:latest`) for distribution.
- `docker compose up -d`: Start the published container with mounts for `config/`, `/tmp`, `/media`, and CloudDrive paths.
- `python -m pytest -q` or targeted scripts such as `python test_rate_limiter.py`: Run smoke and diagnostic tests before submission.

## Coding Style & Naming Conventions
Stick to PEP 8 with four-space indentation, snake_case for functions/modules, and UpperCamelCase for classes. Keep async handler bodies short and push complex logic into `app.utils` or feature helpers. Document every new config key inside the relevant `config/*.example` file, mirror existing kebab-case YAML, and favor explicit logging with actionable context over generic statements.

## Testing Guidelines
Expand the existing `test_*.py` suite whenever you adjust message parsing, download flows, or schedulers. Avoid real credentials: use fixtures in `tmp/` and gate network checks behind environment flags. Name new files `test_<feature>.py`, ensure they run via both `pytest` and `python file.py`, and aim for smoke coverage on every handler, utility, and scheduler touched in a PR.

## Commit & Pull Request Guidelines
History uses a lightweight Conventional Commits format (`feat:`, `fix:`, `chore:`) with concise Mandarin or English summaries—keep the subject imperative and under ~60 characters. PRs should describe the behavior change, call out config or migration steps, link issues, and attach screenshots or logs when bot replies change. Confirm the commands above have been run locally and reference any docs you updated so reviewers can follow along quickly.

## Security & Configuration Tips
Never commit populated `config/config.yaml`, tg_session files, or API secrets; mount them at runtime via Docker volumes or orchestrator secrets. Validate 115 credentials locally before pushing, rotate tokens if they enter logs, and honor the README warning that `/sync` deletes destination directories—mirror that caution in any automation you add.
