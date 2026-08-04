# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `virt_report/`:

- `collectors/` fetches mailing-list and GitLab data.
- `processing/` rebuilds threads, classifies topics, and ranks activity.
- `summarize/` builds DeepSeek prompts and structured reports.
- `render/` contains Jinja2 rendering code and templates.
- `db.py`, `config.py`, and `cli.py` provide storage, configuration, and commands.

Tests are in `tests/`. Operational wrappers are in `scripts/`. Runtime data is stored under `data/` and must remain untracked. Generated static pages go to `site/`; refresh and commit this deployable snapshot after rendering changes or report backfills. The default configuration is `config.yaml`.

## Build, Test, and Development Commands

Use the project virtual environment for all commands:

```bash
.venv/bin/pip install -e .                 # editable install
.venv/bin/virt-report fetch --since-days 3 # collect data and rebuild threads
.venv/bin/virt-report daily --no-fetch     # generate a report from stored data
.venv/bin/virt-report topics-refresh       # rebuild offline topic snapshots
.venv/bin/virt-report status               # inspect source coverage and health
.venv/bin/virt-report index                # rebuild the static index
.venv/bin/python -m pytest tests/ -q        # run the test suite
.venv/bin/python -m pyflakes virt_report tests
.venv/bin/virt-report serve --host 127.0.0.1 --port 8090
.venv/bin/virt-report index                # optional static export
```

## Coding Style & Naming Conventions

Target Python 3.11+ and use four-space indentation. Follow PEP 8 naming: `snake_case` for functions and modules, `PascalCase` for classes, and uppercase names for constants. Add type hints to public functions and short docstrings where behavior is non-obvious. Keep collectors idempotent and normalize timestamps to UTC ISO-8601 strings. Run `pyflakes` before submitting changes.

## Testing Guidelines

Tests use `pytest`. Name files `test_*.py` and functions `test_<behavior>`. Network and LLM calls must be mocked or avoided in unit tests. Add regression tests for schema migrations, source pagination, thread identity, time-window behavior, prompt sanitization, and rendering changes. Run the full suite before opening a pull request.

## Commit & Pull Request Guidelines

Use concise imperative commits such as `Fix GitLab activity windows` or `Add HyperKitty pagination`, and keep each commit focused. Maintainer-authored repository commits use `taifu <taifu@taifua.com>`. External contributors must retain their real Git author identity; never replace their authorship with the maintainer identity.

Append this exact trailer only when Codex materially contributed to the commit; omit it from work completed without Codex assistance:

```text
Co-Authored-By: Codex (GPT-5.6 Sol) <noreply@openai.com>
```

Pull requests should explain the user-visible effect, data migration implications, verification commands, and any API cost or network behavior. Include screenshots for template or responsive-layout changes and link relevant issues when available.

## Security & Configuration Tips

Store `DEEPSEEK_API_KEY` and optional `GITLAB_TOKEN` in `.env`; never commit credentials. Do not log secrets or raw authorization headers. Preserve existing SQLite data during migrations and back it up before destructive maintenance.
