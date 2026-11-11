"""
Fix round 1, Problem log rows 1/3/25: document_parsers.py's running header/
footer detection, PDF table extraction, PPTX table extraction (including
inside group shapes), and table-aware chunking (a whole table stays in one
chunk when it fits; a table too big for one chunk repeats its header row
in every piece it's split into).

PDF fixtures are built with reportlab in the test itself -- real PDFs, not
a mocked parse_pdf -- so these tests actually exercise pdfplumber's own
text/table extraction, not just the orchestration around it.
"""
import io

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, Preformatted, Table, TableStyle
from reportlab.platypus.doctemplate import BaseDocTemplate, PageTemplate
from reportlab.platypus.frames import Frame
from pptx import Presentation
from pptx.util import Inches

import document_parsers as dp
import jobs

HEADER_TEXT = "Object Oriented Programming (C++) | Class Notes"
CODE_CLOSING_BRACE_LINE = "};"


def _draw_header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    canvas.drawString(72, 740, HEADER_TEXT)
    canvas.drawCentredString(300, 30, f"-- Page {doc.page} --")
    canvas.restoreState()


def _build_typed_notes_pdf(num_units: int = 3) -> bytes:
    """
    A multi-page "typed class notes" PDF: a running header/footer (footer
    includes a page number, so it's literally different text on every
    page), a heading containing a number ("Unit N: ..." -- must NOT be
    mistaken for a header/footer just because it repeats a numbered
    pattern), body paragraphs, bulleted lines, and a short code line
    ("};") repeated across pages the way real closing-brace lines in
    programming notes commonly are. A grid-drawn table follows on its own
    page. No page is scanned/image-only -- every page has a real,
    plentiful text layer.
    """
    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=letter)
    frame = Frame(72, 72, 450, 650, id="normal")
    doc.addPageTemplates([PageTemplate(id="with_header_footer", frames=[frame], onPage=_draw_header_footer)])

    styles = getSampleStyleSheet()
    story = []

    for unit in range(1, num_units + 1):
        story.append(Paragraph(f"Unit {unit}: Classes and Objects", styles["Heading1"]))
        story.append(Paragraph(
            ("Object-oriented programming organizes software around objects rather than "
             "functions and logic. Encapsulation, inheritance, and polymorphism are the "
             "three pillars that make this approach powerful for real-world modeling. ") * 3,
            styles["BodyText"]
        ))
        bullets = "\n".join(f"- Key concept {i}: a short explanation of idea {i}." for i in range(1, 6))
        story.append(Preformatted(bullets, styles["Code"]))
        code_block = (
            "class Animal {\n"
            "public:\n"
            "    virtual void speak() {\n"
            "        std::cout << \"...\" << std::endl;\n"
            "    }\n"
            f"{CODE_CLOSING_BRACE_LINE}"
        )
        story.append(Preformatted(code_block, styles["Code"]))
        story.append(PageBreak())

    table_data = [["Concept", "Example", "Notes"]] + [
        [f"Row {i}", f"example_{i}()", f"Explanation text for row {i} with enough words to matter here."]
        for i in range(1, 6)
    ]
    table = Table(table_data, repeatRows=0)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    story.append(table)
    story.append(Paragraph("Additional notes after the table with real sentences for length here.", styles["BodyText"]))

    doc.build(story)
    return buffer.getvalue()


def _build_pdf_with_blank_last_page() -> bytes:
    """A typed first page followed by one page with no text at all (just a drawn shape) -- the "image-only page" case."""
    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=letter)
    frame = Frame(72, 72, 450, 650, id="normal")
    doc.addPageTemplates([PageTemplate(id="plain", frames=[frame])])

    styles = getSampleStyleSheet()
    story = [
        Paragraph(
            ("This page has a completely normal, plentiful text layer describing the "
             "topic in full sentences so it clears the OCR_MIN_CHARS threshold easily. ") * 4,
            styles["BodyText"]
        ),
        PageBreak(),
    ]
    doc.build(story)
    pdf_bytes = buffer.getvalue()

    # Append a second, text-free page by re-opening with pypdfium2 would be
    # overkill here -- simplest is to build the "blank" page as its own
    # single-page PDF via a bare canvas (a drawn rectangle, no text calls
    # at all) and merge the two with pypdfium2's import_pages, which is
    # already a project dependency.
    import pypdfium2 as pdfium
    from reportlab.pdfgen import canvas as rl_canvas

    blank_buffer = io.BytesIO()
    c = rl_canvas.Canvas(blank_buffer, pagesize=letter)
    c.rect(100, 100, 200, 200)  # a shape, but no text -- nothing for pdfplumber to extract
    c.showPage()
    c.save()

    merged = pdfium.PdfDocument(pdf_bytes)
    blank_doc = pdfium.PdfDocument(blank_buffer.getvalue())
    merged.import_pages(blank_doc, pages=[0])
    out_buffer = io.BytesIO()
    merged.save(out_buffer)
    return out_buffer.getvalue()


