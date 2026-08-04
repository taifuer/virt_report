# Contributing to virt-report

Thank you for improving virt-report. Contributions may address collectors,
report quality, rendering, operations, documentation, or source metadata.

## Before You Start

Search existing issues before opening a new one. Discuss substantial changes
first, especially new data sources, schema migrations, scheduling behavior,
prompt changes, or anything that may materially increase network or API usage.
Security reports must follow [SECURITY.md](SECURITY.md), not a public issue.

## Local Development

Use Python 3.11 or newer and the project virtual environment:

```bash
.venv/bin/pip install -e .
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pyflakes virt_report tests
```

Network and LLM calls must be mocked in tests. Add focused regression tests for
behavioral changes. For rendering changes, rebuild `site/`, inspect desktop and
mobile layouts, and include screenshots in the pull request.

## Data and Generated Files

Follow [DATA_POLICY.md](DATA_POLICY.md). Never commit `.env`, runtime databases,
raw archives, caches, logs, credentials, or production metrics. New curated
records must include canonical source links. Keep generated changes limited to
the pages affected by your work.

## Commits and Pull Requests

Use your real Git author identity and concise imperative subjects, such as
`Fix GitLab activity windows`. Keep each commit focused. If Codex materially
contributed to a commit, add this trailer; otherwise omit it:

```text
Co-Authored-By: Codex (GPT-5.6 Sol) <noreply@openai.com>
```

Pull requests should describe the user-visible result, verification commands,
data migration implications, and changes to API cost or network behavior. Link
relevant issues and disclose material AI assistance. By contributing, you agree
that your contribution is licensed under Apache-2.0.
