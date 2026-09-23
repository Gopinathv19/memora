"""Test doubles and tiny document builders for the Extraction Agent tests.

Nothing here touches the network: `FakeLLMClient` stands in for both NVIDIA
providers, so the suite never spends a credit.
"""

import io

from app.llm.client import LLMError, LLMUsage

DEFAULT_EXTRACTION = {
    "document_type": "invoice",
    "title": "Invoice INV-2041",
    "language": "en",
    "summary": "Invoice from Acme to Globex.",
    "fields": [
        {"key": "invoice_number", "value": "INV-2041", "page": 1, "confidence": 0.97},
        {"key": "Total Amount", "value": 12400, "page": 1, "confidence": 92},
        {"key": "", "value": "dropped: no key"},
        "not-a-dict",
    ],
    "tables": [
        {"title": "Line items", "page": 1, "columns": ["Item", "Qty"], "rows": [["Widget", 4]]},
        {"title": "empty", "rows": [], "columns": []},
    ],
}


class FakeLLMClient:
    """Records every call; fails on demand per model id."""

    provider = "build-nvidia"

    def __init__(self, extraction=None, fail_models=(), image_text="transcribed text"):
        self.extraction = extraction if extraction is not None else DEFAULT_EXTRACTION
        self.fail_models = set(fail_models)
        self.image_text = image_text
        self.calls: list[dict] = []

    def chat_json(self, model, system, user):
        self.calls.append({"kind": "chat_json", "model": model, "system": system, "user": user})
        if model in self.fail_models:
            raise LLMError("extract model down", usage=LLMUsage(10, 0, 5))
        return dict(self.extraction), LLMUsage(prompt_tokens=1000, completion_tokens=200, latency_ms=40)

    def read_image(self, model, image, mime, prompt):
        self.calls.append({"kind": "read_image", "model": model, "mime": mime, "prompt": prompt})
        if model in self.fail_models:
            raise LLMError(f"{model} unavailable")
        return f"{self.image_text} by {model}", LLMUsage(prompt_tokens=300, completion_tokens=50, latency_ms=20)


# --- document builders ------------------------------------------------------------


def make_pdf(pages: list[str], width: int = 612, height: int = 792) -> bytes:
    """A minimal valid PDF; each entry is a raw content stream for one page."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # placeholder, filled once the page tree id is known
    pages_id = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for content in pages:
        data = content.encode("latin-1")
        stream = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(data), data))
        kids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_id, width, height, font, stream)
            )
        )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (number, body))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, catalog, xref)
    )
    return out.getvalue()


def text_page(lines: list[str]) -> str:
    ops = ["BT", "/F1 11 Tf", "72 720 Td", "14 TL"]
    for line in lines:
        ops.append(f"({line}) Tj T*")
    ops.append("ET")
    return "\n".join(ops)


def table_page() -> str:
    """A ruled 4x3 grid with cell text: what a real table looks like to pdfplumber."""
    ops = ["0.8 w"]
    x0, y0, cw, rh = 72, 500, 150, 24
    for r in range(5):
        ops.append(f"{x0} {y0 + r * rh} m {x0 + 3 * cw} {y0 + r * rh} l S")
    for c in range(4):
        ops.append(f"{x0 + c * cw} {y0} m {x0 + c * cw} {y0 + 4 * rh} l S")
    ops += ["BT", "/F1 10 Tf"]
    for r in range(4):
        for c in range(3):
            ops.append(
                f"1 0 0 1 {x0 + c * cw + 6} {y0 + (3 - r) * rh + 8} Tm (R{r}C{c}) Tj"
            )
    ops.append("ET")
    return "\n".join(ops)


def png_bytes(size=(300, 200), color=(200, 30, 30)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def scanned_pdf() -> bytes:
    """An image-only page, like a phone scan: no text layer at all."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (1240, 1754), (250, 250, 250)).save(out, format="PDF", resolution=150)
    return out.getvalue()


LOREM = [
    "Memora extraction test document. This page is plain digital text.",
    "It has several full lines so the text layer is clearly reliable.",
    "Invoice number INV-2041 was issued to Globex on 2026-09-01.",
    "The total amount due is 12,400 INR payable within thirty days.",
    "No tables and no pictures appear anywhere on this page at all.",
]
