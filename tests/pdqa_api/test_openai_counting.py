import asyncio
import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi import UploadFile
from starlette.requests import Request

import pdqa_api.main as api
from pdqa_api.openai_counting import (
    SUPPORTED_MODELS,
    clear_count_cache,
    compare_api_counts,
    count_pdf_and_markdown,
)


@pytest.fixture(autouse=True)
def empty_cache():
    clear_count_cache()
    yield
    clear_count_cache()


def run_count(**kwargs):
    return asyncio.run(count_pdf_and_markdown(**kwargs))


def successful_transport(calls):
    def handler(request: httpx.Request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["model"] == "gpt-4.1"
        content = body["input"][0]["content"]
        assert content[0]["type"] == "input_text"
        assert "Do not summarize" in content[0]["text"]
        if content[1]["type"] == "input_file":
            assert content[1]["filename"] == "source.pdf"
            assert content[1]["file_data"].startswith("data:application/pdf;base64,")
            encoded = content[1]["file_data"].split(",", 1)[1]
            assert base64.b64decode(encoded) == b"%PDF-original"
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        assert content[1]["type"] == "input_text"
        assert content[1]["text"] == "# Markdown source"
        return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 70})
    return httpx.MockTransport(handler)


def base_args(**overrides):
    args = dict(pdf_bytes=b"%PDF-original", filename="source.pdf", markdown="# Markdown source",
                model="gpt-4.1", consent=True, api_key="test-key", endpoint="https://count.test/v1/responses/input_tokens")
    args.update(overrides)
    return args


def test_successful_pdf_and_markdown_counts_use_same_model_and_direct_inputs():
    calls = []
    result = run_count(**base_args(transport=successful_transport(calls)))
    assert len(calls) == 2
    assert result["originalPdf"]["inputTokens"] == 100
    assert result["markdown"]["inputTokens"] == 70
    assert result["model"] == "gpt-4.1"
    assert result["originalPdf"]["timestamp"] and result["markdown"]["timestamp"]
    assert result["status"] == "API verified"
    assert result["comparison"]["tokensSaved"] == 30
    assert result["comparison"]["reductionPercent"] == 30.0


@pytest.mark.parametrize("original,markdown,expected_saved,expected_percent", [
    (100, 130, -30, -30.0),
    (100, 70, 30, 30.0),
])
def test_positive_and_negative_comparison(original, markdown, expected_saved, expected_percent):
    result = compare_api_counts(original, markdown)
    assert result["tokensSaved"] == expected_saved
    assert result["reductionPercent"] == expected_percent


def test_zero_baseline_is_not_comparable():
    result = compare_api_counts(0, 12)
    assert result["status"] == "Not comparable"
    assert result["tokensSaved"] is None and result["reductionPercent"] is None


def test_successful_zero_pdf_count_keeps_per_input_api_status():
    def handler(request):
        body = json.loads(request.content)
        is_pdf = body["input"][0]["content"][1]["type"] == "input_file"
        return httpx.Response(200, json={"input_tokens": 0 if is_pdf else 12})

    result = run_count(**base_args(transport=httpx.MockTransport(handler)))
    assert result["status"] == "Not comparable"
    assert result["originalPdf"]["status"] == "API verified"
    assert result["markdown"]["status"] == "API verified"


def test_missing_consent_never_sends_data_even_when_key_is_set():
    def fail_if_called(_request):
        raise AssertionError("must not make an API request without consent")
    result = run_count(**base_args(consent=False, transport=httpx.MockTransport(fail_if_called)))
    assert result["status"] == "Waiting for consent"
    assert result["originalPdf"]["error"].startswith("API counting not enabled")


def test_missing_api_key_never_sends_data():
    def fail_if_called(_request):
        raise AssertionError("must not make an API request without a key")
    result = run_count(**base_args(api_key=None, transport=httpx.MockTransport(fail_if_called)))
    assert result["status"] == "API key missing"
    assert result["markdown"]["status"] == "API key missing"


def test_unsupported_model_is_rejected_before_network_request():
    def fail_if_called(_request):
        raise AssertionError("unsupported models must not be sent")
    result = run_count(**base_args(model="unknown-model", transport=httpx.MockTransport(fail_if_called)))
    assert result["status"] == "Unsupported model"
    assert "gpt-4.1" in SUPPORTED_MODELS


