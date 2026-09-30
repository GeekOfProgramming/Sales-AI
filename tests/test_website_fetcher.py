import pytest
from unittest.mock import patch, MagicMock
from sales_engine.web.website_fetcher import WebsiteFetcher

def test_fetch_invalid_url():
    fetcher = WebsiteFetcher()
    with pytest.raises(ValueError):
        fetcher.fetch_website_content("this_is_not_a_url")

def test_fetch_inaccessible_url():
    fetcher = WebsiteFetcher()
    with pytest.raises(ValueError):
        fetcher.fetch_website_content("http://localhost:55555/not-exist")

@patch('sales_engine.web.website_fetcher.Fetcher.get')
def test_same_domain_filtering(mock_get):
    fetcher = WebsiteFetcher()
    
    class MockResponse:
        def __init__(self, body):
            self.body = body.encode("utf-8")
            
    def get_mock(url, **kwargs):
        if url.endswith("/about"):
            return MockResponse("<html><body>About Us page content here</body></html>")
        elif url.endswith("/contact"):
            return MockResponse("<html><body>Contact Us page content here</body></html>")
        return MockResponse("""
        <html>
            <body>
                <a href="/about">About</a>
                <a href="https://external.com/services">External</a>
                <a href="http://example.com/contact">Contact</a>
            </body>
        </html>
        """)
        
    mock_get.side_effect = get_mock
    
    result = fetcher.fetch_website_content("http://example.com")
    pages = result["pages"]
    
    urls = [p["url"] for p in pages]
    assert "http://example.com" in urls
    assert "http://example.com/about" in urls
    assert "http://example.com/contact" in urls
    assert "https://external.com/services" not in urls

@patch('sales_engine.web.website_fetcher.Fetcher.get')
def test_anchor_text_priority(mock_get):
    fetcher = WebsiteFetcher()
    
    class MockResponse:
        def __init__(self, body):
            self.body = body.encode("utf-8")
            
    def get_mock(url, **kwargs):
        if url.endswith("/page1"):
            return MockResponse("<html><body>Page 1 specific text</body></html>")
        elif url.endswith("/page2"):
            return MockResponse("<html><body>Page 2 specific services text</body></html>")
        return MockResponse("""
        <html>
            <body>
                <a href="/page1">Random Page</a>
                <a href="/page2">Our Services</a>
            </body>
        </html>
        """)
        
    mock_get.side_effect = get_mock
    
    result = fetcher.fetch_website_content("http://example.com")
    pages = result["pages"]
    urls = [p["url"] for p in pages]
    
    assert urls[0] == "http://example.com"
    assert urls[1] == "http://example.com/page2"
    assert urls[2] == "http://example.com/page1"

@patch('sales_engine.web.website_fetcher.Fetcher.get')
@patch('sales_engine.web.website_fetcher.MAX_TOTAL_CHARS', 50)
def test_max_total_chars(mock_get):
    fetcher = WebsiteFetcher()
    
    class MockResponse:
        def __init__(self, body):
            self.body = body.encode("utf-8")
            
    # Text that is too large
    html = "<html><body>" + "A" * 60 + "</body></html>"
    mock_get.return_value = MockResponse(html)
    
    result = fetcher.fetch_website_content("http://example.com")
    pages = result["pages"]
    
    assert "TRUNCATED DUE TO GLOBAL LIMIT" in pages[0]["text"]
