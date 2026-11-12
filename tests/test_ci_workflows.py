"""Keep routine CI bounded and independent of browser package installation."""
from fnmatch import fnmatchcase
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow(name: str) -> dict:
    # Preserve the Actions "on" key instead of YAML 1.1's boolean coercion.
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_model_checks_do_not_depend_on_browser_setup():
    jobs = workflow("ci.yml")["jobs"]
    assert "browser" not in jobs
    models = jobs["models"]
    commands = [step["run"] for step in models["steps"] if "run" in step]
    assert commands == ["npm run test:models"]
    assert "needs" not in models
    assert int(models["timeout-minutes"]) <= 3


def test_browser_workflow_only_runs_automatically_for_frontend_changes():
    triggers = workflow("browser.yml")["on"]
    paths = triggers["push"]["paths"]
    assert triggers["pull_request"]["paths"] == paths
    for filename in ["site/conference-papers.html", "site/assets/site.css",
                     "virt_report/render/assets/site.js", "virt_report/content/conferences.json",
                     "scripts/check_smoke_browser.cjs", "scripts/browser_runtime.cjs",
                     "package-lock.json", ".github/workflows/browser.yml"]:
        assert any(fnmatchcase(filename, pattern) for pattern in paths), filename
    for filename in ["docs/USAGE.md", "virt_report/collectors/lore.py",
                     "virt_report/summarize/prompts.py", "config.yaml"]:
        assert not any(fnmatchcase(filename, pattern) for pattern in paths), filename
    assert triggers["workflow_dispatch"]["inputs"]["full"]["default"] == "false"


def test_browser_ci_reuses_runner_chrome_without_apt_or_browser_downloads():
    job = workflow("browser.yml")["jobs"]["browser"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["env"]["PLAYWRIGHT_CHANNEL"] == "chrome"
    assert job["env"]["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] == "1"
    assert job["env"]["VIRT_REPORT_PYTHON"] == "python3"
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    for forbidden in ["playwright install", "apt-get", "pip install", "continue-on-error"]:
        assert forbidden not in commands
    assert "google-chrome --version" in commands
    assert "npm ci --no-audit --no-fund" in commands
    assert all("continue-on-error" not in step for step in job["steps"])


def test_full_audit_is_manual_and_smoke_failures_are_not_ignored():
    job = workflow("browser.yml")["jobs"]["browser"]
    smoke = next(s for s in job["steps"] if s.get("run") == "npm run test:browser")
    full = next(s for s in job["steps"] if s.get("run") == "npm run test:browser:full")
    assert "!inputs.full" in smoke["if"]
    assert "github.event_name == 'workflow_dispatch' && inputs.full" in full["if"]
    assert int(smoke["timeout-minutes"]) <= 4
    upload = next(s for s in job["steps"] if "upload-artifact" in s.get("uses", ""))
    assert "failure()" in upload["if"]
    assert "workflow_dispatch" in upload["if"] and "inputs.full" in upload["if"]


def test_new_commits_cancel_outdated_checks_in_the_same_branch():
    for name in ["ci.yml", "browser.yml"]:
        rule = workflow(name)["concurrency"]
        assert "github.ref" in rule["group"] and "github.workflow" in rule["group"]
        assert rule["cancel-in-progress"] == "true"