def test_timeout_is_reported_without_throwing():
    def timeout(_request):
        raise httpx.ReadTimeout("timeout")
    result = run_count(**base_args(transport=httpx.MockTransport(timeout), timeout=0.01))
    assert result["status"] == "API unavailable"
    assert "timed out" in result["originalPdf"]["error"]
    assert result["comparison"]["tokensSaved"] is None


@pytest.mark.parametrize("status", [429, 500])
def test_api_error_status_is_reported_without_fake_counts(status):
    transport = httpx.MockTransport(lambda _request: httpx.Response(status, json={"error": {"message": "private body"}}))
    result = run_count(**base_args(transport=transport))
    assert result["status"] == "API unavailable"
    assert result["originalPdf"]["inputTokens"] is None
    assert "private body" not in result["originalPdf"]["error"]


def test_successful_counts_are_cached_by_content_hash_and_model():
    calls = []
    transport = successful_transport(calls)
    first = run_count(**base_args(transport=transport))
    second = run_count(**base_args(transport=transport))
    assert len(calls) == 2
    assert first["originalPdf"]["cached"] is False
    assert second["originalPdf"]["cached"] is True
    assert second["markdown"]["cached"] is True


def test_api_counting_failure_keeps_local_markdown_response(tmp_path, monkeypatch):
    pdf = Path(__file__).parents[1] / "data" / "olmocr_bench" / "pdfs" / "multi_column_page1.pdf"
    monkeypatch.setattr(api, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(api, "run_marker_conversion", lambda _path, _config: ("# Local Markdown is available", 1))

    async def failed_count(**_kwargs):
        return {
            "originalPdf": {"status": "API unavailable", "inputTokens": None, "model": "gpt-4.1", "method": "test", "timestamp": None, "error": "offline", "cached": False},
            "markdown": {"status": "API unavailable", "inputTokens": None, "model": "gpt-4.1", "method": "test", "timestamp": None, "error": "offline", "cached": False},
            "comparison": {"status": "Not comparable", "tokensSaved": None, "reductionPercent": None, "reason": "API unavailable"},
            "status": "API unavailable", "model": "gpt-4.1", "method": "test",
        }

    monkeypatch.setattr(api, "count_pdf_and_markdown", failed_count)
    request = Request({"type": "http", "method": "POST", "path": "/api/convert", "headers": [], "query_string": b"", "state": {"request_id": "local1234"}})
    upload = UploadFile(file=io.BytesIO(pdf.read_bytes()), filename="local.pdf")
    response = asyncio.run(api.convert_pdf(request, upload, None, False, True, "gpt-4.1"))
    result = json.loads(response.body)["result"]
    assert response.status_code == 200
    assert result["success"] and result["markdown"] == "# Local Markdown is available"
    assert result["tokenCountingStatus"] == "API unavailable"
    assert result["originalPdfInputTokens"] is None and result["markdownInputTokens"] is None


def test_unexpected_api_counting_exception_keeps_local_markdown_response(tmp_path, monkeypatch):
    pdf = Path(__file__).parents[1] / "data" / "olmocr_bench" / "pdfs" / "multi_column_page1.pdf"
    monkeypatch.setattr(api, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(api, "run_marker_conversion", lambda _path, _config: ("# Local Markdown is available", 1))

    async def raise_count(**_kwargs):
        raise RuntimeError("counting implementation failure")

    monkeypatch.setattr(api, "count_pdf_and_markdown", raise_count)
    request = Request({"type": "http", "method": "POST", "path": "/api/convert", "headers": [], "query_string": b"", "state": {"request_id": "local1235"}})
    upload = UploadFile(file=io.BytesIO(pdf.read_bytes()), filename="local.pdf")
    response = asyncio.run(api.convert_pdf(request, upload, None, False, True, "gpt-4.1"))
    result = json.loads(response.body)["result"]
    assert response.status_code == 200
    assert result["success"] and result["markdown"] == "# Local Markdown is available"
    assert result["tokenCountingStatus"] == "API unavailable"
    assert result["originalPdfInputTokens"] is None and result["markdownInputTokens"] is None