# --- Running header/footer detection ---

def test_running_header_footer_lines_detects_header_and_footer_with_page_number():
    pages_lines = []
    for i in range(1, 6):
        pages_lines.append([
            HEADER_TEXT,
            f"Unit {i}: topic heading",
            "Some real body text about the topic here with several words of content.",
            CODE_CLOSING_BRACE_LINE,
            f"-- Page {i} --",
        ])
    detected = dp._running_header_footer_lines(pages_lines)
    assert dp._normalize_for_repeat_detection(HEADER_TEXT) in detected
    assert dp._normalize_for_repeat_detection("-- Page 1 --") in detected


def test_running_header_footer_lines_does_not_flag_repeated_code_line():
    pages_lines = [
        [HEADER_TEXT, "Unit 1: topic", "body text", CODE_CLOSING_BRACE_LINE, "-- Page 1 --"],
        [HEADER_TEXT, "Unit 2: topic", "body text", CODE_CLOSING_BRACE_LINE, "-- Page 2 --"],
        [HEADER_TEXT, "Unit 3: topic", "body text", CODE_CLOSING_BRACE_LINE, "-- Page 3 --"],
    ]
    detected = dp._running_header_footer_lines(pages_lines)
    assert CODE_CLOSING_BRACE_LINE not in detected


def test_running_header_footer_lines_does_not_flag_numbered_heading():
    """A heading like "Unit 3: ..." must not be treated as a repeated footer just because it contains a different number on every page."""
    pages_lines = [
        [HEADER_TEXT, f"Unit {i}: Classes and Objects", "body text", f"-- Page {i} --"]
        for i in range(1, 6)
    ]
    detected = dp._running_header_footer_lines(pages_lines)
    assert "Unit #: Classes and Objects" not in detected
    for i in range(1, 6):
        assert f"Unit {i}: Classes and Objects" not in detected


# --- parse_pdf: the real Problem log row 1 regression ---

def test_typed_pdf_pages_all_clear_ocr_threshold():
    """Every page of a well-formed typed PDF (header/footer, code, bullets) must have enough real text left after cleanup to never trigger OCR."""
    pdf_bytes = _build_typed_notes_pdf()
    pages = dp.parse_pdf(io.BytesIO(pdf_bytes))
    assert len(pages) >= 3
    for page in pages:
        assert len(page["content"].strip()) >= jobs.OCR_MIN_CHARS, (
            f"page {page['page_number']} fell under OCR_MIN_CHARS: {page['content']!r}"
        )


def test_typed_pdf_header_and_footer_are_removed():
    pdf_bytes = _build_typed_notes_pdf()
    pages = dp.parse_pdf(io.BytesIO(pdf_bytes))
    for page in pages:
        assert HEADER_TEXT not in page["content"]
        assert "-- Page" not in page["content"]


def test_typed_pdf_code_block_lines_survive():
    pdf_bytes = _build_typed_notes_pdf()
    pages = dp.parse_pdf(io.BytesIO(pdf_bytes))
    assert any(CODE_CLOSING_BRACE_LINE in page["content"] for page in pages)


def test_typed_pdf_bullets_survive():
    pdf_bytes = _build_typed_notes_pdf()
    pages = dp.parse_pdf(io.BytesIO(pdf_bytes))
    assert any("- Key concept 1:" in page["content"] for page in pages)


