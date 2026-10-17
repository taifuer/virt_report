"""Editorial boundaries for CPU compatibility, hotplug and NUMA placement."""
import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


@pytest.mark.parametrize("slug, topic, diagram", [
    ("cpu-model-migration", "cpu-virtualization", "cpu-model-migration-contract"),
    ("x86-vcpu-hotplug", "hotplug", "cpu-hotplug-acpi-handoff"),
    ("vcpu-numa-placement", "memory-virtualization", "numa-placement-layers"),
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


def test_cpu_model_expansion_is_not_presented_as_host_compatibility_validation():
    page = render.render_mechanisms_html(Config(), "cpu-model-migration")
    for marker in (
        "query-cpu-definitions", "query-cpu-model-expansion", "unavailable-features",
        "展开操作本身不会完成宿主可运行性检查", "运行状态", "冷启动",
        "不能在迁移中途删掉它", "TSC", "migratable",
    ):
        assert marker in page


def test_cpu_hotplug_keeps_host_existence_guest_online_and_removal_separate():
    page = render.render_mechanisms_html(Config(), "x86-vcpu-hotplug")
    for marker in (
        "query-hotpluggable-cpus", "query-cpus-fast", "qom-path",
        "props.thread-id", "客户机 online", "DEVICE_DELETED", "ACPI eject",
        "启动 CPU", "超时与删除失败不是同义词",
    ):
        assert marker in page


def test_numa_article_distinguishes_guest_topology_and_physical_placement():
    page = render.render_mechanisms_html(Config(), "vcpu-numa-placement")
    for marker in (
        "nodeid=0", "host-nodes=1", "先通过", "mbind()", "再执行预分配",
        "单纯调整线程亲和性不会把这些页一起搬走", "IOThread",
        "Cpus_allowed_list", "numa_maps", "numastat -p", "迁移后应重新验证",
    ):
        assert marker in page
