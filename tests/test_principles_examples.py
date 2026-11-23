"""Keep the prose, interactive examples, and offline figures in agreement."""
from pathlib import Path
from xml.etree import ElementTree

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render
from virt_report.versions import Document


def test_local_disk_write_matches_the_article_and_all_three_figures():
    route = next(r for r in mechanisms.get_io_scenarios()["routes"] if r["id"] == "local-blk")
    write = route["scenarios"]["write"]
    request = write["request"]
    steps = {s["id"]: s["changes"] for s in write["steps"]}
    chain = " → ".join(str(d["id"]) for d in request["descriptors"])
    head = request["head"]
    size, sector = request["queueSize"], request["sector"]
    used = steps["write-used"]
    assert [d["next"] for d in request["descriptors"]] == [1, 2, None]
    assert used["usedId"] == head and used["usedLength"] == 1

    root = Document(render.render_mechanisms_html(Config(), "qemu-virtio-blk")).root
    sections = {s.attrs["id"]: s for s in root.find("section")
                if s.attrs.get("class") == "mechanism-section"}
    description = sections["request"].text()
    for term in (f"队列大小为 {size}", chain, f"sector = {sector}", f"链头 {head}"):
        assert term in description
    assert f"更新 idx 为 {steps['publish-available']['availIndex']}" in description
    assert f"used.ring[0] = {{ id: {head}, len: {used['usedLength']} }}" in sections["completion"].text()
    assert f"used.idx: 0 → {steps['publish-used']['usedIndex']}" in sections["completion"].text()
    assert "comparison" not in sections
    assert {h.attrs["id"] for h in sections["persistence"].find("h3")} >= {
        "comparison", "persistence-part-1", "persistence-part-2"}

    markers = {
        "virtio-blk-request": [f"链头 = {head}", f"desc {head}", "NEXT = 1", "NEXT = 2"],
        "virtio-blk-request-cycle": [chain, f"id:{head}, len:1"],
        "virtio-blk-completion": [f"链头 {head}", f"id:{head}, len:1"],
    }
    diagrams = Path(__file__).resolve().parents[1] / "virt_report/content/diagrams"
    for name, labels in markers.items():
        source = (diagrams / f"{name}.mmd").read_text()
        svg = ElementTree.parse(render.ASSETS_DIR / f"{name}.svg").getroot()
        visible = " ".join("".join(node.itertext()) for node in svg.iter()
                           if node.tag.endswith("}text"))
        assert all(label in source and label in visible for label in labels)


def test_halt_experiment_sits_beside_the_example_before_the_implementation():
    article = mechanisms.get_article("kvm-vcpu-halt")
    assert [s["id"] for s in article["sections"][:4]] == ["preface", "example", "experiment", "halt"]
    page = render.render_mechanisms_html(Config(), article["slug"])
    assert page.count('id="halt-poll-title"') == 1
    assert page.index('id="example"') < page.index('id="halt-poll-title"') < page.index('id="halt"')
