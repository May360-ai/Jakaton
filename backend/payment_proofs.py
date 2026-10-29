import base64
import secrets
from datetime import datetime, timezone


BANK = {'bank':'Banco de Pruebas FISEI (FICTICIO)', 'account':'DEMO-CTT-0001', 'type':'Cuenta de prueba', 'beneficiary':'Centro de capacitación DEMO', 'notice':'Datos ficticios para demostración. NO realizar transferencias ni pagos reales.'}


def pack(content):
    if len(content)>5*1024*1024:
        raise ValueError('El archivo supera 5 MB.')
    mime='application/pdf' if content.startswith(b'%PDF-') else 'image/jpeg' if content.startswith(b'\xff\xd8\xff') else 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else None
    if not mime:
        raise ValueError('Solo se aceptan imágenes JPG/PNG y archivos PDF.')
    return {'mime':mime,'size':len(content),'content':base64.b64encode(content).decode()}


def attach(row, file):
    for proof in row.get('proofs', []):
        if proof['status']=='received':
            proof['status']='superseded'
    proof={**file,'id':secrets.token_hex(8),'uploaded_at':datetime.now(timezone.utc).isoformat(),'status':'received'}
    row.setdefault('proofs',[]).append(proof)
    row.update(status='pending_review', enrollment_status='pending_validation', payment_demo=True)
    row.setdefault('bank',dict(BANK))
    return proof


def eligible(chat):
    return [r for r in chat.get('registrations',[]) if r['status'] in ['pending_payment','pending_review']]
