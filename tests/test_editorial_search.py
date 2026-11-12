import json
import shutil
import subprocess

import pytest

from virt_report import conferences, db, editorial_search, mechanisms, search, versions
from virt_report.config import Config, Render
from virt_report.render import render


def test_catalogue_covers_published_pages_without_redirect_duplicates():
    documents = editorial_search.catalogue()["documents"]
    assert len([item for item in documents if item["kind"] in {"articles", "demos"}]) == len(mechanisms.articles())
    assert len([item for item in documents if item["kind"] == "papers"]) == conferences.load_content()["paper_count"]
    assert {item["kind"] for item in documents} == {"articles", "papers", "versions"}
    assert len({item["href"] for item in documents}) == len(documents)
    assert not ({item["slug"] for item in documents} & {item["slug"] for item in mechanisms.redirects()})
    assert not [item for item in documents if item["kind"] == "demos"]


def test_editorial_matches_titles_sections_scenarios_and_multiple_terms():
    result = editorial_search.find("irqfd")
    assert "kvm-vcpu-run" in {item["slug"] for item in result["articles"]}
    assert "interrupt-virtualization" in {item["slug"] for item in result["articles"]}
    for query in ("virtqueue", "ioeventfd", "irqfd"):
        assert {"qemu-virtio-blk", "network-io"} <= {
            item["slug"] for item in editorial_search.find(query)["articles"]}
    result = editorial_search.find("balloon")
    assert result["articles"][0]["slug"] == "virtio-balloon"
    assert not result["demos"]  # Embedded demos stay with their parent article.
    assert editorial_search.find("irqfd doesnotexist")["articles"] == []
    assert editorial_search.find("内存")["articles"]
    assert editorial_search.find("<script>alert(1)</script>")["articles"] == []


@pytest.mark.parametrize("query", ["SR-IOV", "mdev", "doorbell", "ELSP", "libvirt XML"])
def test_gpu_sharing_demo_is_discoverable_by_assignment_and_access_terms(query):
    result = editorial_search.find(query)
    gpu = next(item for item in result["articles"] if item["slug"] == "gpu-execution")
    assert gpu["title"] == "GPU 命令执行：直通与共享虚拟化"
    assert "SR-IOV + mdev · 实例与作业边界" in gpu["headings"]


@pytest.mark.parametrize("query", ["", "K", "a" * 101, "irqfd", "balloon", "内存", "热迁移",
                                   "QEMU MMIO", "  virtio  GPU ", "<script>bad</script>",
                                   "__proto__", "constructor", "Straße", "ΟΣ", "Orthus",
                                   "Virtio 1.2", "Dune", "QEMU 10.2", "OSR"])
def test_python_and_static_browser_search_agree(query):
    if not shutil.which("node"):
        pytest.skip("Node.js is required for cross-runtime search checks")
    script = """
const {find} = require('./virt_report/render/assets/editorial-search.js');
let input=''; process.stdin.on('data', chunk=>input+=chunk);
process.stdin.on('end', ()=>{const args=JSON.parse(input); console.log(JSON.stringify(find(args.index,args.query)));});
"""
    output = subprocess.run(
        ["node", "-e", script], input=json.dumps({"index": editorial_search.catalogue(), "query": query}),
        text=True, capture_output=True, check=True,
    )
    assert json.loads(output.stdout) == editorial_search.find(query)


def test_editorial_results_work_without_community_index_and_ignore_community_filters(tmp_path):
    with db.connect(tmp_path / "empty.db") as conn:
        result = search.search(conn, "irqfd", project="libvirt", architecture_key="Arm")
    html = render.render_search_html(Config(), result)
    assert 'href="mechanisms/kvm-vcpu-run.html"' in html
    assert 'href="mechanisms/interrupt-virtualization.html"' in html
    assert "搜索索引正在准备" in html
    assert "不影响原理讲解与交互演示" in html
    assert 'data-editorial-results data-static="false">' in html


def test_paper_and_specification_results_link_to_real_page_anchors():
    papers = editorial_search.find("Dune")["papers"]
    assert papers[0]["href"] == "conference-papers.html#paper-osdi12-dune"
    specifications = editorial_search.find("Virtio 1.2")["versions"]
    assert any(item["href"].endswith("#release-virtio-1.2-cs01") for item in specifications)
    assert not any(item["kind"] == "papers" for item in specifications)
    html = render.render_search_html(Config(), {"query": "QEMU"}, static_mode=True)
    assert 'class="editorial-more"' in html
    assert 'data-editorial-kind="papers"' in html
    assert 'data-editorial-kind="versions"' in html
    assert editorial_search.find("OSR 2008")["papers"][0]["slug"] == "osr08-virtio"


def test_search_export_refreshes_its_browser_assets(tmp_path):
    config = Config(render=Render(output_dir=tmp_path))
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "editorial-search.js").write_text("old browser code")
    out = render.render_search(config, {"query": ""})
    assert 'data-editorial-kind="demos"' not in out.read_text()
    for name in render.SEARCH_ASSET_NAMES:
        assert (assets / name).read_bytes() == (render.ASSETS_DIR / name).read_bytes()


def test_runtime_release_snapshot_is_not_frozen_in_editorial_cache():
    content = {"releases": [], "specifications": [], "topics": {}}
    assert not editorial_search.find("QEMU 12.0", editorial_search.catalogue(content))["versions"]
    content["releases"].append({"project": "qemu", "project_label": "QEMU", "version": "12.0.0",
                                "kind": "feature", "released_on": "2027-01-01", "notes": ["新版本要点"]})
    assert editorial_search.find("QEMU 12.0", editorial_search.catalogue(content))["versions"]


def test_editorial_version_search_excludes_maintenance_releases():
    content = versions.load_content(Config())
    indexed = {item["slug"] for item in editorial_search.catalogue(content)["documents"]}
    for release in content["releases"]:
        key = f'release-{release["project"]}-{release["version"]}'
        assert (key in indexed) == (release["kind"] == "feature")


def test_review_date_displays_existing_metadata_without_refreshing_it():
    article = mechanisms.get_article("qemu-memory-region")
    html = render.render_mechanisms_html(Config(), article["slug"])
    assert f'技术复核：<time datetime="{article["reviewed_on"]}">' in html
    assert "针对文中标注的源码与规范基线" in html
    paper = render.render_mechanisms_html(Config(), "kvm-live-upgrade")
    assert "对应文中列明的论文与分享资料" in paper
