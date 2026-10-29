from io import BytesIO
from xml.sax.saxutils import escape
from datetime import datetime, timedelta, timezone
import base64
import secrets

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import A4


def build(row, order=False):
    output = BytesIO()
    number = row['order_number'] if order else row['receipt_number']
    title = 'Orden de pago' if order else 'Comprobante de registro de pago'
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=48, leftMargin=48, topMargin=45, bottomMargin=45, title=title+' '+number, author='CTT-FISEI')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='ReceiptTitle', fontName='Helvetica-Bold', fontSize=23, leading=29, textColor=colors.HexColor('#153b35'), spaceAfter=16))
    styles.add(ParagraphStyle(name='ReceiptBody', fontName='Helvetica', fontSize=11, leading=17, spaceAfter=8))
    p = lambda text: Paragraph(escape(str(text)).replace('\n','<br/>'), styles['ReceiptBody'])
    date = datetime.fromisoformat((row.get('created_at') or row.get('paid_at') or datetime.now(timezone.utc).isoformat()) if order else row['paid_at']).astimezone(timezone(timedelta(hours=-5))).strftime('%d/%m/%Y %H:%M (Ecuador)')
    price = row.get('price') or {}
    amount = str(price['amount'])+' '+str(price.get('currency','USD')) if price.get('amount') is not None else 'Importe no registrado'
    story = [p('CTT-FISEI | Universidad Técnica de Ambato'), Spacer(1, 16), Paragraph(title,styles['ReceiptTitle']), p('Número: '+number), p(('PENDIENTE DE PAGO - NO ACREDITA UN PAGO' if order else 'Estado: PAGO CONFIRMADO')+(' - DEMOSTRACIÓN SIN COBRO REAL' if row.get('is_demo') else '')), Spacer(1,18)]
    fields = [('Estudiante',row['name']),('Identificación','******'+row.get('identification','')[-4:]),('Curso',row['course_title']),('Categoría',price.get('category') or 'No registrada'),('Importe',amount),('Fecha de emisión' if order else 'Fecha de confirmación',date),('Solicitud',row['id'])]
    if not order:
        fields.append(('Confirmado por',row.get('confirmed_by') or 'Operador'))
    table = Table([[p(label),p(value)] for label,value in fields],colWidths=[145,354],hAlign='LEFT')
    table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(0,-1),colors.HexColor('#edf2e7')),('LINEBELOW',(0,0),(-1,-1),.5,colors.HexColor('#dce5d5')),('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    explanation = 'Presenta esta orden en CTT-FISEI, Campus Huachi, para verificar cupo y realizar el pago. No es un comprobante de pago ni garantiza cupo.' if order else 'Este documento registra la confirmación manual del pago en la plataforma. No constituye una factura tributaria.'
    story += [table,Spacer(1,24),p(explanation),p('Conserva este documento y presenta su número para cualquier consulta.')]
    if row.get('is_demo'):
        story.append(p('Documento de prueba. No acredita una transacción real ni requiere realizar pagos.'))
    doc.build(story)
    return output.getvalue()


def ensure_order(row):
    if not row.get('order_pdf'):
        row['order_number'] = 'ORD-'+row['id'].upper()
        row['order_pdf'] = base64.b64encode(build(row, order=True)).decode()
    return row


def ensure_paid_receipt(row):
    if row.get('status') != 'paid':
        raise ValueError('El pago todavía no está confirmado')
    if not row.get('receipt_pdf'):
        row['receipt_number'] = row.get('receipt_number') or 'CTT-'+secrets.token_hex(6).upper()
        row['paid_at'] = row.get('paid_at') or datetime.now(timezone.utc).isoformat()
        row['receipt_pdf'] = base64.b64encode(build(row)).decode()
    return row
