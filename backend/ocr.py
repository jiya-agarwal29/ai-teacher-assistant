"""
PDF-page rendering and image preparation for OCR.

Kept separate from llm.py (which only talks to the Gemini client) and
jobs.py (orchestration), so the pure image-handling pieces are independently
testable without a real PDF file, a real photo, or any network call.
"""
import io

import pillow_heif
import pypdfium2 as pdfium
from PIL import Image, ImageOps

# Lets PIL.Image.open() read .heic files (phone photos) -- without this,
# Pillow has no decoder for HEIC at all.
pillow_heif.register_heif_opener()

OCR_RENDER_DPI = 200
_PDFIUM_NATIVE_DPI = 72  # pypdfium2's render() scale=1.0 corresponds to 72 DPI
_MAX_LONGEST_SIDE_PX = 2000
_AUTOCONTRAST_CUTOFF = 1


def render_pdf_page_to_png(pdf_path: str, page_index: int, dpi: int = OCR_RENDER_DPI) -> bytes:
    """Renders one page (0-indexed) of a PDF file to PNG bytes at ~dpi."""
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        page = pdf[page_index]
        bitmap = page.render(scale=dpi / _PDFIUM_NATIVE_DPI)
        pil_image = bitmap.to_pil()
        buffer = io.BytesIO()
        pil_image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        pdf.close()


def prepare_image_for_ocr(image_bytes: bytes) -> bytes:
    """
    Normalizes an arbitrary input image (phone photo, scan, or a rendered
    PDF page) before sending it to Gemini for OCR: fixes phone-camera EXIF
    rotation, converts to plain RGB, downscales so the longest side is at
    most _MAX_LONGEST_SIDE_PX (keeps upload size and inference cost bounded
    without losing legibility), and lightly boosts contrast. Returns JPEG
    bytes. No OpenCV -- Pillow only, kept intentionally simple.
    """
    image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")

    longest_side = max(image.size)
    if longest_side > _MAX_LONGEST_SIDE_PX:
        scale = _MAX_LONGEST_SIDE_PX / longest_side
        new_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(new_size, Image.LANCZOS)

    image = ImageOps.autocontrast(image, cutoff=_AUTOCONTRAST_CUTOFF)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
