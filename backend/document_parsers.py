import io
import re
import collections
import shutil
import subprocess
import tempfile
from pathlib import Path

import pdfplumber
import docx
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


class LegacyFormatUnsupportedError(ValueError):
    """Raised when a legacy .doc/.ppt file can't be converted because LibreOffice isn't installed."""


def _convert_legacy_office_file(file_bytes: bytes, ext: str):
    """
    Converts a legacy .doc/.ppt file to its modern .docx/.pptx equivalent using
    LibreOffice's headless CLI (`soffice`). Returns (converted_bytes, new_ext).
    Raises LegacyFormatUnsupportedError if `soffice` isn't installed, or if the
    conversion itself fails.
    """
    if shutil.which("soffice") is None:
        raise LegacyFormatUnsupportedError(
            "Please save this file as .docx/.pptx and upload again."
        )

    target_format = "docx" if ext == ".doc" else "pptx"

    with tempfile.TemporaryDirectory() as tmp_dir:
        source_path = Path(tmp_dir) / f"input{ext}"
        source_path.write_bytes(file_bytes)

        result = subprocess.run(
            [
                "soffice", "--headless", "--norestore",
                "--convert-to", target_format,
                "--outdir", tmp_dir,
                str(source_path)
            ],
            capture_output=True,
            timeout=120
        )

        converted_path = source_path.with_suffix(f".{target_format}")
        if result.returncode != 0 or not converted_path.exists():
            raise LegacyFormatUnsupportedError(
                "Please save this file as .docx/.pptx and upload again."
            )

        return converted_path.read_bytes(), f".{target_format}"


_DIGIT_RUN = re.compile(r'\d+')

# A line is only treated as "page-number-flavored" (and so digit-normalized
# before checking for repeats -- see _normalize_for_repeat_detection) if it
# contains the word "page", an "of N" (as in "3 of 45"), or is basically
# just a decorated number ("- 3 -", "3"). A real content heading that
# happens to contain a number ("Unit 3: Classes and Objects") must NOT
# match this -- it's genuinely different text on every page (by design),
# not a page number, and digit-normalizing it would make it look like a
# repeated header/footer and wrongly strip it from every page.
_PAGE_NUMBER_FLAVORED = re.compile(r'page|of\s*\d+|^[\s\-–—.:|]*\d+[\s\-–—.:|]*$', re.IGNORECASE)

# How many lines from the very top/bottom of a page count as "header/footer
# position" for _running_header_footer_lines below.
_HEADER_FOOTER_EDGE_WINDOW = 2


def _normalize_for_repeat_detection(line: str) -> str:
    """
    For a page-number-flavored line (see _PAGE_NUMBER_FLAVORED), collapses
    any run of digits to a single "#" so a footer like "... Page 3" / "...
    Page 47" -- a different literal line on every single page -- still
    counts as the same repeated line when checking for a running header/
    footer. Left alone otherwise, so a real heading that happens to
    contain a number is never conflated with one that merely differs by
    its page number.
    """
    if _PAGE_NUMBER_FLAVORED.search(line):
        return _DIGIT_RUN.sub('#', line)
    return line


def _running_header_footer_lines(pages_lines: list) -> set:
    """
    Finds lines that look like a running header/footer: the same (digit-
    normalized) text repeated near the very top or bottom of most pages.

    Deliberately restricted to each page's first/last _HEADER_FOOTER_EDGE_
    WINDOW lines, not the whole page -- a real header/footer repeats in a
    fixed position; a short line of code or boilerplate (e.g. "};",
    "public:", a single "{") can easily repeat across many pages of a
    programming-notes document too, but never in a fixed top/bottom
    position the way an actual header does. The old version checked every
    line on the page with no position or length floor, so it would (and
    did) strip a repeated short code line from every page it appeared on
    -- on a code-heavy page with little other text, enough of its real
    content to fall under jobs.OCR_MIN_CHARS, sending a perfectly typed
    page through Gemini vision OCR instead of using its own text layer.
    """
    num_pages = len(pages_lines)
    if num_pages <= 2:
        return set()

    normalized_counts = collections.Counter()
    for lines in pages_lines:
        edge_lines = set(lines[:_HEADER_FOOTER_EDGE_WINDOW]) | set(lines[-_HEADER_FOOTER_EDGE_WINDOW:])
        for line in edge_lines:
            normalized_counts[_normalize_for_repeat_detection(line)] += 1

    return {
        normalized for normalized, count in normalized_counts.items()
        # >30% of pages, same threshold as before; a short floor (3 chars)
        # keeps a lone brace/bullet that happens to sit at a page edge from
        # ever being mistaken for a real header/footer.
        if count / num_pages > 0.3 and len(normalized.strip()) >= 3
    }


