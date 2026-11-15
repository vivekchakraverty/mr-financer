"""Render the narrative, forecast tables, and references shown in the plan."""
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from xml.sax.saxutils import escape, quoteattr

import reportlab
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from pydantic import AfterValidator, BaseModel, Field, HttpUrl, StringConstraints, model_validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

PLAN_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
GREEN = "#244B3A"
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
PlanText = Annotated[str, StringConstraints(max_length=4500),
                     AfterValidator(lambda value: INVALID_XML.sub("\ufffd", value))]


class TextBlock(BaseModel):
    kind: Literal["heading", "paragraph", "bullet"]
    text: PlanText


class Cell(BaseModel):
    text: PlanText
    url: HttpUrl | None = None


class TableBlock(BaseModel):
    kind: Literal["table"]
    headings: list[PlanText] = Field(min_length=1, max_length=8)
    rows: list[list[Cell]] = Field(max_length=200)

    @model_validator(mode="after")
    def check_columns(self):
        if any(len(row) != len(self.headings) for row in self.rows):
            raise ValueError("Each table row must match its headings.")
        return self


class PlanExport(BaseModel):
    preview: bool = False
    blocks: list[Annotated[TextBlock | TableBlock, Field(discriminator="kind")]] = Field(
        min_length=1, max_length=300
    )

    @property
    def title(self) -> str:
        return "Business Plan Financial Preview" if self.preview else "Business Plan"


def _column_widths(block: TableBlock, width: float) -> list[float]:
    lengths = [
        min(32, max(4, len(heading), *(len(row[index].text) for row in block.rows)))
        for index, heading in enumerate(block.headings)
    ]
    return [width * (1 / len(lengths) + length / sum(lengths)) / 2 for length in lengths]


def _register_pdf_fonts():
    candidates = (
        (Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", "arial.ttf", "arialbd.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu"), "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
        (Path(reportlab.__file__).with_name("fonts"), "Vera.ttf", "VeraBd.ttf"),
    )
    for folder, regular, bold in candidates:
        if (folder / regular).is_file() and (folder / bold).is_file():
            pdfmetrics.registerFont(TTFont("PlanSans", str(folder / regular)))
            pdfmetrics.registerFont(TTFont("PlanSansBold", str(folder / bold)))
            return


_register_pdf_fonts()


def _pdf_text(text: str, style: ParagraphStyle, url: HttpUrl | None = None):
    markup = escape(text).replace("\n", "<br/>")
    if url:
        markup = f"<link href={quoteattr(str(url))} color='{GREEN}'><u>{markup}</u></link>"
    return Paragraph(markup, style)


def _pdf(plan: PlanExport, output: BytesIO):
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=42, leftMargin=42,
                            topMargin=45, bottomMargin=48, title=plan.title)
    normal = ParagraphStyle("Body", fontName="PlanSans", fontSize=10, leading=13,
                            textColor=colors.HexColor("#172822"), spaceAfter=5)
    title = ParagraphStyle("Title", parent=normal, fontName="PlanSansBold",
                           fontSize=23, leading=28, spaceAfter=14, keepWithNext=True)
    heading = ParagraphStyle("Heading", parent=normal, fontName="PlanSansBold",
                             fontSize=12, leading=16, spaceBefore=8, keepWithNext=True)
    bullet = ParagraphStyle("Bullet", parent=normal, leftIndent=12, firstLineIndent=-12)
    cell = ParagraphStyle("Cell", parent=normal, fontSize=8, leading=11, spaceAfter=0)
    cell_heading = ParagraphStyle("Column heading", parent=cell, fontName="PlanSansBold",
                                  textColor=colors.white)
    flow = [_pdf_text(plan.title, title)]
    for block in plan.blocks:
        if isinstance(block, TextBlock):
            style = {"heading": heading, "paragraph": normal, "bullet": bullet}[block.kind]
            text = "•  " + block.text if block.kind == "bullet" else block.text
            flow.append(_pdf_text(text, style))
            continue
        rows = [[_pdf_text(value, cell_heading) for value in block.headings]]
        rows.extend([_pdf_text(value.text, cell, value.url) for value in row] for row in block.rows)
        table = LongTable(rows, colWidths=_column_widths(block, doc.width - 12), repeatRows=1,
                          splitInRow=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(GREEN)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F4EF")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        flow.extend([table, Spacer(1, 10)])

    def footer(canvas, page):
        canvas.setFont("PlanSans", 8)
        canvas.setFillColor(colors.HexColor(GREEN))
        canvas.drawRightString(A4[0] - 42, 28, str(page.page))

    doc.build(flow, onFirstPage=footer, onLaterPages=footer)


def _word_cell(cell, value: Cell):
    if value.url is None:
        cell.text = value.text
        return
    paragraph = cell.paragraphs[0]
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), paragraph.part.relate_to(
        str(value.url), RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    ))
    run = paragraph.add_run(value.text)
    run.font.color.rgb = RGBColor.from_string("244B3A")
    run.underline = True
    hyperlink.append(run._r)
    paragraph._p.append(hyperlink)


def _docx(plan: PlanExport, output: BytesIO):
    doc = Document()
    doc.core_properties.title = plan.title
    section = doc.sections[0]
    section.page_width, section.page_height = Pt(A4[0]), Pt(A4[1])
    section.left_margin = section.right_margin = Pt(42)
    section.top_margin = section.bottom_margin = Pt(45)
    title_style = doc.styles["Title"]
    title_style.font.color.rgb = RGBColor.from_string("000000")
    title_properties = title_style.element.get_or_add_pPr()
    border = title_properties.find(qn("w:pBdr"))
    if border is not None:
        title_properties.remove(border)
    doc.styles["Heading 1"].font.color.rgb = RGBColor.from_string("244B3A")
    doc.styles["Normal"].font.size = Pt(10)
    doc.add_paragraph(plan.title, "Title")
    for block in plan.blocks:
        if isinstance(block, TextBlock):
            style = {"heading": "Heading 1", "paragraph": "Normal", "bullet": "List Bullet"}[block.kind]
            doc.add_paragraph(block.text, style)
            continue
        table = doc.add_table(rows=1, cols=len(block.headings))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        widths = _column_widths(block, A4[0] - 84)
        for column, width in zip(table.columns, widths):
            column.width = Pt(width)
        header = table.rows[0]
        header._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for cell, label in zip(header.cells, block.headings):
            run = cell.paragraphs[0].add_run(label)
            run.bold = True
            run.font.size = Pt(9)
            shading = OxmlElement("w:shd")
            shading.set(qn("w:fill"), "E5EFE7")
            cell._tc.get_or_add_tcPr().append(shading)
        for values in block.rows:
            for cell, value in zip(table.add_row().cells, values):
                _word_cell(cell, value)
        for row in table.rows:
            for cell, width in zip(row.cells, widths):
                cell.width = Pt(width)
                for paragraph in cell.paragraphs:
                    paragraph.style = "Normal"
                    paragraph.paragraph_format.space_after = Pt(3)
                    paragraph.paragraph_format.space_before = Pt(2)
    doc.save(output)


def export_plan(plan: PlanExport, file_format: Literal["pdf", "docx"]) -> bytes:
    output = BytesIO()
    renderer = _pdf if file_format == "pdf" else _docx
    renderer(plan, output)
    return output.getvalue()
