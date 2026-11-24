"""Reading order and historical evidence for the principles catalogue."""
import re

import pytest

from virt_report import mechanisms, versions
from virt_report.config import Config
from virt_report.render import render


def sections(slug):
    page = render.render_mechanisms_html(Config(), slug)
    return {node.attrs["id"]: node for node in versions.Document(page).root.find("section")
            if node.attrs.get("class") == "mechanism-section"}


@pytest.mark.parametrize("slug,years,objects", [
    ("device-virtualization", ["2005", "2008", "2010", "2012", "2014", "2020"],
     ["virtio-net", "vhost-net", "VFIO", "vhost-user", "vDPA"]),
    ("interrupt-virtualization", ["2007", "2009", "2013", "2016"],
     ["LAPIC", "irqfd", "VMX", "VT-d"]),
])
def test_overview_defines_the_subject_then_dates_the_designs_in_the_preface(slug, years, objects):
    body = sections(slug)
    preface = body["preface"]
    history, = [h for h in preface.find("h3") if h.attrs.get("id") == "evolution"]
    assert preface.text().index(preface.find("p")[0].text()) < preface.text().index(history.text())
    timeline, = preface.find("table")
    rows = [" ".join(col.text() for col in row.find("td")) for row in timeline.find("tr")[1:]]
    years_in_table = [re.search(r"\d{4}", row).group() for row in rows if re.search(r"\d{4}", row)]
    assert years_in_table == years
    assert all(any(term in row for row in rows) for term in objects)
    sources = [a.attrs["href"] for a in body["sources"].find("a")]
    assert any("torvalds/linux/blob/v" in href for href in sources)
    assert list(body)[-2:] == ["summary", "sources"]


def test_irqfd_is_one_subsection_after_software_delivery_and_its_old_bookmark_works():
    page = render.render_mechanisms_html(Config(), "interrupt-virtualization")
    body = sections("interrupt-virtualization")
    assert "irqfd" not in body and "implementation" not in body
    assert [h.attrs["id"] for h in body["acceleration"].find("h3")] == [
        "irqfd", "posting", "comparison"]
    assert page.index('<section id="delivery"') < page.index('<h3 id="irqfd"')
    assert re.search(r'<span id="acceleration-part-1"[^>]*></span>\s*<h3 id="irqfd"', page)
    assert any("data-execution-fallback" in node.attrs for node in body["walkthrough"].find("div"))


def test_history_precedes_technical_detail_and_flow_articles_keep_their_entry_point():
    for slug in ("cpu-virtualization", "memory-virtualization", "virtualization-basics",
                 "virtio-device-initialization", "libvirt-internals", "qemu-internals",
                 "qemu-tcg", "qemu-multifd", "kvm-ept-fault", "iommufd"):
        assert any(h.attrs.get("id") in {"history", "evolution"}
                   for h in sections(slug)["preface"].find("h3")), slug
    for slug, first in (("linux-boot", "firmware"), ("libvirt-live-migration", "preparation"),
                        ("kvm-vm-creation", "handles")):
        assert list(sections(slug))[:2] == ["preface", first]
    for a in mechanisms.articles():
        assert a["sections"][0] == {"id": "preface", "title": "序言"}
        assert [section["title"] for section in a["sections"]][-2] == "总结", a["slug"]
