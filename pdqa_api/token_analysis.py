"""Conservative local analysis of extracted PDF text and generated Markdown."""
from __future__ import annotations

from dataclasses import dataclass


MIN_PAGE_CHARS = 20
MIN_AVERAGE_CHARS_PER_PAGE = 100


@dataclass(frozen=True)
class PdfTextInspection:
    page_count: int
    extracted_text: str
    extracted_characters: int
    text_page_count: int
    image_page_count: int
    coverage_percent: float
    baseline_status: str
    baseline_reason: str


def inspect_pdf_text(path: str) -> PdfTextInspection:
    """Read selectable text and detect embedded image pages without running OCR."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfraw

    document = pdfium.PdfDocument(path)
    try:
        if len(document) < 1:
            raise ValueError("PDF contains no pages")
        page_texts: list[str] = []
        image_pages = 0
        text_pages = 0
        covered_pages = 0
        for page in document:
            text_page = page.get_textpage()
            text = text_page.get_text_range() or ""
            page_texts.append(text)
            has_text = len(text.strip()) >= MIN_PAGE_CHARS
            if has_text:
                text_pages += 1
            try:
                images = page.get_objects(filter=[pdfraw.FPDF_PAGEOBJ_IMAGE])
                has_image = next(iter(images), None) is not None
                if has_image:
                    image_pages += 1
            except Exception:
                # Keep text counts, but the page will be treated as incomplete below.
                has_image = True
            if has_text and not has_image:
                covered_pages += 1
            text_page.close()
            page.close()
        joined = "\n\n".join(page_texts).strip()
        count = len(document)
        coverage = round(covered_pages * 100 / count, 1)
        if not joined:
            status, reason = "unavailable", "No selectable text was found; scanned/image text is not counted without OCR."
        elif len(joined) / count < MIN_AVERAGE_CHARS_PER_PAGE:
            status, reason = "incomplete", f"Only {len(joined)} selectable characters were found across {count} page(s); this is too little to trust as complete source text."
        elif text_pages < count or image_pages:
            status, reason = "incomplete", f"Text was found on {text_pages} of {count} pages; {image_pages} page(s) contain images or image inspection was inconclusive."
        else:
            status, reason = "complete", "Selectable text was found on every page and no embedded image pages were detected."
        return PdfTextInspection(count, joined, len(joined), text_pages, image_pages, coverage, status, reason)
    finally:
        document.close()
