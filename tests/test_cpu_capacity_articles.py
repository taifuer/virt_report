"""Editorial boundaries for CPU hotplug."""
import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


@pytest.mark.parametrize("slug, topic, diagram", [
    ("x86-vcpu-hotplug", "hotplug", "cpu-hotplug-acpi-handoff"),
])
def test_cpu_capacity_articles_have_fixed_sources_and_offline_diagrams(slug, topic, diagram):
    article = mechanisms.get_article(slug)
    assert article["topic"] == topic
    page = render.render_mechanisms_html(Config(), slug)
    assert f'data-diagram="{diagram}"' in page
    assert all(f'/blob/{item["commit"]}/' in page for item in article["baselines"])
    assert 'id="example"' in page
    assert 'id="evolution"' in page
    assert "这里将补充" not in page


def test_cpu_hotplug_keeps_host_existence_guest_online_and_removal_separate():
    page = render.render_mechanisms_html(Config(), "x86-vcpu-hotplug")
    for marker in (
        "query-hotpluggable-cpus", "query-cpus-fast", "qom-path",
        "props.thread-id", "客户机 online", "DEVICE_DELETED", "ACPI eject",
        "启动 CPU", "事件可能在等待超时后才到达", "重连后应重新查询状态",
    ):
        assert marker in page


def test_numa_placement_article_is_retired():
    assert mechanisms.get_article("vcpu-numa-placement") is None
    assert render.render_mechanisms_html(Config(), "vcpu-numa-placement") is None
