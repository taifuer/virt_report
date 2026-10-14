"""Reuse compiled templates without serving stale local edits."""
import os

from virt_report.config import Config
from virt_report.render import render


def test_repeated_home_renders_reuse_template_compilation(monkeypatch):
    render._cached_env.cache_clear()
    environment = render._env()
    original = environment.compile
    compiled = []

    def counted(source, *args, **kwargs):
        compiled.append(args)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(environment, "compile", counted)
    context = render.build_home_context([], [], [])
    first = render.render_index_html(Config(), context)
    count = len(compiled)
    assert count > 0
    assert render.render_index_html(Config(), context) == first
    assert len(compiled) == count
    assert render._env() is environment
    render._cached_env.cache_clear()


def test_cached_environment_reloads_edited_templates_and_separates_paths(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    template = first / "sample.html"
    template.write_text("before {{ value }}", encoding="utf-8")
    (second / "sample.html").write_text("separate", encoding="utf-8")
    env = render._cached_env(str(first), "version-one")
    assert env.get_template("sample.html").render(value="<test>") == "before &lt;test&gt;"
    old_mtime = template.stat().st_mtime
    template.write_text("after {{ value }}", encoding="utf-8")
    os.utime(template, (old_mtime + 2, old_mtime + 2))
    assert env.get_template("sample.html").render(value="edit") == "after edit"
    assert render._cached_env(str(second), "version-one").get_template(
        "sample.html"
    ).render() == "separate"
    assert render._cached_env(str(first), "version-two").globals["asset_version"] == "version-two"
