"""Keep the hand-maintained migration wire diagram accessible and self-contained."""

from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "virt_report/render/assets/migration-wire-layout.svg"
NS = {"svg": "http://www.w3.org/2000/svg"}


def test_migration_wire_diagram_is_accessible_self_contained_svg():
    root = ET.parse(ASSET).getroot()
    assert root.attrib["role"] == "img"
    assert root.find("svg:title", NS).text
    assert root.find("svg:desc", NS).text
    assert not root.findall(".//svg:script", NS)
    assert not root.findall(".//svg:image", NS)
    assert not root.findall(".//svg:foreignObject", NS)
    assert "http" not in " ".join(
        value
        for element in root.iter()
        for key, value in element.attrib.items()
        if key.endswith("href")
    )


def test_migration_wire_diagram_retains_full_stream_order_and_distinct_markers():
    root = ET.parse(ASSET).getroot()
    sections = [node.attrib["id"] for node in root.findall("svg:g", NS)]
    assert sections == [
        "flow-header", "flow-configuration", "flow-start", "flow-part",
        "flow-command", "flow-end", "flow-full", "flow-eof", "flow-description",
    ]
    text = " ".join(root.itertext())
    for marker in (
        "0x5145564d", "pc-q35-10.2", "used_length", "TARGET_PAGE_SIZE",
        "EOS = 0x10", "0x7e", "section_id", "0x00", "JSON length",
        "MIG_CMD_SWITCHOVER_START = 0x000b", "0x0000000000002028",
        "cpu_synchronize_all_states()", "Libvirt 保存文件头不属于",
    ):
        assert marker in text
    assert "没有统一的 payload length 字段" in text
    assert "RAM EOS 只属于 RAM 格式" in text


def test_wire_diagram_documents_hand_maintained_source():
    doc = ASSET.read_text()
    assert "Hand-maintained SVG" in doc
    assert "v10.2.0" in doc
    assert "save_section_header()" in doc
