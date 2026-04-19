from go2web.http_client import Response
from go2web.render import to_text


def test_renders_json():
    response = Response(
        status_code=200,
        reason="OK",
        headers={"content-type": "application/json"},
        body=b'{"ok":true,"n":1}',
        url="https://example.com",
    )
    rendered = to_text(response)
    assert '"ok": true' in rendered


def test_renders_html_without_tags():
    html = b"<html><body><h1>Hello</h1><script>x=1</script><p>World</p></body></html>"
    response = Response(
        status_code=200,
        reason="OK",
        headers={"content-type": "text/html; charset=utf-8"},
        body=html,
        url="https://example.com",
    )
    rendered = to_text(response)
    assert "Hello" in rendered
    assert "World" in rendered
    assert "<h1>" not in rendered
    assert "x=1" not in rendered


def test_drops_navigation_and_footer_noise():
    html = b"""
    <html>
      <body>
        <nav>Privacy policy Terms of Use</nav>
        <article>
          <h1>Cat</h1>
          <p>The cat is a domestic species of small carnivorous mammal.</p>
          <p>It is the only domesticated species in the family Felidae.</p>
        </article>
        <div class="navbox">v t e Domestication of animals</div>
        <div class="catlinks">Categories: Domesticated animals Cats</div>
        <footer>This page was last edited on 19 March 2026</footer>
      </body>
    </html>
    """
    response = Response(
        status_code=200,
        reason="OK",
        headers={"content-type": "text/html; charset=utf-8"},
        body=html,
        url="https://example.com/wiki/cat",
    )
    rendered = to_text(response)
    assert "The cat is a domestic species" in rendered
    assert "Domestication of animals" not in rendered
    assert "This page was last edited" not in rendered


def test_boilerplate_removal_does_not_crash_on_large_pages():
    html = b"""
    <html><body>
      <div id="content">
        <p>Main article paragraph that should remain visible.</p>
      </div>
      <div class="navbox">v t e</div>
      <div class="catlinks">Categories: Example</div>
      <div class="authority-control">Authority control databases</div>
      <footer>Privacy policy</footer>
    </body></html>
    """
    response = Response(
        status_code=200,
        reason="OK",
        headers={"content-type": "text/html; charset=utf-8"},
        body=html,
        url="https://example.com/page",
    )
    rendered = to_text(response)
    assert "Main article paragraph" in rendered
