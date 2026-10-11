from pathlib import Path

from streamlit.testing.v1 import AppTest

from frontend.examples import EXAMPLE_QUERIES

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def test_main_page_renders():
    app = AppTest.from_file(str(FRONTEND / "app.py"), default_timeout=30).run()
    assert not app.exception
    assert app.title[0].value == "Q-Select — PostgreSQL Query Rewriter"
    assert "Run (or press Ctrl/Cmd+Enter)" in [b.label for b in app.button]


def test_examples_page_renders_every_example():
    page = AppTest.from_file(str(FRONTEND / "pages" / "1_Example_Queries.py"), default_timeout=30).run()
    assert not page.exception
    assert [b.label for b in page.button] == ["Test this query"] * len(EXAMPLE_QUERIES)
    assert len(page.code) == len(EXAMPLE_QUERIES)
