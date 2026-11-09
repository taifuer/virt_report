"""Troubleshooting entry points must identify their scope, not claim new measurements."""
import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


@pytest.mark.parametrize("slug,command,boundary", [
    ("memory-virtualization", "virsh dommemstat VM", "last-update"),
    ("virtio-blk-read", "virsh domblkstat VM vda --human", "缺项不代表零"),
    ("network-io", "virsh domifstat VM vnet0", "不能套用 TAP 名称"),
])
def test_read_only_verification_entry_is_listed_in_article_navigation(slug, command, boundary):
    article = mechanisms.get_article(slug)
    assert any(section["id"] == "verification" for section in article["sections"])
    html = render.render_mechanisms_html(Config(), slug)
    assert 'id="verification"' in html
    assert 'href="#verification"' in html
    assert command in html and boundary in html
    section = html.split('<section id="verification"', 1)[1].split('</section>', 1)[0]
    assert "virsh dumpxml VM" in section
    assert "https://www.libvirt.org/manpages/virsh.html#" in section
    assert "virsh setmem" not in section
    assert "--period" not in section
