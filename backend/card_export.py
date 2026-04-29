"""Export Card objects from the research library to a formatted .docx file."""

import io
import json
import zipfile


def _xe(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _run(text: str, kind: str, hl_color: str) -> str:
    if not text:
        return ""
    sa = ' xml:space="preserve"'
    t = _xe(text)
    if kind == "plain":
        return f'<w:r><w:rPr><w:sz w:val="20"/></w:rPr><w:t{sa}>{t}</w:t></w:r>'
    if kind == "ul":
        return f'<w:r><w:rPr><w:sz w:val="20"/><w:u w:val="single"/></w:rPr><w:t{sa}>{t}</w:t></w:r>'
    if kind == "hl":
        return (
            f'<w:r><w:rPr><w:sz w:val="20"/><w:u w:val="single"/>'
            f'<w:highlight w:val="{hl_color}"/></w:rPr>'
            f'<w:t{sa}>{t}</w:t></w:r>'
        )
    return ""


def _body_para(text: str, underlined: list, highlighted: list, hl_color: str) -> str:
    types = ["plain"] * len(text)

    for phrase in underlined:
        idx = text.find(phrase)
        while idx != -1:
            for p in range(idx, idx + len(phrase)):
                if types[p] == "plain":
                    types[p] = "ul"
            idx = text.find(phrase, idx + len(phrase))

    for phrase in highlighted:
        idx = text.find(phrase)
        while idx != -1:
            for p in range(idx, idx + len(phrase)):
                types[p] = "hl"
            idx = text.find(phrase, idx + len(phrase))

    segs = []
    i = 0
    while i < len(text):
        t = types[i]
        j = i
        while j < len(text) and types[j] == t:
            j += 1
        segs.append((t, text[i:j]))
        i = j

    runs = "".join(_run(s, k, hl_color) for k, s in segs)
    return f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>{runs}</w:p>'


def _card_xml(card: dict, hl_color: str) -> str:
    parts = []

    tag = card.get("tag") or "Untitled"
    parts.append(
        f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
        f'<w:r><w:rPr><w:b/><w:sz w:val="24"/></w:rPr>'
        f'<w:t xml:space="preserve">{_xe(tag)}</w:t></w:r></w:p>'
    )

    # Build cite line: Initials YY. Author, qualifications, date, "Title," Publisher. URL.
    initials = card.get("initials") or ""
    date = card.get("date") or ""
    author = card.get("author") or ""
    qual = card.get("author_qualifications") or ""
    title = card.get("title") or ""
    publisher = card.get("publisher") or ""
    url = card.get("url") or ""

    cite_parts = []
    if initials and date:
        yr = date[:4] if len(date) >= 4 else date
        cite_parts.append(f"{initials} {yr[2:]}.")
    if author:
        cite_parts.append(author + ("," if qual else ""))
    if qual:
        cite_parts.append(qual + ",")
    if date:
        cite_parts.append(date + ".")
    if title:
        cite_parts.append(f'"{title}."')
    if publisher:
        cite_parts.append(publisher + ("." if url else ""))
    if url:
        cite_parts.append(url)

    cite_text = " ".join(cite_parts)
    if cite_text:
        parts.append(
            f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
            f'<w:r><w:rPr><w:sz w:val="18"/><w:color w:val="777777"/></w:rPr>'
            f'<w:t xml:space="preserve">{_xe(cite_text)}</w:t></w:r></w:p>'
        )

    card_text = card.get("card_text") or ""
    if card_text:
        try:
            ul = json.loads(card.get("underlined") or "[]")
        except Exception:
            ul = []
        try:
            hl = json.loads(card.get("highlighted") or "[]")
        except Exception:
            hl = []

        for line in card_text.split("\n"):
            if line.strip():
                parts.append(_body_para(line, ul, hl, hl_color))

    parts.append('<w:p><w:pPr><w:spacing w:before="160" w:after="160"/></w:pPr></w:p>')
    return "\n".join(parts)


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_DOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_WORD_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>
        <w:sz w:val="20"/>
      </w:rPr>
    </w:rPrDefault>
  </w:docDefaults>
</w:styles>"""


def export_cards_to_docx(cards: list, hl_color: str = "cyan") -> bytes:
    body = "\n".join(_card_xml(c, hl_color) for c in cards)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"\n'
        '  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"\n'
        '  xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"\n'
        '  mc:Ignorable="">\n'
        "  <w:body>\n"
        f"{body}\n"
        "    <w:sectPr>\n"
        '      <w:pgSz w:w="12240" w:h="15840"/>\n'
        '      <w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080"/>\n'
        "    </w:sectPr>\n"
        "  </w:body>\n"
        "</w:document>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES.encode())
        zf.writestr("_rels/.rels", _DOT_RELS.encode())
        zf.writestr("word/document.xml", document_xml.encode())
        zf.writestr("word/styles.xml", _STYLES.encode())
        zf.writestr("word/_rels/document.xml.rels", _WORD_RELS.encode())
    return buf.getvalue()
