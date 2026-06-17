# =============================================================================
# PH Agent Hub — Utility Tools Unit Tests
# =============================================================================
# Tests for built-in utility tool factories: datetime, weather, calculator,
# currency_exchange, web_search, wikipedia, fetch_url, rss_feed.
#
# All external API calls are mocked — no real network requests.
# =============================================================================

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

# ---------------------------------------------------------------------------
# Module markers — pure unit tests, no DB / no network
# ---------------------------------------------------------------------------
pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _make_mock_httpx_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
    headers: dict | None = None,
):
    """Return an AsyncMock that behaves like an httpx.Response.

    Note: ``response.json()``, ``raise_for_status()``, and ``.text`` are
    *synchronous* in httpx, so we use plain ``Mock`` for those.
    """
    mock = AsyncMock()
    mock.status_code = status_code
    mock.raise_for_status = Mock()
    mock.json = Mock(return_value=json_data or {})
    mock.text = text
    mock.headers = headers or {"content-type": "text/html"}
    # str(response.url) — used by fetch_url
    mock.url = "http://example.com"
    return mock


def _make_mock_httpx_client(mock_response):
    """Return an AsyncMock that behaves like an async context-manager
    httpx.AsyncClient with ``.get`` returning *mock_response*."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get = AsyncMock(return_value=mock_response)
    return client


# ===================================================================
# 1. Datetime tool
# ===================================================================
class TestDatetimeTool:
    """Tests for ``build_datetime_tools()`` — pure stdlib, no mocking."""

    @pytest.fixture
    def tool(self):
        """Return the ``current_time`` callable."""
        tools = __import__("src.tools.datetime", fromlist=["build_datetime_tools"])
        return tools.build_datetime_tools()[0]

    async def test_valid_timezone(self, tool):
        result = await tool(timezone="America/New_York")
        assert result["timezone"] == "America/New_York"
        assert "iso" in result
        assert "date" in result
        assert "time" in result
        assert "utc_offset" in result
        assert "day_of_week" in result
        assert "unix_timestamp" in result
        assert isinstance(result["unix_timestamp"], int)
        # ISO string should contain timezone info
        assert any(c in result["iso"] for c in ("+", "-", "Z"))

    async def test_invalid_timezone_falls_back_to_utc(self, tool):
        result = await tool(timezone="Mars/Colony")
        assert result["timezone"] == "UTC"

    async def test_default_timezone_from_config(self):
        mod = __import__("src.tools.datetime", fromlist=["build_datetime_tools"])
        (tool,) = mod.build_datetime_tools({"default_timezone": "Asia/Tokyo"})
        result = await tool()  # no arg → use config default
        assert result["timezone"] == "Asia/Tokyo"

    async def test_no_arg_uses_default_utc(self, tool):
        result = await tool()
        assert result["timezone"] == "UTC"

    async def test_returns_all_expected_keys(self, tool):
        result = await tool(timezone="Europe/London")
        assert set(result.keys()) == {
            "iso", "date", "time", "timezone",
            "utc_offset", "day_of_week", "unix_timestamp",
        }


# ===================================================================
# 2. Weather tool
# ===================================================================
class TestWeatherTool:
    """Tests for ``build_weather_tools()`` — mocks ``httpx.AsyncClient``."""

    @pytest.fixture
    def tool(self):
        mod = __import__("src.tools.weather", fromlist=["build_weather_tools"])
        return mod.build_weather_tools()[0]

    FAKE_WTTR_JSON = {
        "current_condition": [
            {
                "temp_C": "18",
                "temp_F": "64",
                "FeelsLikeC": "17",
                "humidity": "65",
                "pressure": "1015",
                "visibility": "10",
                "uvIndex": "3",
                "windspeedKmph": "12",
                "winddir16Point": "NW",
                "weatherDesc": [{"value": "Partly cloudy"}],
            }
        ],
        "nearest_area": [
            {
                "areaName": [{"value": "London"}],
                "country": [{"value": "United Kingdom"}],
            }
        ],
        "weather": [
            {
                "date": "2025-06-17",
                "mintempC": "12",
                "maxtempC": "20",
                "avgtempC": "16",
                "sunHour": "8.5",
                "hourly": [
                    {"time": "300", "tempC": "14",
                     "weatherDesc": [{"value": "Clear"}], "chanceofrain": "5"}
                ],
            }
        ],
    }

    async def test_success_parses_response(self, tool):
        mock_resp = _make_mock_httpx_response(
            status_code=200, json_data=self.FAKE_WTTR_JSON
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.weather.httpx.AsyncClient", return_value=mock_client):
            result = await tool(location="London")

        assert result["source"] == "wttr.in"
        assert result["location"] == "London"
        assert result["current"]["temp_C"] == 18.0
        assert result["current"]["humidity"] == 65.0
        assert result["current"]["weatherDesc"] == "Partly cloudy"
        assert len(result["forecast"]) > 0
        assert result["forecast"][0]["date"] == "2025-06-17"
        assert result["forecast"][0]["hourly"][0]["tempC"] == 14.0

    async def test_timeout_returns_error(self, tool):
        mock_client = _make_mock_httpx_client(None)
        mock_client.get.side_effect = __import__("httpx").TimeoutException(
            "timeout"
        )

        with patch("src.tools.weather.httpx.AsyncClient", return_value=mock_client):
            result = await tool(location="London")

        assert "error" in result
        assert "timed out" in result["error"].lower()

    async def test_http_status_error_returns_error(self, tool):
        httpx_mod = __import__("httpx")
        mock_resp = _make_mock_httpx_response(status_code=404)
        mock_resp.raise_for_status.side_effect = httpx_mod.HTTPStatusError(
            "Not Found", request=Mock(), response=mock_resp
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.weather.httpx.AsyncClient", return_value=mock_client):
            result = await tool(location="Unknown")

        assert "error" in result

    async def test_generic_exception_returns_error(self, tool):
        mock_client = _make_mock_httpx_client(None)
        mock_client.get.side_effect = RuntimeError("unexpected failure")

        with patch("src.tools.weather.httpx.AsyncClient", return_value=mock_client):
            result = await tool(location="London")

        assert "error" in result

    async def test_custom_timeout_from_config(self):
        mod = __import__("src.tools.weather", fromlist=["build_weather_tools"])
        (tool,) = mod.build_weather_tools({"timeout": 5.0})

        mock_resp = _make_mock_httpx_response(
            status_code=200, json_data=self.FAKE_WTTR_JSON
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.weather.httpx.AsyncClient", return_value=mock_client) as mock_cls:
            result = await tool(location="Paris")

        # Tool should succeed
        assert result["source"] == "wttr.in"
        # Verify the client was constructed with the custom timeout
        assert mock_cls.called
        args, kwargs = mock_cls.call_args
        assert kwargs.get("timeout") == 5.0


# ===================================================================
# 3. Calculator tool
# ===================================================================
class TestCalculatorTool:
    """Tests for ``build_calculator_tools()`` — pure stdlib, no mocking."""

    @pytest.fixture
    def tool(self):
        mod = __import__("src.tools.calculator", fromlist=["build_calculator_tools"])
        return mod.build_calculator_tools()[0]

    async def test_basic_arithmetic(self, tool):
        result = await tool(expression="2 + 3 * 4")
        assert result["result"] == 14
        assert result["result_type"] == "int"

    async def test_float_result(self, tool):
        result = await tool(expression="10 / 3")
        assert isinstance(result["result"], float)
        assert result["result_type"] == "float"
        assert result["result"] == pytest.approx(3.333, rel=1e-3)

    async def test_exponentiation_with_caret(self, tool):
        result = await tool(expression="2 ^ 3")
        assert result["result"] == 8

    async def test_math_functions(self, tool):
        assert (await tool(expression="sqrt(16)"))["result"] == 4.0
        assert (await tool(expression="sin(pi/2)"))["result"] == pytest.approx(1.0)
        assert (await tool(expression="log10(1000)"))["result"] == 3.0
        assert (await tool(expression="abs(-5)"))["result"] == 5

    async def test_constants(self, tool):
        pi = (await tool(expression="pi"))["result"]
        e = (await tool(expression="e"))["result"]
        tau = (await tool(expression="tau"))["result"]
        assert pi == pytest.approx(3.14159, rel=1e-4)
        assert e == pytest.approx(2.71828, rel=1e-4)
        assert tau == pytest.approx(6.28318, rel=1e-4)

    async def test_min_max_sum(self, tool):
        assert (await tool(expression="min(3, 1, 2)"))["result"] == 1
        assert (await tool(expression="max(5, 9, 2)"))["result"] == 9
        assert (await tool(expression="sum([1, 2, 3])"))["result"] == 6

    async def test_factorial_gcd(self, tool):
        assert (await tool(expression="factorial(5)"))["result"] == 120
        assert (await tool(expression="gcd(48, 18)"))["result"] == 6

    async def test_syntax_error_returns_error(self, tool):
        result = await tool(expression="2 +* 3")
        assert "error" in result

    async def test_division_by_zero_returns_error(self, tool):
        result = await tool(expression="1 / 0")
        assert "error" in result

    async def test_disallowed_function(self, tool):
        result = await tool(expression="__import__('os')")
        assert "error" in result

    async def test_disallowed_variable(self, tool):
        result = await tool(expression="x + 5")
        assert "error" in result

    async def test_unary_negation(self, tool):
        result = await tool(expression="-5 + 3")
        assert result["result"] == -2

    async def test_floor_div_and_mod(self, tool):
        assert (await tool(expression="17 // 5"))["result"] == 3
        assert (await tool(expression="17 % 5"))["result"] == 2


# ===================================================================
# 4. Currency Exchange tool
# ===================================================================
class TestCurrencyExchangeTool:
    """Tests for ``build_currency_exchange_tools()`` — mocks ``httpx.AsyncClient``."""

    @pytest.fixture
    def tools(self):
        mod = __import__(
            "src.tools.currency_exchange", fromlist=["build_currency_exchange_tools"]
        )
        convert, rates = mod.build_currency_exchange_tools()
        return {"convert": convert, "rates": rates}

    FRANKFURTER_CONVERT = {
        "amount": 100,
        "base": "USD",
        "date": "2025-01-15",
        "rates": {"EUR": 0.92},
    }

    FRANKFURTER_RATES = {
        "amount": 1,
        "base": "EUR",
        "date": "2025-01-15",
        "rates": {"USD": 1.08, "GBP": 0.85, "JPY": 130.5},
    }

    async def test_convert_success(self, tools):
        mock_resp = _make_mock_httpx_response(
            status_code=200, json_data=self.FRANKFURTER_CONVERT
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch(
            "src.tools.currency_exchange.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await tools["convert"](
                amount=100, from_currency="USD", to_currency="EUR"
            )

        assert result["amount"] == 100
        assert result["from_currency"] == "USD"
        assert result["to_currency"] == "EUR"
        assert result["result"] == pytest.approx(0.92 * 100, rel=1e-4)
        assert result["rate"] == 0.92
        assert result["date"] == "2025-01-15"

    async def test_get_exchange_rates_success(self, tools):
        mock_resp = _make_mock_httpx_response(
            status_code=200, json_data=self.FRANKFURTER_RATES
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch(
            "src.tools.currency_exchange.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await tools["rates"](base="EUR")

        assert result["base"] == "EUR"
        assert result["date"] == "2025-01-15"
        assert "USD" in result["rates"]
        assert result["rate_count"] == 3

    async def test_convert_timeout(self, tools):
        mock_client = _make_mock_httpx_client(None)
        mock_client.get.side_effect = __import__("httpx").TimeoutException(
            "timeout"
        )

        with patch(
            "src.tools.currency_exchange.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await tools["convert"](
                amount=100, from_currency="USD", to_currency="EUR"
            )

        assert "error" in result
        assert "timed out" in result["error"].lower()

    async def test_convert_http_error(self, tools):
        httpx_mod = __import__("httpx")
        mock_resp = _make_mock_httpx_response(status_code=400)
        mock_resp.raise_for_status.side_effect = httpx_mod.HTTPStatusError(
            "Bad Request", request=Mock(), response=mock_resp
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch(
            "src.tools.currency_exchange.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await tools["convert"](
                amount=100, from_currency="XXX", to_currency="YYY"
            )

        assert "error" in result

    async def test_get_rates_timeout(self, tools):
        mock_client = _make_mock_httpx_client(None)
        mock_client.get.side_effect = __import__("httpx").TimeoutException(
            "timeout"
        )

        with patch(
            "src.tools.currency_exchange.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await tools["rates"](base="EUR")

        assert "error" in result

    async def test_custom_base_currency_from_config(self):
        mod = __import__(
            "src.tools.currency_exchange", fromlist=["build_currency_exchange_tools"]
        )
        convert, rates = mod.build_currency_exchange_tools({"base_currency": "GBP"})

        mock_resp = _make_mock_httpx_response(
            status_code=200, json_data=self.FRANKFURTER_RATES
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch(
            "src.tools.currency_exchange.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await rates()  # no arg → uses config default "GBP"

        assert result["base"] == "EUR"  # API returns the actual base
        # Verify the URL called used from=GBP
        call_url = mock_client.get.call_args[0][0]
        assert "from=GBP" in call_url or "from=gbp" in call_url.lower()

    async def test_generic_exception_convert(self, tools):
        mock_client = _make_mock_httpx_client(None)
        mock_client.get.side_effect = RuntimeError("something went wrong")

        with patch(
            "src.tools.currency_exchange.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await tools["convert"](
                amount=100, from_currency="USD", to_currency="EUR"
            )

        assert "error" in result


# ===================================================================
# 5. Web Search tool
# ===================================================================
class TestWebSearchTool:
    """Tests for ``build_web_search_tools()`` — mocks ``ddgs.DDGS`` inside
    ``_do_search`` so the actual search helper runs."""

    @pytest.fixture
    def tool(self):
        mod = __import__("src.tools.web_search", fromlist=["build_web_search_tools"])
        return mod.build_web_search_tools()[0]

    FAKE_DDGS_RESULTS = [
        {
            "title": "Result 1",
            "href": "https://example.com",
            "body": "Snippet text for result 1",
            "source": "duckduckgo",
        },
        {
            "title": "Result 2",
            "href": "https://example.org",
            "body": "Snippet text for result 2",
            "source": "duckduckgo",
        },
    ]

    @staticmethod
    def _make_ddgs_mock(return_value=None, side_effect=None):
        """Create a mock for the ``DDGS`` class used in ``_do_search``."""
        ddgs_instance = Mock()
        ddgs_instance.text = Mock(return_value=return_value, side_effect=side_effect)
        ddgs_cls = Mock(return_value=ddgs_instance)
        return ddgs_cls

    async def test_search_success_with_results(self, tool):
        ddgs_cls = self._make_ddgs_mock(return_value=self.FAKE_DDGS_RESULTS)
        with patch("ddgs.DDGS", ddgs_cls):
            result = await tool(query="test query")

        assert result["query"] == "test query"
        assert len(result["results"]) == 2
        assert result["result_count"] == 2
        assert result["backend_used"] == "duckduckgo"

    async def test_search_empty_results(self, tool):
        ddgs_cls = self._make_ddgs_mock(return_value=[])
        with patch("ddgs.DDGS", ddgs_cls):
            result = await tool(query="nonexistent")

        assert result["result_count"] == 0
        assert result["results"] == []

    async def test_search_exception_returns_error(self, tool):
        with patch("src.tools.web_search.asyncio.to_thread",
                   side_effect=RuntimeError("search down")):
            result = await tool(query="broken")

        assert "error" in result
        assert result["result_count"] == 0

    async def test_custom_config_params(self):
        mod = __import__("src.tools.web_search", fromlist=["build_web_search_tools"])
        (tool,) = mod.build_web_search_tools({
            "max_results": 5,
            "region": "uk-en",
            "safesearch": "on",
        })

        ddgs_cls = self._make_ddgs_mock(return_value=[])
        with patch("ddgs.DDGS", ddgs_cls) as mock_ddgs:
            await tool(query="config test")

        # Verify DDGS.text() received the config defaults
        instance = mock_ddgs.return_value
        instance.text.assert_called_once_with(
            query="config test", region="uk-en", safesearch="on",
            timelimit=None, max_results=5, backend="auto",
        )

    async def test_parameter_override(self, tool):
        ddgs_cls = self._make_ddgs_mock(return_value=[])
        with patch("ddgs.DDGS", ddgs_cls) as mock_ddgs:
            await tool(query="test", max_results=3, region="de-de")

        instance = mock_ddgs.return_value
        instance.text.assert_called_once_with(
            query="test", region="de-de", safesearch="moderate",
            timelimit=None, max_results=3, backend="auto",
        )

    async def test_body_truncation(self, tool):
        """Results with body exceeding MAX_SNIPPET_LENGTH get truncated."""
        long_body = "x" * 500
        results = [{"title": "Long", "href": "https://example.com",
                     "body": long_body, "source": "ddg"}]
        ddgs_cls = self._make_ddgs_mock(return_value=results)
        with patch("ddgs.DDGS", ddgs_cls):
            result = await tool(query="long body")

        assert len(result["results"][0]["snippet"]) <= 301  # 300 + "…"

    async def test_backend_used_fallback(self, tool):
        """When results have no 'source' field, backend_used defaults to input."""
        results_no_source = [{"title": "A", "href": "https://a.com", "body": "aaa"}]
        ddgs_cls = self._make_ddgs_mock(return_value=results_no_source)
        with patch("ddgs.DDGS", ddgs_cls):
            result = await tool(query="test", backend="auto")
        assert result["backend_used"] == "auto"


# ===================================================================
# 6. Wikipedia tool
# ===================================================================
class TestWikipediaTool:
    """Tests for ``build_wikipedia_tools()`` — mocks ``httpx.AsyncClient``."""

    @pytest.fixture
    def tools(self):
        mod = __import__(
            "src.tools.wikipedia", fromlist=["build_wikipedia_tools"]
        )
        search, summary = mod.build_wikipedia_tools()
        return {"search": search, "summary": summary}

    SUMMARY_RESPONSE = {
        "title": "Python (programming language)",
        "pageid": 23862,
        "description": "General-purpose programming language",
        "extract": "Python is a high-level, general-purpose programming language.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Python_(programming_language)"}},
    }

    SEARCH_RESPONSE = {
        "pages": [
            {
                "id": 23862,
                "title": "Python (programming language)",
                "description": "General-purpose programming language",
                "excerpt": "Python is a high-level...",
                "key": "Python_(programming_language)",
            }
        ]
    }

    async def _make_wiki_client(self, summary_data=None, search_data=None):
        """Create a mock httpx client for Wikipedia's two-call search pattern."""
        summary_resp = _make_mock_httpx_response(
            status_code=200, json_data=summary_data or self.SUMMARY_RESPONSE
        )
        search_resp = _make_mock_httpx_response(
            status_code=200, json_data=search_data or self.SEARCH_RESPONSE
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        # First call → summary, second call → search
        client.get = AsyncMock(side_effect=[summary_resp, search_resp])
        return client

    async def test_search_success(self, tools):
        mock_client = await self._make_wiki_client()

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await tools["search"](query="Python")

        assert result["query"] == "Python"
        assert len(result["results"]) > 0
        assert result["result_count"] > 0
        assert result["results"][0]["title"] == "Python (programming language)"

    async def test_search_404_falls_back_to_title(self, tools):
        """First call raises 404, second call (title endpoint) succeeds."""
        httpx_mod = __import__("httpx")
        err_resp = _make_mock_httpx_response(status_code=404)
        err_resp.raise_for_status.side_effect = httpx_mod.HTTPStatusError(
            "Not Found", request=Mock(), response=err_resp
        )
        success_resp = _make_mock_httpx_response(
            status_code=200, json_data=self.SUMMARY_RESPONSE
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[err_resp, success_resp, success_resp])

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=client):
            result = await tools["search"](query="Python")

        assert len(result["results"]) > 0

    async def test_search_404_both_fail(self, tools):
        """Both summary and title endpoints return 404."""
        httpx_mod = __import__("httpx")
        err_resp = _make_mock_httpx_response(status_code=404)
        err_resp.raise_for_status.side_effect = httpx_mod.HTTPStatusError(
            "Not Found", request=Mock(), response=err_resp
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[err_resp, err_resp])

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=client):
            result = await tools["search"](query="NonexistentArticleXYZ")

        assert len(result["results"]) == 0
        assert "error" in result

    async def test_search_http_error(self, tools):
        httpx_mod = __import__("httpx")
        err_resp = _make_mock_httpx_response(status_code=500)
        err_resp.raise_for_status.side_effect = httpx_mod.HTTPStatusError(
            "Server Error", request=Mock(), response=err_resp
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=err_resp)

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=client):
            result = await tools["search"](query="Python")

        assert "error" in result

    async def test_search_timeout(self, tools):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.side_effect = __import__("httpx").TimeoutException("timeout")

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=client):
            result = await tools["search"](query="Python")

        assert "error" in result

    async def test_summary_success(self, tools):
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={
                **self.SUMMARY_RESPONSE,
                "thumbnail": {"source": "https://upload.wikimedia.org/thumb.png"},
            },
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await tools["summary"](title="Python_(programming_language)")

        assert result["title"] == "Python (programming language)"
        assert result["page_id"] == 23862
        assert "extract" in result
        assert result["thumbnail"] == "https://upload.wikimedia.org/thumb.png"

    async def test_summary_not_found(self, tools):
        httpx_mod = __import__("httpx")
        err_resp = _make_mock_httpx_response(status_code=404)
        err_resp.raise_for_status.side_effect = httpx_mod.HTTPStatusError(
            "Not Found", request=Mock(), response=err_resp
        )
        mock_client = _make_mock_httpx_client(err_resp)

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await tools["summary"](title="Nonexistent")

        assert "error" in result

    async def test_summary_truncation(self):
        mod = __import__(
            "src.tools.wikipedia", fromlist=["build_wikipedia_tools"]
        )
        _, summary = mod.build_wikipedia_tools({"max_extract_chars": 50})

        long_extract = "x" * 200
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={**self.SUMMARY_RESPONSE, "extract": long_extract},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await summary(title="Long")

        assert result["truncated"] is True
        assert len(result["extract"]) <= 50

    async def test_custom_language_config(self):
        mod = __import__(
            "src.tools.wikipedia", fromlist=["build_wikipedia_tools"]
        )
        search, _ = mod.build_wikipedia_tools({"language": "de"})

        mock_resp = _make_mock_httpx_response(
            status_code=200, json_data=self.SUMMARY_RESPONSE
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=mock_client):
            await search(query="Python")

        # The URL called should point to de.wikipedia.org
        call_url = mock_client.get.call_args[0][0]
        assert "de.wikipedia.org" in call_url

    async def test_search_duplicate_dedup(self, tools):
        """Results with same page_id as summary are not duplicated."""
        summary_data = {
            "title": "Python",
            "pageid": 42,
            "description": "Lang",
            "extract": "Python is...",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Python"}},
        }
        search_data = {
            "pages": [
                {"id": 42, "title": "Python", "description": "Lang",
                 "excerpt": "Python is...", "key": "Python"},
                {"id": 99, "title": "Monty", "description": "Comedy",
                 "excerpt": "Monty Python...", "key": "Monty"},
            ]
        }
        mock_client = await self._make_wiki_client(summary_data, search_data)

        with patch("src.tools.wikipedia.httpx.AsyncClient", return_value=mock_client):
            result = await tools["search"](query="Python")

        # Should have 2 unique results (not 3)
        assert result["result_count"] == 2
        page_ids = [r["page_id"] for r in result["results"]]
        assert page_ids == [42, 99]


