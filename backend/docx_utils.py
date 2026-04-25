import zipfile
import io
import re
from xml.sax.saxutils import unescape


def strip_tracing(docx_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        with z.open("word/document.xml") as f:
            xml = f.read().decode("utf-8")

    def strip_run(m):
        run = m.group(0)
        run = re.sub(r'<w:rStyle\s+w:val="StyleUnderline"\s*/>', "", run)
        run = re.sub(r"<w:highlight[^/]*/>", "", run)
        run = re.sub(r"<w:bdr[^/]*/>", "", run)
        return run

    xml = re.sub(r"<w:r[^>]*>[\s\S]*?<\/w:r>", strip_run, xml)
    return xml


def _decode_xml_text(s: str) -> str:
    s = unescape(s)
    s = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    return s


def extract_text_from_xml(stripped_xml: str) -> str:
    # Inject newlines inside <w:t> nodes at paragraph/break boundaries so
    # they survive the subsequent <w:t> extraction step.
    text = stripped_xml.replace("</w:p>", "<w:t>\n</w:t>")
    text = re.sub(r"<w:br[^/]*/?>", "<w:t>\n</w:t>", text)
    parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", text, re.DOTALL)
    result = "".join(parts)
    result = re.sub(r"<[^>]+>", "", result)
    return _decode_xml_text(result)


def get_para_text(para_str: str) -> str:
    parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", para_str, re.DOTALL)
    return _decode_xml_text("".join(parts))


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _make_run(text: str, run_type: str, sz: str, hl_color: str) -> str:
    if not text:
        return ""
    space_attr = ' xml:space="preserve"' if text != text.strip() else ""
    escaped = _xml_escape(text)

    if run_type == "plain":
        return (
            f'<w:r><w:rPr><w:sz w:val="{sz}"/></w:rPr>'
            f"<w:t{space_attr}>{escaped}</w:t></w:r>"
        )
    if run_type == "ul":
        return (
            f'<w:r><w:rPr><w:rStyle w:val="StyleUnderline"/></w:rPr>'
            f"<w:t{space_attr}>{escaped}</w:t></w:r>"
        )
    if run_type == "hl":
        return (
            f'<w:r><w:rPr><w:rStyle w:val="StyleUnderline"/>'
            f'<w:highlight w:val="{hl_color}"/>'
            f'<w:bdr w:val="single" w:sz="4" w:space="0" w:color="auto"/></w:rPr>'
            f"<w:t{space_attr}>{escaped}</w:t></w:r>"
        )
    return ""


def rebuild_para(
    para_str: str, matched_ul: list, matched_hl: list, hl_color: str
) -> str:
    # Guard: self-closing opening tag
    opening_match = re.match(r"<w:p([^>]*)>", para_str)
    if not opening_match or opening_match.group(0).rstrip().endswith("/>"):
        return para_str

    p_open = opening_match.group(0)
    p_pr_match = re.search(r"<w:pPr>[\s\S]*?</w:pPr>", para_str)
    p_pr = p_pr_match.group(0) if p_pr_match else ""

    sz_match = re.search(r'<w:sz\s+w:val="(\d+)"', para_str)
    sz = sz_match.group(1) if sz_match else "20"

    full_text = get_para_text(para_str)
    if not full_text:
        return para_str

    # Build character-level type array: plain | ul | hl
    types = ["plain"] * len(full_text)

    for ul in matched_ul:
        idx = full_text.find(ul)
        while idx != -1:
            for pos in range(idx, idx + len(ul)):
                if types[pos] == "plain":
                    types[pos] = "ul"
            idx = full_text.find(ul, idx + len(ul))

    for hl in matched_hl:
        idx = full_text.find(hl)
        while idx != -1:
            for pos in range(idx, idx + len(hl)):
                types[pos] = "hl"
            idx = full_text.find(hl, idx + len(hl))

    # Collapse into segments
    segments = []
    i = 0
    while i < len(full_text):
        t = types[i]
        j = i
        while j < len(full_text) and types[j] == t:
            j += 1
        segments.append((t, full_text[i:j]))
        i = j

    new_runs = "".join(_make_run(text, rtype, sz, hl_color) for rtype, text in segments)
    return f"{p_open}{p_pr}{new_runs}</w:p>"


def apply_tracings(stripped_xml: str, tracings: list, hl_color: str) -> str:
    para_pattern = re.compile(r"<w:p[^/][^>]*>[\s\S]*?<\/w:p>")
    cards_to_match = [t for t in tracings if not t.get("skip")]
    prev_was_heading = False

    def process_para(m):
        nonlocal prev_was_heading
        para_str = m.group(0)

        opening_match = re.match(r"<w:p([^>]*)>", para_str)
        if opening_match and opening_match.group(0).rstrip().endswith("/>"):
            prev_was_heading = False
            return para_str

        is_heading = bool(re.search(r'<w:pStyle\s+w:val="Heading\d"', para_str))
        if is_heading:
            prev_was_heading = True
            return para_str

        para_text = get_para_text(para_str)

        is_citation = (
            len(para_text) < 600
            and bool(re.search(r"\d{2,4}", para_text))
            and bool(re.search(r"[,;]", para_text))
        )

        if prev_was_heading and is_citation:
            prev_was_heading = False
            return para_str

        prev_was_heading = False

        if not para_text.strip():
            return para_str

        for tracing in cards_to_match:
            underlined = tracing.get("underlined", [])
            highlighted = tracing.get("highlighted", [])
            if not underlined:
                continue
            matched_ul = [ul for ul in underlined if ul in para_text]
            if matched_ul:
                matched_hl = [hl for hl in highlighted if hl in para_text]
                return rebuild_para(para_str, matched_ul, matched_hl, hl_color)

        return para_str

    return para_pattern.sub(process_para, stripped_xml)


def build_output_docx(original_docx_bytes: bytes, traced_xml: str) -> bytes:
    output_buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original_docx_bytes)) as zin:
        with zipfile.ZipFile(output_buffer, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "word/document.xml":
                    zout.writestr(item, traced_xml.encode("utf-8"))
                elif item.filename == "word/_rels/settings.xml.rels":
                    data = zin.read(item.filename).decode("utf-8")
                    data = re.sub(
                        r"<Relationship[^>]*attachedTemplate[^>]*/>", "", data
                    )
                    zout.writestr(item, data.encode("utf-8"))
                else:
                    zout.writestr(item, zin.read(item.filename))
    return output_buffer.getvalue()
