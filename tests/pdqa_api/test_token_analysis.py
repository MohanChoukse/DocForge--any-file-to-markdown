from pathlib import Path

import pytest
from PIL import Image

from pdqa_api.token_analysis import inspect_pdf_text

PDFS = Path(__file__).parents[1] / "data" / "olmocr_bench" / "pdfs"


def make_pdf(path: Path, text: str = "", image: bool = False):
    """Write a small real PDF fixture with selectable text and/or a raster page."""
    image_bytes = b""
    image_entry = b""
    image_draw = b""
    if image:
        img = Image.new("RGB", (20, 20), "white")
        from io import BytesIO
        output = BytesIO()
        img.save(output, format="JPEG")
        image_bytes = output.getvalue()
        image_entry = b"/XObject << /Im1 4 0 R >>"
        image_draw = b"q 200 0 0 200 50 40 cm /Im1 Do Q\n"
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    text_draw = f"BT /F1 12 Tf 30 270 Td ({escaped}) Tj ET\n".encode("ascii") if text else b""
    content = image_draw + text_draw
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 5 0 R >> " + image_entry + b" >> /Contents 6 0 R >>",
        b"<< /Type /XObject /Subtype /Image /Width 20 /Height 20 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length " + str(len(image_bytes)).encode() + b" >>\nstream\n" + image_bytes + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    data.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    path.write_bytes(data)


@pytest.mark.parametrize("filename,expected_status,expected_images,expected_coverage", [
    ("headers_footers_page1.pdf", "incomplete", 1, 0.0),
    ("long_tiny_text_pg36.pdf", "incomplete", 1, 0.0),
    ("multi_column_page1.pdf", "complete", 0, 100.0),
])
def test_repository_pdf_fixtures_inspect_page_text_and_images(filename, expected_status, expected_images, expected_coverage):
    report = inspect_pdf_text(str(PDFS / filename))
    assert report.page_count == 1
    assert report.extracted_characters > 0
    assert report.baseline_status == expected_status
    assert report.image_page_count == expected_images
    assert report.coverage_percent == expected_coverage


def test_invalid_pdf_is_reported_as_inspection_failure(tmp_path):
    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"not a pdf")
    with pytest.raises(Exception):
        inspect_pdf_text(str(invalid))


def test_text_only_pdf_has_complete_selectable_text(tmp_path):
    pdf = tmp_path / "selectable.pdf"
    make_pdf(pdf, "This is selectable source text with enough characters for analysis, and the deliberately detailed sentence exceeds one hundred characters so the source page is not considered suspiciously sparse by the coverage check.")
    report = inspect_pdf_text(str(pdf))
    assert report.baseline_status == "complete"
    assert report.extracted_characters > 20 and report.coverage_percent == 100


def test_scanned_image_pdf_has_unavailable_source_baseline(tmp_path):
    pdf = tmp_path / "scanned.pdf"
    make_pdf(pdf, image=True)
    report = inspect_pdf_text(str(pdf))
    assert report.baseline_status == "unavailable"
    assert report.extracted_characters == 0 and report.image_page_count == 1


def test_mixed_image_and_text_pdf_marks_extraction_incomplete(tmp_path):
    pdf = tmp_path / "mixed.pdf"
    make_pdf(pdf, "Selectable text is present alongside a scanned table image.", image=True)
    report = inspect_pdf_text(str(pdf))
    assert report.baseline_status == "incomplete" and report.image_page_count == 1
    assert report.extracted_characters > 0


def test_suspiciously_small_selectable_text_is_incomplete(tmp_path):
    pdf = tmp_path / "suspiciously-small.pdf"
    make_pdf(pdf, "Only 73 characters or less were extracted from this source page.")
    report = inspect_pdf_text(str(pdf))
    assert report.extracted_characters < 100
    assert report.baseline_status == "incomplete"
