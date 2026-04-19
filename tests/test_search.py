from go2web.http_client import Response
from go2web.search import _parse_results_ddg, _parse_results_wikipedia, search


SAMPLE_DDG_HTML = """
<html>
  <body>
    <div class="result">
      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Result A</a>
      <div class="result__snippet">Snippet A</div>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.org/b">Result B</a>
      <div class="result__snippet">Snippet B</div>
    </div>
  </body>
</html>
"""

SAMPLE_WIKI_JSON = """
{
  "query": {
    "search": [
      {"title": "Cat", "snippet": "<span class='searchmatch'>Cat</span> article"},
      {"title": "Dog", "snippet": "Dog article"}
    ]
  }
}
"""


def test_parse_results_ddg():
    results = _parse_results_ddg(SAMPLE_DDG_HTML, limit=10)
    assert len(results) == 2
    assert results[0].title == "Result A"
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "Snippet A"
    assert results[0].rank == 1


def test_parse_results_ddg_respects_limit():
    results = _parse_results_ddg(SAMPLE_DDG_HTML, limit=1)
    assert len(results) == 1


def test_parse_results_wikipedia():
    results = _parse_results_wikipedia(SAMPLE_WIKI_JSON, limit=10)
    assert len(results) == 2
    assert results[0].title == "Cat"
    assert results[0].url.endswith("/Cat")
    assert "Cat" in results[0].snippet


def test_engine_routing_wikipedia():
    def fake_fetch(url, **kwargs):
        assert "w/api.php" in url
        return Response(
            status_code=200,
            reason="OK",
            headers={"content-type": "application/json"},
            body=SAMPLE_WIKI_JSON.encode("utf-8"),
            url=url,
        )

    results = search("cats", engine="wikipedia", fetcher=fake_fetch)
    assert len(results) >= 1
    assert results[0].rank == 1