# ===================================================================
# 7. Fetch URL tool
# ===================================================================
class TestFetchUrlTool:
    """Tests for ``build_fetch_url_tools()`` — mocks ``httpx.AsyncClient``
    and ``html2text``."""

    @pytest.fixture
    def tool(self):
        mod = __import__("src.tools.fetch_url", fromlist=["build_fetch_url_tools"])
        return mod.build_fetch_url_tools()[0]

    HTML_BODY = "<html><head><title>Test Page</title></head><body><p>Hello world</p></body></html>"

    async def test_html_content_success(self, tool):
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={},
            text=self.HTML_BODY,
            headers={"content-type": "text/html"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.fetch_url.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.fetch_url.html2text.HTML2Text") as mock_html2text,
        ):
            mock_converter = Mock()
            mock_converter.handle.return_value = "Hello world"
            mock_html2text.return_value = mock_converter

            result = await tool(url="https://example.com")

        assert result["url"] is not None
        assert result["status_code"] == 200
        assert result["title"] == "Test Page"
        assert "Hello world" in result["text"]
        assert result["truncated"] is False

    async def test_non_html_content(self, tool):
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={},
            text="plain text response",
            headers={"content-type": "text/plain"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.fetch_url.httpx.AsyncClient", return_value=mock_client):
            result = await tool(url="https://example.com/file.txt")

        assert result["text"] == "plain text response"
        assert result["title"] == ""

    async def test_unsafe_url_localhost(self, tool):
        result = await tool(url="http://localhost:8080")
        assert "error" in result
        assert "not allowed" in result["error"].lower()

    async def test_unsafe_url_private_ip_192_168(self, tool):
        result = await tool(url="http://192.168.1.1/admin")
        assert "error" in result

    async def test_unsafe_url_private_ip_10_dot(self, tool):
        result = await tool(url="http://10.0.0.1/config")
        assert "error" in result

    async def test_unsafe_url_private_ip_172(self, tool):
        result = await tool(url="http://172.16.0.1/admin")
        assert "error" in result

    async def test_timeout_returns_error(self, tool):
        mock_client = _make_mock_httpx_client(None)
        mock_client.get.side_effect = __import__("httpx").TimeoutException(
            "timeout"
        )

        with patch("src.tools.fetch_url.httpx.AsyncClient", return_value=mock_client):
            result = await tool(url="https://example.com")

        assert "error" in result

    async def test_request_error(self, tool):
        mock_client = _make_mock_httpx_client(None)
        mock_client.get.side_effect = __import__("httpx").RequestError("connection failed")

        with patch("src.tools.fetch_url.httpx.AsyncClient", return_value=mock_client):
            result = await tool(url="https://example.com")

        assert "error" in result

    async def test_html2text_conversion_failure(self, tool):
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={},
            text=self.HTML_BODY,
            headers={"content-type": "text/html"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.fetch_url.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.fetch_url.html2text.HTML2Text") as mock_html2text,
        ):
            mock_converter = Mock()
            mock_converter.handle.side_effect = ValueError("conversion failed")
            mock_html2text.return_value = mock_converter

            result = await tool(url="https://example.com")

        assert "error" in result

    async def test_content_truncation(self):
        mod = __import__("src.tools.fetch_url", fromlist=["build_fetch_url_tools"])
        (tool,) = mod.build_fetch_url_tools({"max_content_length": 100})

        long_text = "Hello world " * 50  # > 100 chars
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={},
            text=long_text,
            headers={"content-type": "text/plain"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.fetch_url.httpx.AsyncClient", return_value=mock_client):
            result = await tool(url="https://example.com/long")

        assert result["truncated"] is True
        assert len(result["text"]) <= 200  # max_len * 2 due to split display

    async def test_custom_user_agent(self):
        mod = __import__("src.tools.fetch_url", fromlist=["build_fetch_url_tools"])
        (tool,) = mod.build_fetch_url_tools({"user_agent": "MyBot/2.0"})

        mock_resp = _make_mock_httpx_response(
            status_code=200, json_data={}, text="ok",
            headers={"content-type": "text/plain"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.fetch_url.httpx.AsyncClient", return_value=mock_client):
            await tool(url="https://example.com")

        # Verify the User-Agent header was sent
        call_headers = mock_client.get.call_args.kwargs.get("headers", {})
        assert call_headers.get("User-Agent") == "MyBot/2.0"

    async def test_follow_redirects_enabled(self, tool):
        mock_resp = _make_mock_httpx_response(
            status_code=200, json_data={}, text="ok",
            headers={"content-type": "text/plain"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.fetch_url.httpx.AsyncClient", return_value=mock_client):
            await tool(url="https://example.com")

        # The client.get should be called with follow_redirects=True
        assert mock_client.get.call_args.kwargs.get("follow_redirects") is True

    async def test_unsafe_url_loopback_ipv6(self, tool):
        result = await tool(url="http://[::1]:8080")
        assert "error" in result

    async def test_unsafe_url_zero_dot(self, tool):
        result = await tool(url="http://0.0.0.0/health")
        assert "error" in result

    async def test_unsafe_url_link_local(self, tool):
        result = await tool(url="http://169.254.1.1/config")
        assert "error" in result


# ===================================================================
# 8. RSS Feed tool
# ===================================================================
class TestRssFeedTool:
    """Tests for ``build_rss_feed_tools()`` — mocks ``asyncio.to_thread``
    and ``feedparser``."""

    async def test_missing_feed_url_config(self):
        """When no feed_url is configured, the tool returns an error."""
        mod = __import__("src.tools.rss_feed", fromlist=["build_rss_feed_tools"])
        (tool,) = mod.build_rss_feed_tools()  # no config
        result = await tool()
        assert "error" in result
        assert "not configured" in result["error"].lower()

    @pytest.fixture
    def tool_with_feed(self):
        mod = __import__("src.tools.rss_feed", fromlist=["build_rss_feed_tools"])
        (tool,) = mod.build_rss_feed_tools({
            "feed_url": "https://example.com/feed.xml",
            "max_entries": 20,
        })
        return tool

    @staticmethod
    def _entry(**kwargs):
        """Build a feedparser-style entry that supports both
        ``.get()`` (dict-like) and attribute access (``.summary``)."""
        return type("FeedEntry", (), {
            "get": lambda self, k, default=None: getattr(self, k, default),
            **kwargs,
        })()

    FAKE_FEED = {
        "feed": {
            "title": "Example Feed",
            "link": "https://example.com",
            "subtitle": "An example feed",
        },
        "entries": [
            _entry.__func__(
                title="Post 1",
                link="https://example.com/post1",
                published="2025-01-15T10:00:00Z",
                summary="Summary of post 1",
                author="Author 1",
            ),
            _entry.__func__(
                title="Post 2",
                link="https://example.com/post2",
                published="2025-01-14T09:00:00Z",
                summary="Summary of post 2",
                author="Author 2",
            ),
        ],
        "bozo": 0,
    }

    async def test_success_with_entries(self, tool_with_feed):
        with patch("src.tools.rss_feed.asyncio.to_thread",
                   return_value=self.FAKE_FEED):
            result = await tool_with_feed()

        assert result["feed_title"] == "Example Feed"
        assert result["feed_link"] == "https://example.com"
        assert result["entry_count"] == 2
        assert result["entries"][0]["title"] == "Post 1"
        assert result["entries"][1]["author"] == "Author 2"

    async def test_bozo_error_no_entries(self, tool_with_feed):
        bozo_feed = {
            "feed": {},
            "entries": [],
            "bozo": 1,
            "bozo_exception": "syntax error",
        }
        with patch("src.tools.rss_feed.asyncio.to_thread",
                   return_value=bozo_feed):
            result = await tool_with_feed()

        assert "error" in result
        assert "parse error" in result["error"].lower()

    async def test_parse_exception(self, tool_with_feed):
        with patch("src.tools.rss_feed.asyncio.to_thread",
                   side_effect=Exception("network error")):
            result = await tool_with_feed()

        assert "error" in result

    async def test_max_entries_override(self, tool_with_feed):
        feed_many_entries = {
            "feed": {"title": "Many Feed"},
            "entries": [
                self._entry(
                    title=f"Post {i}", link=f"https://example.com/{i}",
                    published="", summary=f"Summary {i}", author=""
                )
                for i in range(10)
            ],
            "bozo": 0,
        }
        with patch("src.tools.rss_feed.asyncio.to_thread",
                   return_value=feed_many_entries):
            result = await tool_with_feed(max_entries_override=3)

        assert result["entry_count"] == 3

    async def test_html_stripping_in_summary(self, tool_with_feed):
        feed_with_html = {
            "feed": {"title": "HTML Feed"},
            "entries": [
                self._entry(
                    title="Post",
                    link="https://example.com/post",
                    published="2025-01-15T10:00:00Z",
                    summary="<p>Hello <b>World</b></p>",
                    author="",
                )
            ],
            "bozo": 0,
        }
        with patch("src.tools.rss_feed.asyncio.to_thread",
                   return_value=feed_with_html):
            result = await tool_with_feed()

        assert "<b>" not in result["entries"][0]["summary"]
        assert "Hello" in result["entries"][0]["summary"]
        assert "World" in result["entries"][0]["summary"]

    async def test_empty_feed_info(self, tool_with_feed):
        minimal_feed = {
            "feed": {},
            "entries": [
                self._entry(
                    title="Only Post",
                    link="https://example.com/only",
                    published="",
                    summary="",
                    author="",
                )
            ],
            "bozo": 0,
        }
        with patch("src.tools.rss_feed.asyncio.to_thread",
                   return_value=minimal_feed):
            result = await tool_with_feed()

        assert result["feed_title"] == ""
        assert result["feed_link"] == ""
        assert result["entry_count"] == 1
        assert result["entries"][0]["title"] == "Only Post"

    async def test_custom_timeout_config(self):
        """Custom timeout in config is accepted without error."""
        mod = __import__("src.tools.rss_feed", fromlist=["build_rss_feed_tools"])
        (tool,) = mod.build_rss_feed_tools({
            "feed_url": "https://example.com/feed.xml",
            "timeout": 30.0,
        })

        with patch("src.tools.rss_feed.asyncio.to_thread",
                   return_value=self.FAKE_FEED):
            result = await tool()

        assert "error" not in result
        assert result["entry_count"] == 2

    async def test_updated_used_when_no_published(self, tool_with_feed):
        """When 'published' is missing, 'updated' is used instead."""
        feed = {
            "feed": {"title": "Test"},
            "entries": [
                self._entry(
                    title="Post",
                    link="https://example.com/p",
                    updated="2025-06-01T12:00:00Z",
                    summary="Test",
                    author="",
                )
            ],
            "bozo": 0,
        }
        with patch("src.tools.rss_feed.asyncio.to_thread", return_value=feed):
            result = await tool_with_feed()

        assert result["entries"][0]["published"] == "2025-06-01T12:00:00Z"