def _extract_table_lines(page) -> list:
    """
    Pipe-joins each row of every table pdfplumber finds on this page
    ("cell | cell | cell", header row first -- same convention parse_docx/
    parse_pptx already use for their own tables) so a PDF table survives
    into the indexed text instead of only being visible as pdfplumber's
    own best-effort (often garbled, column-order-losing) plain extract_
    text() reading of the same cells. Returned as one block of lines per
    table; appended after the page's plain text rather than spliced back
    into its exact reading position, which pdfplumber doesn't make cheap
    to do reliably -- a table's cell text ends up indexed twice (once
    plain, once as a clean row), a reasonable trade for not losing the
    table's structure at all.
    """
    table_blocks = []
    for table in page.extract_tables() or []:
        rows = []
        for row in table:
            cells = [(cell or "").strip().replace("\n", " ") for cell in row]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            table_blocks.append(rows)
    return table_blocks


def parse_pdf(file_file):
    """
    Extracts text page-by-page from a PDF. Per page: pdfplumber's own text
    layer (with running headers/footers stripped -- see
    _running_header_footer_lines) plus any tables found on that page,
    pipe-joined. Returns a list of dicts; opens the PDF once.
    """
    pages_lines = []  # list of (raw, non-empty) lines per page
    pages_table_blocks = []  # list of (list of pipe-joined-row lists) per page

    with pdfplumber.open(file_file) as pdf:
        num_pages = len(pdf.pages)
        for page in pdf.pages:
            text = page.extract_text()
            lines = []
            if text:
                for line in text.split('\n'):
                    line_strip = line.strip()
                    if line_strip:
                        lines.append(line_strip)
            pages_lines.append(lines)
            pages_table_blocks.append(_extract_table_lines(page))

    header_footer_lines = _running_header_footer_lines(pages_lines)

    pages_data = []
    for page_idx, lines in enumerate(pages_lines):
        cleaned_lines = []
        for line_strip in lines:
            # Skip a running header/footer (digit-normalized match).
            if _normalize_for_repeat_detection(line_strip) in header_footer_lines:
                continue
            # Skip a standalone page number ("3", "Page 3", "3 of 45").
            if re.match(r'^(page\s+)?\d+(\s+of\s+\d+)?$', line_strip, re.IGNORECASE):
                continue
            # Spacing normalization
            line_clean = re.sub(r'[ \t]+', ' ', line_strip)
            cleaned_lines.append(line_clean)

        content_parts = []
        if cleaned_lines:
            content_parts.append("\n".join(cleaned_lines))
        for table_rows in pages_table_blocks[page_idx]:
            content_parts.append("\n".join(table_rows))

        pages_data.append({
            "page_number": page_idx + 1,
            "content": "\n\n".join(content_parts)
        })

    return pages_data

def parse_docx(file_file):
    """
    Parses a DOCX file using python-docx. Preserves section and heading hierarchy
    using Markdown formatting (# for Heading 1, etc.).
    """
    doc = docx.Document(file_file)
    paragraphs = []

    # Build element->object lookups once instead of rescanning doc.paragraphs /
    # doc.tables for every body element (was O(n*m) on large files).
    paragraph_by_element = {p._element: p for p in doc.paragraphs}
    table_by_element = {t._element: t for t in doc.tables}

    # Process elements: paragraphs and tables
    for element in doc.element.body:
        if element.tag.endswith('p'):
            p = paragraph_by_element.get(element)
            if p is None:
                continue

            text = p.text.strip()
            if not text:
                continue

            style_name = p.style.name if p.style else ""
            if style_name.startswith('Heading'):
                match = re.match(r'Heading\s*(\d+)', style_name, re.IGNORECASE)
                level = int(match.group(1)) if match else 1
                prefix = "#" * level
                text = f"{prefix} {text}"
            elif style_name.startswith('List'):
                # Add a bullet
                text = f"- {text}"

            paragraphs.append(text)
        elif element.tag.endswith('tbl'):
            t = table_by_element.get(element)
            if t is None:
                continue

            table_rows = []
            for row in t.rows:
                row_cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                table_rows.append(" | ".join(row_cells))
            if table_rows:
                paragraphs.append("\n" + "\n".join(table_rows) + "\n")

    # Group into virtual pages/sections of ~300 words
    pages_data = []
    current_page = []
    current_word_count = 0
    page_idx = 1
    
    for para in paragraphs:
        current_page.append(para)
        current_word_count += len(para.split())
        
        if current_word_count >= 300:
            pages_data.append({
                "page_number": page_idx,
                "content": "\n\n".join(current_page)
            })
            current_page = []
            current_word_count = 0
            page_idx += 1
            
    if current_page:
        pages_data.append({
            "page_number": page_idx,
            "content": "\n\n".join(current_page)
        })
        
    return pages_data

