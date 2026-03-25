"""
document_generator.py — Generazione documenti formali (PDF + Excel)

Usato dal responder quando Claude decide di allegare un documento alla risposta email.

Tipi supportati:
  - preventivo    → PDF con tabella voci/importi
  - conferma      → PDF di conferma appuntamento/ordine
  - riepilogo     → PDF o Excel con dati strutturati
  - comunicazione → PDF lettera formale generica

Entry point principale:
  generate_pdf(doc_type, data, client_info)  → bytes
  generate_excel(doc_type, data, client_info) → bytes
"""

import io
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("polpo.docgen")

# ─────────────────────────────────────────────
# Colori e stili condivisi
# ─────────────────────────────────────────────

COLOR_PRIMARY   = (0.10, 0.20, 0.45)   # blu navy
COLOR_SECONDARY = (0.25, 0.50, 0.75)   # blu medio
COLOR_ACCENT    = (0.95, 0.30, 0.10)   # arancio per totali
COLOR_LIGHT     = (0.94, 0.96, 0.99)   # sfondo righe alternate
COLOR_BORDER    = (0.70, 0.75, 0.85)   # bordi tabella
COLOR_TEXT      = (0.10, 0.10, 0.15)   # testo principale
COLOR_MUTED     = (0.45, 0.50, 0.58)   # testo secondario
COLOR_WHITE     = (1.0, 1.0, 1.0)


def _now_str() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def _doc_number() -> str:
    """Genera un numero documento basato su timestamp al secondo + 3 cifre random."""
    import random
    suffix = random.randint(100, 999)
    return datetime.now().strftime(f"DOC-%Y%m%d-%H%M%S-{suffix}")


# ─────────────────────────────────────────────
# PDF — Generazione con reportlab
# ─────────────────────────────────────────────

