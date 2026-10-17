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


def open_pdf(pdf_path_or_bytes):
    """
    Opens a PDF once for rendering multiple pages from it (see render_page
    below) -- a scanned PDF with many low-text pages used to call
    pdfium.PdfDocument(...) (and so re-parse the whole file) once per page;
    opening it once up front and reusing the same handle removes that
    per-page reparse, which matters for peak memory on a large file
    (Phase 7C item 6). Caller is responsible for calling .close() on the
    returned document once done with every page.
    """
    return pdfium.PdfDocument(pdf_path_or_bytes)


def _dpi_for_page(page, requested_dpi: int) -> int:
    """
    Caps the render DPI so an unusually large page (a huge physical page
    size, or a PDF already created at a very high DPI) never renders to a
    bitmap bigger than _MAX_LONGEST_SIDE_PX on its longest side -- the same
    cap prepare_image_for_ocr already applies after the fact, applied
    before the render instead so the oversized intermediate bitmap is
    never allocated in the first place (Phase 7C item 6).
    """
    width_pt, height_pt = page.get_size()
    longest_pt = max(width_pt, height_pt)
    longest_px_at_requested = longest_pt / _PDFIUM_NATIVE_DPI * requested_dpi
    if longest_px_at_requested <= _MAX_LONGEST_SIDE_PX:
        return requested_dpi
    return max(1, round(requested_dpi * _MAX_LONGEST_SIDE_PX / longest_px_at_requested))


def render_page(pdf, page_index: int, dpi: int = OCR_RENDER_DPI) -> bytes:
    """Renders one page (0-indexed) of an already-open PDF (see open_pdf) to PNG bytes at ~dpi (capped for a very large page -- see _dpi_for_page)."""
    page = pdf[page_index]
    try:
        effective_dpi = _dpi_for_page(page, dpi)
        bitmap = page.render(scale=effective_dpi / _PDFIUM_NATIVE_DPI)
        try:
            pil_image = bitmap.to_pil()
            try:
                buffer = io.BytesIO()
                pil_image.save(buffer, format="PNG")
                return buffer.getvalue()
            finally:
                pil_image.close()
        finally:
            bitmap.close()
    finally:
        page.close()


def render_pdf_page_to_png(pdf_path_or_bytes, page_index: int, dpi: int = OCR_RENDER_DPI) -> bytes:
    """
    Renders one page (0-indexed) of a PDF to PNG bytes at ~dpi -- a single-
    page convenience wrapper around open_pdf()/render_page() for a caller
    that only ever needs one page from a given PDF (e.g. main.py's old-
    paper-import flow). Processing every page of the same PDF in a loop
    should call open_pdf() once and render_page() per page instead, so the
    file isn't re-opened (and re-parsed) once per page -- see jobs.py's
    _extract_pdf_as_pages.
    """
    pdf = open_pdf(pdf_path_or_bytes)
    try:
        return render_page(pdf, page_index, dpi=dpi)
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


# PIL's ROTATE_90 turns the image 90 degrees counter-clockwise (what was on
# the right edge ends up on top) -- i.e. a "rotate left"; ROTATE_270 is the
# other way around, a "rotate right". Verified empirically, not just by the
# PIL docs' wording, since getting this backwards would be a confusing bug.
_ROTATE_TRANSPOSE = {
    "left": Image.Transpose.ROTATE_90,
    "right": Image.Transpose.ROTATE_270,
}


def rotate_image(image_bytes: bytes, direction: str) -> bytes:
    """
    Rotates a saved page image 90 degrees "left" (counter-clockwise) or
    "right" (clockwise) -- used by the review page's Rotate buttons when a
    page image was saved sideways/upside down. Returns JPEG bytes, matching
    the format every page image is already saved in (see jobs.py's
    _save_page_image).
    """
    if direction not in _ROTATE_TRANSPOSE:
        raise ValueError(f"Unknown rotate direction '{direction}'; expected 'left' or 'right'.")

    image = Image.open(io.BytesIO(image_bytes))
    image = image.convert("RGB")
    image = image.transpose(_ROTATE_TRANSPOSE[direction])

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
