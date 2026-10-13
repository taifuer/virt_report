"""Local previews must not reuse an earlier header after rendering changes."""
from email.message import Message
from http.server import SimpleHTTPRequestHandler
from io import BytesIO

from scripts.preview_site import PreviewHandler


def test_preview_disables_browser_storage():
    handler = object.__new__(PreviewHandler)
    handler.request_version = "HTTP/1.1"
    handler.wfile = BytesIO()
    handler.end_headers()
    assert b"Cache-Control: no-store\r\n" in handler.wfile.getvalue()


def test_preview_does_not_return_stale_304_for_rapid_edits(monkeypatch):
    handler = object.__new__(PreviewHandler)
    handler.headers = Message()
    handler.headers["If-Modified-Since"] = "Tue, 13 Oct 2026 13:00:00 GMT"
    handler.headers["If-None-Match"] = '"old-html"'
    handler.headers["Accept"] = "text/html"

    def send_head(self):
        assert "If-Modified-Since" not in self.headers
        assert "If-None-Match" not in self.headers
        assert self.headers["Accept"] == "text/html"
        return "fresh file"

    monkeypatch.setattr(SimpleHTTPRequestHandler, "send_head", send_head)
    assert handler.send_head() == "fresh file"
