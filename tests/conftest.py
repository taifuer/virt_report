"""Shared editorial evidence fixtures, independent of link presentation."""
import pytest

from virt_report import mechanisms
from virt_report.render import render


@pytest.fixture
def article_source():
    """Read authored evidence without requiring every citation to be displayed."""
    def read(slug):
        article = mechanisms.get_article(slug)
        assert article is not None
        path = (render.ARTICLES_DIR / article['source'] if article.get('source')
                else render.TEMPLATES_DIR / article['template'])
        return path.read_text(encoding='utf-8')
    return read
