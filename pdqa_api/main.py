"""FastAPI wrapper for the local Marker PDF conversion engine."""

from __future__ import annotations

import logging
import os
import time
import uuid
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from dotenv import load_dotenv

from marker.converters.pdf import PdfConverter
from marker.config.parser import ConfigParser
from marker.models import create_model_dict
from marker.output import text_from_rendered
from marker.settings import settings as marker_settings
from pdqa_api.token_analysis import inspect_pdf_text
from pdqa_api.openai_counting import count_pdf_and_markdown, unavailable_count_result


logger = logging.getLogger("pdqa_api")
load_dotenv(Path(__file__).with_name(".env"), override=False)
app_data: dict = {}
history: list[dict] = []
_marker_lock = asyncio.Lock()
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def run_marker_conversion(upload_path: str, config_dict: dict):
    """Run synchronous Marker inference using the lifespan's shared models."""
    config_parser = ConfigParser(config_dict)
    converter = PdfConverter(
        config=config_dict,
        artifact_dict=app_data["models"],
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )
    rendered = converter(upload_path)
    markdown, _, _ = text_from_rendered(rendered)
    return markdown, getattr(converter, "page_count", None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize Marker artifacts once and reuse them across conversions."""
    started = time.perf_counter()
    logger.info("Marker model initialization started")
    app_data["models"] = create_model_dict()
    logger.info("Marker models initialized elapsed=%.3fs", time.perf_counter() - started)
    try:
        yield
    finally:
        if "models" in app_data:
            from marker.models import shutdown_models

            shutdown_models(app_data.pop("models"))


app = FastAPI(title="DocForge API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_request_lifecycle(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    started = time.perf_counter()
    logger.info("[%s] request received method=%s path=%s", request_id, request.method, request.url.path)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "[%s] response returned status=%d total_elapsed=%.3fs",
            request_id,
            response.status_code,
            time.perf_counter() - started,
        )
        return response
    except Exception:
        logger.exception("[%s] request exception total_elapsed=%.3fs", request_id, time.perf_counter() - started)
        raise


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "modelsLoaded": "models" in app_data,
        "device": str(marker_settings.TORCH_DEVICE_MODEL),
        "tokenCountingConfigured": bool(os.getenv("OPENAI_API_KEY")),
    }


class ConvertMeta(BaseModel):
    id: str
    fileName: str
    fileType: str
    originalSize: int
    pageCount: Optional[int] = None
    durationMs: int
    conversionDurationMs: int
    tokenCountingDurationMs: int
    markdown: str
    markdownCharacters: int
    markdownWords: int
    baselineCharacters: Optional[int] = None
    baselineWords: Optional[int] = None
    baselineAvailable: bool
    baselineStatus: str
    baselineReason: str
    extractionCoveragePercent: float
    extractedTextPageCount: int
    imagePageCount: int
    originalPdfInputTokens: Optional[int] = None
    markdownInputTokens: Optional[int] = None
    tokensSaved: Optional[int] = None
    reductionPercent: Optional[float] = None
    comparisonComparable: bool
    comparisonReason: Optional[str] = None
    tokenCountingStatus: str
    tokenCountingModel: str
    tokenCountingMethod: str
    originalPdfCount: dict
    markdownCount: dict
    mode: str
    forceOcr: bool
    success: bool
    error: Optional[str] = None


@app.post("/api/convert")
async def convert_pdf(
    request: Request,
    file: UploadFile = File(..., description="PDF file to convert"),
    mode: Optional[str] = Form(default=None),
    force_ocr: Optional[bool] = Form(default=False),
    count_with_openai: Optional[bool] = Form(default=False),
    target_model: Optional[str] = Form(default="gpt-4.1"),
):
    request_id = request.state.request_id
    started = time.perf_counter()
    filename = os.path.basename(file.filename or "document.pdf")
    upload_path = None
    marker_lock_acquired = False
    logger.info(
        "[%s] conversion handler started filename=%s mode=%s force_ocr=%s",
        request_id,
        filename,
        mode or "device-default",
        force_ocr or False,
    )

    try:
        stage_started = time.perf_counter()
        file_bytes = await file.read()
        file_size = len(file_bytes)
        logger.info("[%s] upload completed bytes=%d elapsed=%.3fs", request_id, file_size, time.perf_counter() - stage_started)

        file_ext = os.path.splitext(filename)[1].lstrip(".").lower() or "pdf"
        upload_path = os.path.join(UPLOAD_DIR, f"{request_id}_{filename}")
        stage_started = time.perf_counter()
        with open(upload_path, "wb") as target:
            target.write(file_bytes)
        logger.info("[%s] upload saved elapsed=%.3fs", request_id, time.perf_counter() - stage_started)

        lock_wait_started = time.perf_counter()
        await _marker_lock.acquire()
        marker_lock_acquired = True
        logger.info("[%s] conversion lock acquired wait=%.3fs", request_id, time.perf_counter() - lock_wait_started)

        # Inspect selectable text and image coverage without invoking OCR.
        stage_started = time.perf_counter()
        inspection = await run_in_threadpool(inspect_pdf_text, upload_path)
        baseline_available = bool(inspection.extracted_text)
        baseline_chars = inspection.extracted_characters if baseline_available else None
        baseline_words = len(inspection.extracted_text.split()) if baseline_available else None
        logger.info(
            "[%s] PDF text inspection finished pages=%d text_pages=%d image_pages=%d chars=%d status=%s elapsed=%.3fs",
            request_id,
            inspection.page_count, inspection.text_page_count, inspection.image_page_count,
            inspection.extracted_characters, inspection.baseline_status,
            time.perf_counter() - stage_started,
        )

        options = {
            "output_format": "markdown",
            "force_ocr": force_ocr or False,
            "paginate_output": False,
        }
        if mode:
            options["mode"] = mode
        config_parser = ConfigParser(options)
        config_dict = config_parser.generate_config_dict()
        # marker_single uses the provider's default of 4 pdftext workers. Match it
        # here; forcing one worker was a material API/CLI configuration mismatch.
        config_dict["pdftext_workers"] = 4
        safe_config = {
            key: ("[redacted]" if any(secret in key.lower() for secret in ("key", "token", "secret")) else value)
            for key, value in config_dict.items()
        }
        logger.info(
            "[%s] conversion started marker_config=%s effective_pdftext_workers=4",
            request_id,
            safe_config,
        )

        stage_started = time.perf_counter()
        text, page_count = await run_in_threadpool(run_marker_conversion, upload_path, config_dict)
        actual_mode = config_dict.get("mode", "unknown")
        logger.info(
            "[%s] conversion finished pages=%s markdown_chars=%d elapsed=%.3fs",
            request_id,
            page_count,
            len(text),
            time.perf_counter() - stage_started,
        )
        conversion_duration_ms = int((time.perf_counter() - stage_started) * 1000)

        stage_started = time.perf_counter()
        count_model = target_model or "gpt-4.1"
        try:
            token_count_result = await count_pdf_and_markdown(
                pdf_bytes=file_bytes,
                filename=filename,
                markdown=text,
                model=count_model,
                consent=count_with_openai is True,
                api_key=os.getenv("OPENAI_API_KEY"),
            )
        except Exception as count_exc:
            # Optional external counting must never discard a completed local conversion.
            logger.warning(
                "[%s] optional API counting failed; preserving local Markdown error_type=%s",
                request_id,
                type(count_exc).__name__,
            )
            token_count_result = unavailable_count_result(
                count_model, "Token counting failed unexpectedly; local Markdown remains available."
            )
        pdf_count = token_count_result["originalPdf"]
        markdown_count = token_count_result["markdown"]
        comparison = token_count_result["comparison"]
        token_counting_duration_ms = int((time.perf_counter() - stage_started) * 1000)
        result = ConvertMeta(
            id=request_id,
            fileName=filename,
            fileType=file_ext,
            originalSize=file_size,
            pageCount=page_count or inspection.page_count,
            durationMs=int((time.perf_counter() - started) * 1000),
            conversionDurationMs=conversion_duration_ms,
            tokenCountingDurationMs=token_counting_duration_ms,
            markdown=text,
            markdownCharacters=len(text),
            markdownWords=len(text.split()),
            baselineCharacters=baseline_chars,
            baselineWords=baseline_words,
            baselineAvailable=baseline_available,
            baselineStatus=inspection.baseline_status,
            baselineReason=inspection.baseline_reason,
            extractionCoveragePercent=inspection.coverage_percent,
            extractedTextPageCount=inspection.text_page_count,
            imagePageCount=inspection.image_page_count,
            originalPdfInputTokens=pdf_count["inputTokens"],
            markdownInputTokens=markdown_count["inputTokens"],
            tokensSaved=comparison["tokensSaved"],
            reductionPercent=comparison["reductionPercent"],
            comparisonComparable=comparison["status"] == "API verified",
            comparisonReason=comparison["reason"],
            tokenCountingStatus=token_count_result["status"],
            tokenCountingModel=token_count_result["model"],
            tokenCountingMethod=token_count_result["method"],
            originalPdfCount=pdf_count,
            markdownCount=markdown_count,
            mode=actual_mode,
            forceOcr=force_ocr or False,
            success=True,
        )
        logger.info("[%s] token metrics finished elapsed=%.3fs", request_id, time.perf_counter() - stage_started)
        history.insert(0, result.model_dump())
        logger.info(
            "[%s] JSON response prepared markdown_chars=%d conversion_elapsed=%.3fs",
            request_id,
            len(text),
            time.perf_counter() - started,
        )
        return JSONResponse(content={"success": True, "result": result.model_dump()})
    except Exception as exc:
        elapsed = time.perf_counter() - started
        logger.exception("[%s] conversion exception elapsed=%.3fs", request_id, elapsed)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(exc), "durationMs": int(elapsed * 1000)},
        )
    finally:
        if marker_lock_acquired:
            _marker_lock.release()
        if upload_path and os.path.exists(upload_path):
            try:
                os.remove(upload_path)
            except Exception:
                logger.exception("[%s] failed to remove uploaded temporary PDF", request_id)


@app.get("/api/history")
async def get_history():
    entries = [{k: v for k, v in item.items() if k != "markdown"} for item in history]
    return {"success": True, "entries": entries, "count": len(entries)}


@app.get("/api/history/{entry_id}")
async def get_history_entry(entry_id: str):
    for item in history:
        if item["id"] == entry_id:
            return {"success": True, "entry": item}
    return JSONResponse(status_code=404, content={"success": False, "error": "Not found"})
