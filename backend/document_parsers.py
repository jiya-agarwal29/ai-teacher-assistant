import re
import collections
import pdfplumber
import docx
from pptx import Presentation

def extract_strings_from_binary(file_bytes):
    """
    Extracts printable ASCII and UTF-16-LE strings from a binary stream.
    Used as a fallback for older .doc and .ppt formats without COM dependencies.
    """
    # Extract ASCII strings
    ascii_strings = []
    current = []
    for char in file_bytes:
        if 32 <= char <= 126 or char in (10, 13, 9):
            current.append(chr(char))
        else:
            if len(current) >= 8:
                ascii_strings.append("".join(current))
            current = []
    if len(current) >= 8:
        ascii_strings.append("".join(current))
        
    # Extract UTF-16-LE strings
    utf16_strings = []
    i = 0
    n = len(file_bytes)
    current_u = []
    while i < n - 1:
        b1 = file_bytes[i]
        b2 = file_bytes[i+1]
        if b2 == 0 and (32 <= b1 <= 126 or b1 in (10, 13, 9)):
            current_u.append(chr(b1))
            i += 2
        else:
            if len(current_u) >= 8:
                utf16_strings.append("".join(current_u))
            current_u = []
            i += 1
    if len(current_u) >= 8:
        utf16_strings.append("".join(current_u))
        
    all_strings = ascii_strings + utf16_strings
    cleaned_paragraphs = []
    for s in all_strings:
        s_clean = s.strip()
        if not s_clean:
            continue
        # Remove obvious junk lines (too many non-alphanumeric chars)
        alnum_count = sum(1 for c in s_clean if c.isalnum())
        if len(s_clean) > 0 and (alnum_count / len(s_clean)) < 0.4:
            continue
        # Avoid common binary metadata or software keywords
        if any(bad in s_clean.lower() for bad in ['microsoft', 'word document', 'powerpoint', 'msword', 'document summary', 'normal.dotm', 'root entry', 'current user']):
            if len(s_clean) < 40:
                continue
        cleaned_paragraphs.append(s_clean)
        
    # Split paragraphs by newline and filter short lines
    text = "\n".join(cleaned_paragraphs)
    lines = text.split('\n')
    good_lines = []
    for line in lines:
        l_strip = line.strip()
        if len(l_strip) < 15:
            continue
        # Standardize spacing
        l_clean = re.sub(r'[ \t]+', ' ', l_strip)
        good_lines.append(l_clean)
        
    return "\n".join(good_lines)

def parse_pdf(file_file):
    """
    Extracts text page-by-page from PDF. Identifies and removes headers/footers,
    normalizes spacing, and returns list of dicts.
    """
    pages_data = []
    all_lines = []
    
    # Read PDF using pdfplumber
    with pdfplumber.open(file_file) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                for line in text.split('\n'):
                    line_strip = line.strip()
                    if line_strip:
                        all_lines.append(line_strip)
                        
    # Count frequencies of lines to identify repeated headers/footers
    line_counts = collections.Counter(all_lines)
    total_pages = len(all_lines) # Estimate density
    # If a line appears on more than 30% of pages (when document has at least 3 pages), flag as header/footer
    header_footers = set()
    if total_pages > 3:
        # Re-estimate based on actual page count
        with pdfplumber.open(file_file) as pdf:
            num_pages = len(pdf.pages)
        if num_pages > 2:
            header_footers = {line for line, count in line_counts.items() if count / num_pages > 0.3}

    # Reset cursor and extract clean text
    file_file.seek(0)
    with pdfplumber.open(file_file) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                pages_data.append({
                    "page_number": page_idx + 1,
                    "content": ""
                })
                continue
                
            lines = text.split('\n')
            cleaned_lines = []
            for line in lines:
                line_strip = line.strip()
                if not line_strip:
                    continue
                # Skip headers/footers
                if line_strip in header_footers:
                    continue
                # Skip page numbers
                if re.match(r'^(page\s+)?\d+(\s+of\s+\d+)?$', line_strip, re.IGNORECASE):
                    continue
                # Spacing normalization
                line_clean = re.sub(r'[ \t]+', ' ', line_strip)
                cleaned_lines.append(line_clean)
                
            page_text = "\n".join(cleaned_lines)
            pages_data.append({
                "page_number": page_idx + 1,
                "content": page_text
            })
            
    return pages_data

def parse_docx(file_file):
    """
    Parses a DOCX file using python-docx. Preserves section and heading hierarchy
    using Markdown formatting (# for Heading 1, etc.).
    """
    doc = docx.Document(file_file)
    paragraphs = []
    
    # Process elements: paragraphs and tables
    for element in doc.element.body:
        if element.tag.endswith('p'):
            # Paragraph
            # Find the paragraph object in python-docx
            for p in doc.paragraphs:
                if p._element == element:
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
                    break
        elif element.tag.endswith('tbl'):
            # Table
            for t in doc.tables:
                if t._element == element:
                    table_rows = []
                    for row in t.rows:
                        row_cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                        table_rows.append(" | ".join(row_cells))
                    if table_rows:
                        paragraphs.append("\n" + "\n".join(table_rows) + "\n")
                    break

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
        # Fallback raw binary text extraction
        raw_text = extract_strings_from_binary(file_bytes)
        return _chunk_raw_text_into_pages(raw_text)
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
