import re
import secrets
from datetime import datetime, timezone


def begin(chat, course):
    chat['registration_draft'] = {'course_id': course['id'], 'course_title': course['titulo'], 'step': 'name'}
    return 'Para registrar tu solicitud necesito tu nombre completo, cédula, teléfono y correo. Se usarán para gestionar la inscripción y el pago. No envíes comprobantes ni datos bancarios.\n\nEscribe tu nombre completo. Puedes cancelar con /cancelar.'


def collect(chat, text):
    draft = chat['registration_draft']
    step = draft['step']
    if text.strip().lower() == '/cancelar':
        chat.pop('registration_draft', None)
        return 'Solicitud cancelada. No se ha creado una inscripción.', False
    value = text.strip()
    if step == 'name':
        if len(value.split()) < 2 or not 5 <= len(value) <= 120 or any(c.isdigit() for c in value):
            return 'Escribe tus nombres y apellidos completos (sin números).', False
        draft.update(name=value, step='identification')
        return 'Escribe tu cédula ecuatoriana de 10 dígitos.', False
    if step == 'identification':
        if not re.fullmatch(r'\d{10}', value):
            return 'La cédula debe contener 10 dígitos. Revisa el número.', False
        digits = list(map(int, value))
        total = sum((n * 2 - 9 if n * 2 > 9 else n * 2) if i % 2 == 0 else n for i, n in enumerate(digits[:9]))
        if not 1 <= int(value[:2]) <= 24 or digits[2] > 5 or (10 - total % 10) % 10 != digits[9]:
            return 'La cédula no pasa la validación de formato. Revisa sus 10 dígitos.', False
        draft.update(identification=value, step='phone')
        return 'Escribe tu teléfono de contacto (10 dígitos, o formato internacional con +).', False
    if step == 'phone':
        value = re.sub(r'[\s()-]', '', value)
        if not re.fullmatch(r'(?:0\d{9}|\+[1-9]\d{7,14})', value):
            return 'Revisa tu teléfono: usa 10 dígitos empezando por 0, o formato internacional con +.', False
        draft.update(phone=value, step='email')
        return 'Escribe tu correo electrónico.', False
    if step == 'email':
        if len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
            return 'Escribe un correo electrónico válido.', False
        draft.update(email=value, step='confirm')
        return f"Revisa tu solicitud:\nCurso: {draft['course_title']}\nNombre: {draft['name']}\nCédula: ******{draft['identification'][-4:]}\nTeléfono: {draft['phone']}\nCorreo: {draft['email']}\n\nAl confirmar quedará pendiente de pago; no significa que ya esté pagada ni garantiza cupo.", True
    return 'Pulsa Confirmar solicitud o Cancelar.', True


def confirm(chat, course):
    draft = chat.get('registration_draft') or {}
    if draft.get('step') != 'confirm' or draft.get('course_id') != course['id']:
        return None
    rows = chat.setdefault('registrations', [])
    existing = next((r for r in rows if r['course_id'] == course['id']), None)
    if existing:
        chat.pop('registration_draft', None)
        return existing
    row = {k: draft[k] for k in ['course_id', 'course_title', 'name', 'identification', 'phone', 'email']}
    row.update(id=secrets.token_hex(8), status='pending_payment', created_at=datetime.now(timezone.utc).isoformat(), paid_at=None)
    rows.append(row)
    chat.pop('registration_draft', None)
    return row
