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


def parse_pdf(file_file):
    """
    Extracts text page-by-page from PDF. Identifies and removes headers/footers,
    normalizes spacing, and returns list of dicts. Opens the PDF once.
    """
    pages_lines = []  # list of (raw, non-empty) lines per page
    all_lines = []

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
                        all_lines.append(line_strip)
            pages_lines.append(lines)

    # Count frequencies of lines to identify repeated headers/footers.
    # If a line appears on more than 30% of pages (when the document has more
    # than 2 pages), flag it as a header/footer to strip out.
    line_counts = collections.Counter(all_lines)
    header_footers = set()
    if num_pages > 2:
        header_footers = {line for line, count in line_counts.items() if count / num_pages > 0.3}

    pages_data = []
    for page_idx, lines in enumerate(pages_lines):
        cleaned_lines = []
        for line_strip in lines:
            # Skip headers/footers
            if line_strip in header_footers:
                continue
            # Skip page numbers
            if re.match(r'^(page\s+)?\d+(\s+of\s+\d+)?$', line_strip, re.IGNORECASE):
                continue
            # Spacing normalization
            line_clean = re.sub(r'[ \t]+', ' ', line_strip)
            cleaned_lines.append(line_clean)

        pages_data.append({
            "page_number": page_idx + 1,
            "content": "\n".join(cleaned_lines)
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

def parse_pptx(file_file):
    """
    Parses a PPTX presentation slide-by-slide. Extracts titles, shape content
    with bullet point structures preserved, and slide speaker notes.
    """
    prs = Presentation(file_file)
    pages_data = []
    
    for slide_idx, slide in enumerate(prs.slides):
        slide_text_lines = []
        title = f"Slide {slide_idx + 1}"
        
        if slide.shapes.title:
            title_text = slide.shapes.title.text.strip()
            if title_text:
                title = title_text
        
        slide_text_lines.append(f"## {title}")
        
        # Extract from shapes
        for shape in slide.shapes:
            if shape.has_text_frame and shape != slide.shapes.title:
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

def chunk_parsed_document(pages_data: list, chunk_size=150, overlap=30) -> list:
    """
    Splits page content semantically. Groups consecutive lines/sentences
    until chunk_size words is reached. Retains overlap.
    """
    chunks = []
    
    for page in pages_data:
        content = page["content"]
        page_num = page["page_number"]
        if not content.strip():
            continue
            
        # Segment by double newlines (paragraphs) or single newlines
        segments = [s.strip() for s in content.split('\n') if s.strip()]
        
        current_chunk_segments = []
        current_word_count = 0
        
        for seg in segments:
            seg_words = seg.split()
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
                    
            current_chunk_segments.append(seg)
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