def test_typed_pdf_table_text_is_present_as_pipe_rows():
    pdf_bytes = _build_typed_notes_pdf()
    pages = dp.parse_pdf(io.BytesIO(pdf_bytes))
    table_page = pages[-1]
    assert "Concept | Example | Notes" in table_page["content"]
    assert "Row 1 | example_1() | Explanation text for row 1" in table_page["content"]


def test_pdf_with_image_only_page_leaves_that_page_under_ocr_threshold():
    pdf_bytes = _build_pdf_with_blank_last_page()
    pages = dp.parse_pdf(io.BytesIO(pdf_bytes))
    assert len(pages) == 2
    assert len(pages[0]["content"].strip()) >= jobs.OCR_MIN_CHARS
    assert len(pages[1]["content"].strip()) < jobs.OCR_MIN_CHARS


# --- PPTX tables (Problem log row 3) ---

def _build_pptx_with_table(inside_group: bool = False) -> bytes:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout

    rows, cols = 4, 4
    left, top, width, height = Inches(1), Inches(1), Inches(6), Inches(3)

    table_shape = slide.shapes.add_table(rows, cols, left, top, width, height)
    if inside_group:
        # python-pptx builds a group by moving already-placed shapes into
        # one, not by adding a table directly under a group -- so add the
        # table at top level first, then group it.
        slide.shapes.add_group_shape([table_shape])

    table = table_shape.table
    for r in range(rows):
        for c in range(cols):
            table.cell(r, c).text = f"R{r}C{c}"

    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def test_pptx_table_rows_all_present():
    pptx_bytes = _build_pptx_with_table(inside_group=False)
    pages = dp.parse_pptx(io.BytesIO(pptx_bytes))
    content = pages[0]["content"]
    for r in range(4):
        row_line = " | ".join(f"R{r}C{c}" for c in range(4))
        assert row_line in content


def test_pptx_table_inside_group_shape_is_found():
    pptx_bytes = _build_pptx_with_table(inside_group=True)
    pages = dp.parse_pptx(io.BytesIO(pptx_bytes))
    content = pages[0]["content"]
    header_row = " | ".join(f"R0C{c}" for c in range(4))
    assert header_row in content


# --- Table-aware chunking (Problem log row 25) ---

def test_chunk_keeps_whole_small_table_in_one_chunk():
    content = "Header | Col2 | Col3\nRow1 | a | b\nRow2 | c | d\n\nSome normal text here with a few words."
    chunks = dp.chunk_parsed_document([{"page_number": 1, "content": content}])
    table_chunks = [c for c in chunks if "Header | Col2 | Col3" in c["content"]]
    assert len(table_chunks) == 1
    assert "Row1 | a | b" in table_chunks[0]["content"]
    assert "Row2 | c | d" in table_chunks[0]["content"]


def test_chunk_repeats_header_when_table_must_be_split():
    header = "Name | Score | Comment"
    rows = [header] + [f"Student{i} | {i * 2} | some comment text here number {i}" for i in range(1, 60)]
    content = "\n".join(rows)
    chunks = dp.chunk_parsed_document([{"page_number": 1, "content": content}], chunk_size=150)

    table_chunks = [c for c in chunks if "Score | Comment" in c["content"]]
    assert len(table_chunks) > 1, "this table should have needed more than one chunk"
    for chunk in table_chunks:
        assert chunk["content"].startswith(header), (
            f"chunk did not start with the table header: {chunk['content'][:60]!r}"
        )


def test_chunk_does_not_mix_table_split_pieces_with_unrelated_text():
    """Each table-split chunk is self-contained -- it never has leftover non-table text glued in front of the repeated header."""
    header = "Name | Score | Comment"
    rows = [header] + [f"Student{i} | {i * 2} | comment number {i} with a little extra padding text" for i in range(1, 60)]
    content = "Some short intro line before the table.\n" + "\n".join(rows)
    chunks = dp.chunk_parsed_document([{"page_number": 1, "content": content}], chunk_size=150)

    table_chunks = [c for c in chunks if "Score | Comment" in c["content"]]
    assert len(table_chunks) > 1
    for chunk in table_chunks:
        assert chunk["content"].startswith(header)