def _iter_shapes_recursive(shapes):
    """Yields every shape in `shapes`, descending into group shapes too -- a table or text box nested inside a group is otherwise invisible to a flat loop over slide.shapes."""
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes_recursive(shape.shapes)


def _table_rows_as_lines(table) -> list:
    """Pipe-joins each row of a pptx table, header row first (table.rows is already top-to-bottom) -- same "cell | cell | cell" convention parse_docx/parse_pdf use for their own tables."""
    lines = []
    for row in table.rows:
        cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
        lines.append(" | ".join(cells))
    return lines


def parse_pptx(file_file):
    """
    Parses a PPTX presentation slide-by-slide. Extracts titles, shape content
    with bullet point structures preserved, table shapes (including ones
    nested inside a group), and slide speaker notes.
    """
    prs = Presentation(file_file)
    pages_data = []

    for slide_idx, slide in enumerate(prs.slides):
        slide_text_lines = []
        title = f"Slide {slide_idx + 1}"
        title_shape = slide.shapes.title

        if title_shape:
            title_text = title_shape.text.strip()
            if title_text:
                title = title_text

        slide_text_lines.append(f"## {title}")

        # Extract from shapes (including inside group shapes)
        for shape in _iter_shapes_recursive(slide.shapes):
            if shape is title_shape:
                continue

            if shape.has_table:
                table_lines = _table_rows_as_lines(shape.table)
                if table_lines:
                    slide_text_lines.append("\n" + "\n".join(table_lines) + "\n")
                continue

            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    p_text = paragraph.text.strip()
                    if not p_text:
                        continue
                    level = paragraph.level
                    indent = "  " * level
                    prefix = "- " if level > 0 else ""
                    slide_text_lines.append(f"{indent}{prefix}{p_text}")

        # Extract speaker notes
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                # Remove extra blank lines from notes
                notes_clean = "\n".join([line.strip() for line in notes.split('\n') if line.strip()])
                slide_text_lines.append(f"\n*Speaker Notes:*\n{notes_clean}")
                
        pages_data.append({
            "page_number": slide_idx + 1,
            "content": "\n".join(slide_text_lines)
        })
        
    return pages_data

def parse_document(file, filename: str) -> list:
    """
    Routes document parsing based on extension. Returns a list of dicts:
    [{"page_number": int, "content": str}, ...]
    """
    ext = filename[filename.rfind('.'):].lower() if '.' in filename else filename.lower()
    if not ext.startswith('.'):
        ext = '.' + ext
        
    # Read binary bytes for fallback parsers or for seeking
    file_bytes = file.read()
    # Reset seek cursor for standard parsers
    file.seek(0)
    
    if ext == '.pdf':
        return parse_pdf(file)
    elif ext == '.docx':
        return parse_docx(file)
    elif ext == '.pptx':
        return parse_pptx(file)
    elif ext in ('.doc', '.ppt'):
        converted_bytes, converted_ext = _convert_legacy_office_file(file_bytes, ext)
        converted_file = io.BytesIO(converted_bytes)
        if converted_ext == '.docx':
            return parse_docx(converted_file)
        return parse_pptx(converted_file)
    elif ext in ('.txt', '.md'):
        raw_text = file_bytes.decode('utf-8', errors='ignore')
        return _chunk_raw_text_into_pages(raw_text)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

def _chunk_raw_text_into_pages(raw_text: str) -> list:
    # Partition into chunks of ~300 words
    words = raw_text.split()
    pages_data = []
    chunk_words = []
    page_idx = 1
    for w in words:
        chunk_words.append(w)
        if len(chunk_words) >= 300:
            pages_data.append({
                "page_number": page_idx,
                "content": " ".join(chunk_words)
            })
            chunk_words = []
            page_idx += 1
    if chunk_words:
        pages_data.append({
            "page_number": page_idx,
            "content": " ".join(chunk_words)
        })
    return pages_data

def _looks_like_table_row(line: str) -> bool:
    """A line built by parse_docx/parse_pdf/parse_pptx's own table extraction -- "cell | cell | cell"."""
    return " | " in line


def _segment_page_content(content: str) -> list:
    """
    Splits one page's content into chunking segments: each non-table line
    is its own {"type": "text", ...} segment, same as before; a run of one
    or more consecutive "cell | cell" lines becomes a single {"type":
    "table", "rows": [...]} segment instead of several independent line
    segments, so the packing step below can keep a table's rows together
    and knows which row is its header if it ever has to split one.
    """
    lines = [s.strip() for s in content.split('\n') if s.strip()]
    segments = []
    i = 0
    while i < len(lines):
        if _looks_like_table_row(lines[i]):
            j = i
            while j < len(lines) and _looks_like_table_row(lines[j]):
                j += 1
            segments.append({"type": "table", "rows": lines[i:j]})
            i = j
        else:
            segments.append({"type": "text", "content": lines[i]})
            i += 1
    return segments


