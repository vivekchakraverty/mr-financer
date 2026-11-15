from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
from pptx import Presentation
from pypdf import PdfReader

from mrfinancer.api import app

client = TestClient(app)
PLAN_ENDPOINT = "/business-plan/export/"
PITCH_ENDPOINT = "/business-plan/pitch/export/pptx"
FILING = "https://example.org/filing?a=1&b=2"
PLAN = {
    "blocks": [
        {"kind": "heading", "text": "Executive summary"},
        {"kind": "paragraph", "text": "Growth & operations <reviewed>"},
        {"kind": "table", "headings": ["Figure", "Reference"],
         "rows": [[{"text": "INR 1200.00"}, {"text": "Annual filing", "url": FILING}]]},
        {"kind": "bullet", "text": "Confirm timing"},
    ],
}


def test_business_plan_exports_preserve_text_tables_and_filing_links():
    pdf = client.post(PLAN_ENDPOINT + "pdf", json=PLAN)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert 'filename="Mr-Financer-Business-Plan.pdf"' in pdf.headers["content-disposition"]
    page = PdfReader(BytesIO(pdf.content)).pages[0]
    for text in ("Executive summary", "Growth & operations <reviewed>", "INR 1200.00",
                 "Annual filing", "Confirm timing"):
        assert text in page.extract_text()
    assert page["/Annots"][0].get_object()["/A"]["/URI"] == FILING

    word = client.post(PLAN_ENDPOINT + "docx", json={**PLAN, "preview": True})
    assert word.status_code == 200
    assert "Financial-Preview.docx" in word.headers["content-disposition"]
    doc = Document(BytesIO(word.content))
    assert doc.paragraphs[0].text == "Business Plan Financial Preview"
    assert doc.paragraphs[2].text == "Growth & operations <reviewed>"
    assert doc.tables[0].cell(1, 0).text == "INR 1200.00"
    assert doc.tables[0].cell(1, 1).paragraphs[0].hyperlinks[0].url == FILING


def test_plan_export_rejects_malformed_table():
    result = client.post(PLAN_ENDPOINT + "pdf", json={
        "blocks": [{"kind": "table", "headings": ["Input"], "rows": [[]]}],
    })
    assert result.status_code == 422


def _picture(size, color, image_format):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, image_format)
    return output.getvalue()


def test_powerpoint_preserves_batch_order_and_contains_each_image():
    wide = _picture((160, 90), "red", "PNG")
    tall = _picture((40, 80), "blue", "WEBP")
    result = client.post(PITCH_ENDPOINT, files=[
        ("images", ("wide.png", wide, "image/png")),
        ("images", ("tall.webp", tall, "image/webp")),
    ])
    assert result.status_code == 200
    assert result.headers["content-disposition"].endswith('.pptx"')
    deck = Presentation(BytesIO(result.content))
    assert len(deck.slides) == 2
    first = deck.slides[0].shapes[0]
    second = deck.slides[1].shapes[0]
    assert first.image.blob == wide
    assert first.width == deck.slide_width
    assert second.image.size == (40, 80)
    assert second.height == deck.slide_height
    assert second.width * 2 == second.height
    assert abs(second.left * 2 + second.width - deck.slide_width) <= 1


def test_powerpoint_rejects_bad_image_without_partial_download():
    assert client.post(PITCH_ENDPOINT, files={
        "images": ("fake.png", b"not an image", "image/png"),
    }).status_code == 422
    assert client.post(PITCH_ENDPOINT, files=[
        ("images", ("wide.png", b"", "image/png")),
    ] * 41).status_code == 422
