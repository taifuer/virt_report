import json
import shutil
import subprocess

import pytest

from virt_report import db, editorial_search, mechanisms, search
from virt_report.config import Config
from virt_report.render import render


def test_catalogue_covers_published_pages_without_redirect_duplicates():
    documents = editorial_search.catalogue()["documents"]
    assert len(documents) == len(mechanisms.articles())
    assert len({item["href"] for item in documents}) == len(documents)
    assert not ({item["slug"] for item in documents} & {item["slug"] for item in mechanisms.redirects()})
    assert len([item for item in documents if item["kind"] == "demos"]) == 7


def test_editorial_matches_titles_sections_scenarios_and_multiple_terms():
    result = editorial_search.find("irqfd")
    assert "kvm-vcpu-run" in {item["slug"] for item in result["articles"]}
    assert "interrupt-delivery" in {item["slug"] for item in result["demos"]}
    for query in ("virtqueue", "ioeventfd", "irqfd"):
        assert {"virtio-blk-read", "network-io"} <= {
            item["slug"] for item in editorial_search.find(query)["demos"]}
    result = editorial_search.find("balloon")
    assert result["articles"][0]["slug"] == "virtio-balloon"
    assert not result["demos"]  # Embedded demos stay with their parent article.
    assert editorial_search.find("irqfd doesnotexist")["articles"] == []
    assert editorial_search.find("内存")["articles"]
    assert editorial_search.find("<script>alert(1)</script>")["articles"] == []


@pytest.mark.parametrize("query", ["", "K", "a" * 101, "irqfd", "balloon", "内存", "热迁移",
                                   "QEMU MMIO", "  virtio  GPU ", "<script>bad</script>",
                                   "__proto__", "constructor", "Straße", "ΟΣ"])
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
    assert 'href="mechanisms/interrupt-delivery.html"' in html
    assert "搜索索引正在准备" in html
    assert "不影响原理讲解与交互演示" in html
    assert 'data-editorial-results data-static="false">' in html


def test_review_date_displays_existing_metadata_without_refreshing_it():
    article = mechanisms.get_article("qemu-memory-region")
    html = render.render_mechanisms_html(Config(), article["slug"])
    assert f'技术复核：<time datetime="{article["reviewed_on"]}">' in html
    assert "不代表后续版本已验证" in html
    paper = render.render_mechanisms_html(Config(), "kvm-live-upgrade")
    assert "对应文中列明的论文与分享资料" in paper