def generate_pdf(
    doc_type:    str,
    data:        dict,
    client_info: dict,
) -> bytes:
    """
    Genera un PDF formale.

    Args:
        doc_type:    'preventivo' | 'conferma' | 'riepilogo' | 'comunicazione'
        data:        dati del documento (vedi esempi in fondo)
        client_info: record del cliente da DB (name, sector, signature, ecc.)

    Returns:
        bytes del PDF generato
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            HRFlowable, KeepTogether
        )
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=22*mm, rightMargin=22*mm,
            topMargin=20*mm,  bottomMargin=20*mm,
        )

        W = A4[0] - 44*mm  # larghezza utile

        rl_primary   = colors.Color(*COLOR_PRIMARY)
        rl_secondary = colors.Color(*COLOR_SECONDARY)
        rl_accent    = colors.Color(*COLOR_ACCENT)
        rl_light     = colors.Color(*COLOR_LIGHT)
        rl_border    = colors.Color(*COLOR_BORDER)
        rl_muted     = colors.Color(*COLOR_MUTED)
        rl_white     = colors.white

        styles = getSampleStyleSheet()

        def st(name, **kw):
            return ParagraphStyle(name, parent=styles["Normal"], **kw)

        s_company  = st("company",  fontSize=16, textColor=rl_primary, fontName="Helvetica-Bold", spaceAfter=1)
        s_tagline  = st("tagline",  fontSize=8,  textColor=rl_muted,  fontName="Helvetica", spaceAfter=2)
        s_doctype  = st("doctype",  fontSize=22, textColor=rl_primary, fontName="Helvetica-Bold", alignment=TA_RIGHT, spaceAfter=2)
        s_docmeta  = st("docmeta",  fontSize=8,  textColor=rl_muted,  fontName="Helvetica", alignment=TA_RIGHT, spaceAfter=1)
        s_label    = st("label",    fontSize=7,  textColor=rl_muted,  fontName="Helvetica-Bold", spaceAfter=1)
        s_body     = st("body",     fontSize=9,  textColor=colors.Color(*COLOR_TEXT), fontName="Helvetica", leading=14, spaceAfter=4)
        s_bold     = st("bold",     fontSize=9,  textColor=colors.Color(*COLOR_TEXT), fontName="Helvetica-Bold", spaceAfter=2)
        s_small    = st("small",    fontSize=7,  textColor=rl_muted,  fontName="Helvetica", leading=11)
        s_footer   = st("footer",   fontSize=7,  textColor=rl_muted,  fontName="Helvetica", alignment=TA_CENTER)
        s_th       = st("th",       fontSize=8,  textColor=rl_white,  fontName="Helvetica-Bold", alignment=TA_CENTER)
        s_td       = st("td",       fontSize=8,  textColor=colors.Color(*COLOR_TEXT), fontName="Helvetica")
        s_td_r     = st("td_r",     fontSize=8,  textColor=colors.Color(*COLOR_TEXT), fontName="Helvetica", alignment=TA_RIGHT)
        s_total    = st("total",    fontSize=10, textColor=rl_accent,  fontName="Helvetica-Bold", alignment=TA_RIGHT)

        story = []

        # ── INTESTAZIONE ─────────────────────────────
        company_name = client_info.get("name", "Azienda")
        sector       = client_info.get("sector", "")
        signature    = client_info.get("signature", company_name)

        doc_labels = {
            "preventivo":    "PREVENTIVO",
            "conferma":      "CONFERMA",
            "riepilogo":     "RIEPILOGO",
            "comunicazione": "COMUNICAZIONE",
        }
        doc_label = doc_labels.get(doc_type, "DOCUMENTO")
        doc_num   = data.get("numero_documento") or _doc_number()
        doc_date  = data.get("data") or _now_str()

        header_data = [[
            Paragraph(company_name, s_company),
            Paragraph(doc_label, s_doctype),
        ]]
        header_meta = [[
            Paragraph(sector, s_tagline),
            Paragraph(f"N. {doc_num} &nbsp;&nbsp; Data: {doc_date}", s_docmeta),
        ]]

        for row, colW in [(header_data, [W*0.55, W*0.45]),
                          (header_meta, [W*0.55, W*0.45])]:
            t = Table(row, colWidths=colW)
            t.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
            story.append(t)

        story.append(HRFlowable(width=W, thickness=2, color=rl_primary, spaceAfter=6))

        # ── DESTINATARIO + DETTAGLI DOC ───────────────
        recipient      = data.get("destinatario", {})
        recipient_name = recipient.get("nome", "")
        recipient_email= recipient.get("email", "")
        recipient_ref  = recipient.get("riferimento", "")

        addr_lines = [l for l in [recipient_name, recipient_email, recipient_ref] if l]

        dest_data = [[
            [Paragraph("DESTINATARIO", s_label)] +
            [Paragraph(l, s_body) for l in addr_lines],
            [Paragraph("OGGETTO", s_label),
             Paragraph(data.get("oggetto", "—"), s_bold)],
        ]]
        dt = Table(dest_data, colWidths=[W*0.5, W*0.5])
        dt.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
        story.append(dt)
        story.append(Spacer(1, 4*mm))

        # ── CORPO / INTRODUZIONE ──────────────────────
        intro = data.get("introduzione", "")
        if intro:
            story.append(Paragraph(intro, s_body))
            story.append(Spacer(1, 3*mm))

        # ── TABELLA VOCI (per preventivi e riepiloghi) ─
        voci = data.get("voci", [])
        if voci:
            th_style = TableStyle([
                ("BACKGROUND",  (0,0), (-1,0),  rl_primary),
                ("TEXTCOLOR",   (0,0), (-1,0),  rl_white),
                ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
                ("FONTSIZE",    (0,0), (-1,-1), 8),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [rl_white, rl_light]),
                ("GRID",        (0,0), (-1,-1), 0.3, rl_border),
                ("ALIGN",       (-1,0), (-1,-1), "RIGHT"),
                ("ALIGN",       (1,0), (1,-1),  "CENTER"),
                ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
                ("TOPPADDING",  (0,0), (-1,-1), 4),
                ("BOTTOMPADDING",(0,0),(-1,-1), 4),
                ("LEFTPADDING", (0,0), (-1,-1), 6),
                ("RIGHTPADDING",(0,0), (-1,-1), 6),
            ])

            table_data = [[
                Paragraph("VOCE / DESCRIZIONE", s_th),
                Paragraph("QTÀ", s_th),
                Paragraph("PREZZO UNIT.", s_th),
                Paragraph("TOTALE", s_th),
            ]]
            totale = 0.0
            for v in voci:
                desc  = v.get("descrizione", "")
                qty   = v.get("quantita", 1)
                price = v.get("prezzo_unitario", 0.0)
                line  = qty * price
                totale += line
                table_data.append([
                    Paragraph(desc, s_td),
                    Paragraph(str(qty), s_td),
                    Paragraph(f"€ {price:,.2f}", s_td_r),
                    Paragraph(f"€ {line:,.2f}", s_td_r),
                ])

            iva_pct = data.get("iva_percentuale", 22)
            iva_val = totale * iva_pct / 100
            totale_iva = totale + iva_val

            table_data.append(["", "", Paragraph("Imponibile", s_td_r), Paragraph(f"€ {totale:,.2f}", s_td_r)])
            table_data.append(["", "", Paragraph(f"IVA {iva_pct}%", s_td_r), Paragraph(f"€ {iva_val:,.2f}", s_td_r)])
            # Riga totale con stile speciale
            th_style.add("BACKGROUND", (0, len(table_data)-1), (-1, len(table_data)-1), rl_primary)
            th_style.add("TEXTCOLOR",  (0, len(table_data)-1), (-1, len(table_data)-1), rl_white)
            th_style.add("FONTNAME",   (0, len(table_data)-1), (-1, len(table_data)-1), "Helvetica-Bold")
            table_data.append(["", "", Paragraph("TOTALE IVA INCLUSA", s_th), Paragraph(f"€ {totale_iva:,.2f}", s_th)])

            voci_table = Table(table_data, colWidths=[W*0.50, W*0.10, W*0.20, W*0.20])
            voci_table.setStyle(th_style)
            story.append(KeepTogether([voci_table]))
            story.append(Spacer(1, 5*mm))

        # ── CORPO LIBERO (conferma / comunicazione) ───
        corpo = data.get("corpo", "")
        if corpo:
            for para in corpo.split("\n"):
                if para.strip():
                    story.append(Paragraph(para.strip(), s_body))
            story.append(Spacer(1, 3*mm))

        # ── NOTE LEGALI ───────────────────────────────
        note = data.get("note", "")
        condizioni = data.get("condizioni", "")
        if note or condizioni:
            story.append(HRFlowable(width=W, thickness=0.5, color=rl_border, spaceAfter=3))
            if note:
                story.append(Paragraph(f"Note: {note}", s_small))
            if condizioni:
                story.append(Paragraph(f"Condizioni: {condizioni}", s_small))
            story.append(Spacer(1, 3*mm))

        # ── FIRMA ─────────────────────────────────────
        validita = data.get("validita_giorni")
        sign_rows = []
        if validita:
            sign_rows.append(Paragraph(f"Il presente documento è valido per {validita} giorni dalla data di emissione.", s_small))
        sign_rows.append(Spacer(1, 6*mm))
        sign_rows.append(Paragraph("Cordiali saluti,", s_body))
        sign_rows.append(Paragraph(signature.replace("\n", "<br/>"), s_bold))

        story.extend(sign_rows)

        # ── FOOTER PAGINA ─────────────────────────────
        story.append(Spacer(1, 8*mm))
        story.append(HRFlowable(width=W, thickness=0.5, color=rl_border, spaceAfter=2))
        story.append(Paragraph(
            f"Documento generato da Polpo AI Mail Intelligence &nbsp;·&nbsp; {company_name} &nbsp;·&nbsp; {_now_str()}",
            s_footer
        ))

        doc.build(story)
        return buf.getvalue()

    except Exception as e:
        logger.error("document_generator | Errore PDF: %s", e)
        raise


# ─────────────────────────────────────────────
# Excel — Generazione con openpyxl
# ─────────────────────────────────────────────

def generate_excel(
    doc_type:    str,
    data:        dict,
    client_info: dict,
) -> bytes:
    """
    Genera un Excel formale con openpyxl.
    Adatto per: preventivi multi-riga, listini, riepiloghi ordini.
    """
    try:
        import openpyxl
        from openpyxl.styles import (
            Font, PatternFill, Alignment, Border, Side, numbers
        )
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = doc_type.capitalize()

        # Stili
        navy_fill   = PatternFill("solid", fgColor="1A3373")
        light_fill  = PatternFill("solid", fgColor="EEF2FC")
        total_fill  = PatternFill("solid", fgColor="1A3373")
        white_fill  = PatternFill("solid", fgColor="FFFFFF")
        thin        = Side(style="thin", color="B0BBDA")
        border      = Border(left=thin, right=thin, top=thin, bottom=thin)

        def cell_style(ws, row, col, value="", bold=False, color="000000",
                       fill=None, align="left", num_format=None, size=10):
            c = ws.cell(row=row, column=col, value=value)
            c.font      = Font(bold=bold, color=color, size=size, name="Calibri")
            c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
            c.border    = border
            if fill:
                c.fill = fill
            if num_format:
                c.number_format = num_format
            return c

        company_name = client_info.get("name", "Azienda")
        doc_num      = data.get("numero_documento") or _doc_number()
        doc_date     = data.get("data") or _now_str()

        # Intestazione
        ws.merge_cells("A1:F1")
        c = ws["A1"]
        c.value     = company_name.upper()
        c.font      = Font(bold=True, size=16, color="FFFFFF", name="Calibri")
        c.fill      = navy_fill
        c.alignment = Alignment(horizontal="left", vertical="center")

        ws.row_dimensions[1].height = 30

        ws.merge_cells("A2:D2")
        ws["A2"] = f"{doc_type.upper()} N. {doc_num}"
        ws["A2"].font      = Font(bold=True, size=11, color="1A3373", name="Calibri")
        ws["A2"].alignment = Alignment(horizontal="left", vertical="center")

        ws["E2"] = "Data:"
        ws["E2"].font = Font(bold=True, size=9, color="777777", name="Calibri")
        ws["F2"] = doc_date
        ws["F2"].font = Font(size=9, name="Calibri")

        ws.row_dimensions[2].height = 20

        # Destinatario
        recipient = data.get("destinatario", {})
        ws["A3"] = "Destinatario:"
        ws["A3"].font = Font(bold=True, size=9, color="777777", name="Calibri")
        ws.merge_cells("B3:F3")
        ws["B3"] = f"{recipient.get('nome','')}  {recipient.get('email','')}".strip()
        ws["B3"].font = Font(size=9, name="Calibri")
        ws.row_dimensions[3].height = 16

        # Oggetto
        ws["A4"] = "Oggetto:"
        ws["A4"].font = Font(bold=True, size=9, color="777777", name="Calibri")
        ws.merge_cells("B4:F4")
        ws["B4"] = data.get("oggetto", "")
        ws["B4"].font = Font(size=9, name="Calibri")
        ws.row_dimensions[4].height = 16

        row = 6  # lascia una riga vuota

        voci = data.get("voci", [])
        if voci:
            # Intestazione tabella
            headers = ["DESCRIZIONE", "QTÀ", "PREZZO UNIT. (€)", "TOTALE (€)", "", ""]
            col_widths = [40, 8, 18, 18, 5, 5]
            for ci, (h, cw) in enumerate(zip(headers, col_widths), start=1):
                ws.column_dimensions[get_column_letter(ci)].width = cw
                c = ws.cell(row=row, column=ci, value=h)
                c.font      = Font(bold=True, size=9, color="FFFFFF", name="Calibri")
                c.fill      = navy_fill
                c.alignment = Alignment(horizontal="center", vertical="center")
                c.border    = border
            ws.row_dimensions[row].height = 18
            row += 1

            # Righe dati
            totale = 0.0
            for i, v in enumerate(voci):
                desc  = v.get("descrizione", "")
                qty   = v.get("quantita", 1)
                price = v.get("prezzo_unitario", 0.0)
                line  = qty * price
                totale += line
                fill_i = light_fill if i % 2 == 0 else white_fill

                cell_style(ws, row, 1, desc,  fill=fill_i, align="left")
                cell_style(ws, row, 2, qty,   fill=fill_i, align="center")
                cell_style(ws, row, 3, price, fill=fill_i, align="right", num_format='#,##0.00 "€"')
                cell_style(ws, row, 4, line,  fill=fill_i, align="right", num_format='#,##0.00 "€"')
                ws.row_dimensions[row].height = 16
                row += 1

            # IVA e totale
            iva_pct = data.get("iva_percentuale", 22)
            iva_val = totale * iva_pct / 100
            totale_iva = totale + iva_val
            row += 1
            for label, val in [("Imponibile", totale), (f"IVA {iva_pct}%", iva_val)]:
                cell_style(ws, row, 3, label, align="right", bold=True)
                cell_style(ws, row, 4, val, align="right", num_format='#,##0.00 "€"')
                ws.row_dimensions[row].height = 14
                row += 1
            # Totale finale
            ws.merge_cells(f"A{row}:C{row}")
            c = ws.cell(row=row, column=1, value="TOTALE IVA INCLUSA")
            c.font      = Font(bold=True, size=11, color="FFFFFF", name="Calibri")
            c.fill      = navy_fill
            c.alignment = Alignment(horizontal="right", vertical="center")
            c = ws.cell(row=row, column=4, value=totale_iva)
            c.font         = Font(bold=True, size=11, color="FFFFFF", name="Calibri")
            c.fill         = navy_fill
            c.alignment    = Alignment(horizontal="right", vertical="center")
            c.number_format = '#,##0.00 "€"'
            ws.row_dimensions[row].height = 22

        # Corpo libero
        corpo = data.get("corpo", "")
        if corpo:
            row += 2
            ws.merge_cells(f"A{row}:F{row+5}")
            c = ws.cell(row=row, column=1, value=corpo)
            c.font      = Font(size=9, name="Calibri")
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[row].height = 80

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    except Exception as e:
        logger.error("document_generator | Errore Excel: %s", e)
        raise


# ─────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────

def generate_document(
    format:      str,
    doc_type:    str,
    data:        dict,
    client_info: dict,
) -> tuple[bytes, str]:
    """
    Entry point principale.

    Args:
        format:      'pdf' | 'excel'
        doc_type:    'preventivo' | 'conferma' | 'riepilogo' | 'comunicazione'
        data:        dati del documento
        client_info: record cliente

    Returns:
        (file_bytes, filename)
    """
    company_slug = client_info.get("name", "polpo").lower().replace(" ", "_")
    doc_num      = (data.get("numero_documento") or _doc_number()).replace(":", "-")

    if format == "excel":
        content  = generate_excel(doc_type, data, client_info)
        filename = f"{company_slug}_{doc_type}_{doc_num}.xlsx"
        logger.info("docgen | Excel generato: %s (%d bytes)", filename, len(content))
        return content, filename
    else:
        content  = generate_pdf(doc_type, data, client_info)
        filename = f"{company_slug}_{doc_type}_{doc_num}.pdf"
        logger.info("docgen | PDF generato: %s (%d bytes)", filename, len(content))
        return content, filename


# ─────────────────────────────────────────────
# Esempio dati per test (eseguire: python document_generator.py)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    client = {
        "name": "TecnoDomus360",
        "sector": "Impianti domestici e domotica",
        "signature": "Cordiali saluti,\nTecnoDomus360 — Ufficio Commerciale",
    }

    doc_data = {
        "oggetto":          "Preventivo — Impianto Fotovoltaico 8kW con accumulo",
        "destinatario": {
            "nome":         "Luca Ferretti",
            "email":        "luca.ferretti@example.com",
            "riferimento":  "Rif. email del 10/03/2026",
        },
        "introduzione": (
            "In riferimento alla Sua richiesta, siamo lieti di presentarLe "
            "il nostro preventivo per la fornitura e installazione di un impianto "
            "fotovoltaico presso la Sua abitazione. Il preventivo include tutti i costi "
            "di manodopera, materiali certificati e pratiche burocratiche."
        ),
        "voci": [
            {"descrizione": "Pannelli fotovoltaici monocristallini 400W (n. 20)",  "quantita": 20, "prezzo_unitario": 210.00},
            {"descrizione": "Inverter ibrido 8kW trifase con monitoraggio",        "quantita": 1,  "prezzo_unitario": 1850.00},
            {"descrizione": "Sistema di accumulo 10kWh LFP con BMS",              "quantita": 1,  "prezzo_unitario": 3200.00},
            {"descrizione": "Strutture di ancoraggio e cablaggio completo",        "quantita": 1,  "prezzo_unitario": 780.00},
            {"descrizione": "Installazione e collaudo (2 giorni lavoro)",          "quantita": 1,  "prezzo_unitario": 960.00},
            {"descrizione": "Pratiche GSE per detrazione fiscale 50%",             "quantita": 1,  "prezzo_unitario": 280.00},
        ],
        "iva_percentuale":  22,
        "validita_giorni":  30,
        "note":             "Garanzia pannelli 25 anni produzione, inverter 10 anni.",
        "condizioni":       "Accettazione entro i termini di validità. Pagamento: 30% all'ordine, saldo alla consegna.",
    }

    # Genera PDF
    pdf_bytes, pdf_name = generate_document("pdf", "preventivo", doc_data, client)
    with open(f"/tmp/{pdf_name}", "wb") as f:
        f.write(pdf_bytes)
    print(f"PDF → /tmp/{pdf_name} ({len(pdf_bytes)} bytes)")

    # Genera Excel
    xl_bytes, xl_name = generate_document("excel", "preventivo", doc_data, client)
    with open(f"/tmp/{xl_name}", "wb") as f:
        f.write(xl_bytes)
    print(f"Excel → /tmp/{xl_name} ({len(xl_bytes)} bytes)")