def _split_table_into_chunk_texts(rows: list, chunk_size: int) -> list:
    """
    Splits a table too big to fit in one chunk into several, repeating its
    header row (rows[0]) at the top of every piece -- a chunk from partway
    through a long table is otherwise just numbers with no column labels
    to say what they mean (Problem log row 25).
    """
    header = rows[0]
    body = rows[1:]
    header_word_count = len(header.split())

    chunk_texts = []
    current_rows = [header]
    current_words = header_word_count

    for row in body:
        row_word_count = len(row.split())
        if current_words + row_word_count > chunk_size and len(current_rows) > 1:
            chunk_texts.append("\n".join(current_rows))
            current_rows = [header, row]
            current_words = header_word_count + row_word_count
        else:
            current_rows.append(row)
            current_words += row_word_count

    if len(current_rows) > 1 or not chunk_texts:
        chunk_texts.append("\n".join(current_rows))

    return chunk_texts


def chunk_parsed_document(pages_data: list, chunk_size=150, overlap=30) -> list:
    """
    Splits page content semantically. Groups consecutive lines/sentences
    until chunk_size words is reached. Retains overlap. A table (see
    _segment_page_content) is kept whole in one chunk whenever it fits;
    one too big to ever fit whole is split with its header row repeated at
    the top of every resulting chunk instead of losing it after the first.
    """
    chunks = []

    for page in pages_data:
        content = page["content"]
        page_num = page["page_number"]
        if not content.strip():
            continue

        segments = _segment_page_content(content)

        current_chunk_segments = []
        current_word_count = 0

        for seg in segments:
            if seg["type"] == "table":
                rows = seg["rows"]
                table_word_count = sum(len(r.split()) for r in rows)

                # Same "exceeds the budget -> flush first" rule as a plain
                # text segment below, just sized by the whole table.
                if current_word_count + table_word_count > chunk_size and current_chunk_segments:
                    chunks.append({
                        "page_number": page_num,
                        "content": "\n".join(current_chunk_segments)
                    })
                    if len(current_chunk_segments) > 1:
                        current_chunk_segments = current_chunk_segments[-1:]
                        current_word_count = len(current_chunk_segments[0].split())
                    else:
                        current_chunk_segments = []
                        current_word_count = 0

                if table_word_count <= chunk_size:
                    # Whole table fits (now that there's room) -- keep it
                    # together as one piece, exactly like any other segment.
                    current_chunk_segments.append("\n".join(rows))
                    current_word_count += table_word_count
                    continue

                # The table alone is bigger than a whole chunk -- flush
                # whatever was accumulated first (so a table-split chunk is
                # never missing its header behind unrelated leftover text),
                # then split it into its own self-contained chunks.
                if current_chunk_segments:
                    chunks.append({
                        "page_number": page_num,
                        "content": "\n".join(current_chunk_segments)
                    })
                    current_chunk_segments = []
                    current_word_count = 0

                for table_chunk_text in _split_table_into_chunk_texts(rows, chunk_size):
                    chunks.append({"page_number": page_num, "content": table_chunk_text})
                continue

            # seg["type"] == "text"
            seg_words = seg["content"].split()
            if not seg_words:
                continue
            seg_word_count = len(seg_words)

            # If adding this segment exceeds chunk_size, save current chunk
            if current_word_count + seg_word_count > chunk_size and current_chunk_segments:
                chunks.append({
                    "page_number": page_num,
                    "content": "\n".join(current_chunk_segments)
                })

                # Create overlap: keep the last segment if possible, or reset
                if len(current_chunk_segments) > 1:
                    current_chunk_segments = current_chunk_segments[-1:]
                    current_word_count = len(current_chunk_segments[0].split())
                else:
                    current_chunk_segments = []
                    current_word_count = 0

            current_chunk_segments.append(seg["content"])
            current_word_count += seg_word_count

        if current_chunk_segments:
            chunks.append({
                "page_number": page_num,
                "content": "\n".join(current_chunk_segments)
            })

    # Clean up empty chunks and normalize spaces
    cleaned_chunks = []
    for c in chunks:
        content_clean = re.sub(r'[ \t]+', ' ', c["content"]).strip()
        if content_clean:
            cleaned_chunks.append({
                "page_number": c["page_number"],
                "content": content_clean
            })

    return cleaned_chunks
