"""Dependency-free PDF writer for proxy lists.

Produces a plain, searchable, selectable-text PDF (A4, Courier, multi-column)
using only the standard library: base-14 fonts need no embedding and every page
is a Flate-compressed content stream, so a list of 50,000 proxies stays small.

Layout: a title block on page 1, then ``columns`` columns of fixed-width rows,
with page numbers in the footer.
"""
from __future__ import annotations

import zlib
from datetime import datetime, timezone

PAGE_W, PAGE_H = 595.28, 841.89      # A4 in points
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 36.0, 40.0, 42.0
FONT_SIZE = 7.5
LEADING = 9.6
CHAR_W = 0.6 * FONT_SIZE             # Courier advance width
TITLE_BLOCK = 92.0                   # vertical space used by the title on page 1


def _escape(text: str) -> str:
    """Escape a string for a PDF literal; non-Latin-1 characters become '?'."""
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _stream(data: bytes) -> bytes:
    body = zlib.compress(data, 9)
    return b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(body) + body + b"\nendstream"


def rows_per_page(first: bool) -> int:
    top = PAGE_H - MARGIN_TOP - (TITLE_BLOCK if first else 0)
    return max(1, int((top - MARGIN_BOTTOM) // LEADING))


def render_table(title: str, subtitle: list[str], header: str, rows: list[str], *,
                 columns: int = 3, author: str = "Proxy-List-Gamt",
                 footer: str = "", created: datetime | None = None) -> bytes:
    """Return the bytes of a PDF listing *rows* (one string per row)."""
    created = created or datetime.now(timezone.utc)
    row_width = max([len(header)] + [len(r) for r in rows]) * CHAR_W
    usable = PAGE_W - 2 * MARGIN_X
    columns = max(1, min(columns, int((usable + 12) // (row_width + 12))))
    col_step = usable / columns

    # Split rows into pages: page 1 has less room because of the title block.
    pages: list[list[list[str]]] = []
    index, first = 0, True
    if not rows:
        pages.append([[]])
    while index < len(rows):
        per_col = rows_per_page(first)
        page_cols = []
        for _ in range(columns):
            page_cols.append(rows[index:index + per_col])
            index += per_col
        pages.append(page_cols)
        first = False

    contents: list[bytes] = []
    total = len(pages)
    for number, page_cols in enumerate(pages, 1):
        ops: list[str] = []
        top = PAGE_H - MARGIN_TOP
        if number == 1:
            ops.append(f"BT /F4 18 Tf {MARGIN_X} {top - 14:.2f} Td ({_escape(title)}) Tj ET")
            y = top - 32
            for line in subtitle:
                ops.append(f"BT /F3 8.5 Tf {MARGIN_X} {y:.2f} Td ({_escape(line)}) Tj ET")
                y -= 11.5
            ops.append(f"0.6 w {MARGIN_X} {top - TITLE_BLOCK + 14:.2f} m "
                       f"{PAGE_W - MARGIN_X} {top - TITLE_BLOCK + 14:.2f} l S")
            top -= TITLE_BLOCK
        for col, col_rows in enumerate(page_cols):
            x = MARGIN_X + col * col_step
            if header and (col_rows or number == 1):
                ops.append(f"BT /F2 {FONT_SIZE} Tf {x:.2f} {top + 2:.2f} Td ({_escape(header)}) Tj ET")
            if col_rows:
                body = "T* ".join(f"({_escape(r)}) Tj " for r in col_rows)
                ops.append(f"BT /F1 {FONT_SIZE} Tf {LEADING} TL {x:.2f} {top - 8:.2f} Td {body}ET")
        label = f"{footer}   -   page {number} of {total}" if footer else f"page {number} of {total}"
        ops.append(f"BT /F3 7 Tf {MARGIN_X} 24 Td ({_escape(label)}) Tj ET")
        contents.append("\n".join(ops).encode("latin-1"))

    # Object layout: 1 catalog, 2 pages, 3-6 fonts, 7 info, then (content, page) pairs.
    first_page_obj = 8
    page_objs = [first_page_obj + 2 * i + 1 for i in range(total)]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [%s] /Count %d >>"
         % (" ".join(f"{n} 0 R" for n in page_objs), total)).encode(),
    ]
    for name in ("Courier", "Courier-Bold", "Helvetica", "Helvetica-Bold"):
        objects.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} "
                       f"/Encoding /WinAnsiEncoding >>".encode())
    stamp = created.strftime("D:%Y%m%d%H%M%SZ")
    objects.append(
        f"<< /Title ({_escape(title)}) /Author ({_escape(author)}) /Creator (Proxy-List-Gamt) "
        f"/Producer (pdfgen.py) /CreationDate ({stamp}) >>".encode("latin-1"))
    for number, content in enumerate(contents):
        content_obj = first_page_obj + 2 * number
        objects.append(_stream(content))
        objects.append(
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
             f"/Contents {content_obj} 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R "
             f"/F3 5 0 R /F4 6 0 R >> >> >>").encode())

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<< /Size %d /Root 1 0 R /Info 7 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    return bytes(out)
