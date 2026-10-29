import hmac
import json
import logging
import os
import queue
import secrets
import threading
import time
import base64
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory, session
from werkzeug.security import generate_password_hash, check_password_hash

from backend import agents, telegram, bot_flow, enrollment, operator_summary, receipts
from backend.store import Store

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
logging.basicConfig(level=logging.INFO)
COURSES = [
    {'id': 1, 'titulo': 'Python aplicado', 'duracion': '40 horas', 'estado': 'DEMO', 'contenido': 'Fundamentos, archivos y automatización', 'enlace': None},
    {'id': 2, 'titulo': 'Análisis de datos', 'duracion': '32 horas', 'estado': 'DEMO', 'contenido': 'Limpieza, análisis y visualización', 'enlace': None},
    {'id': 3, 'titulo': 'Desarrollo web', 'duracion': '50 horas', 'estado': 'DEMO', 'contenido': 'HTML, CSS, JavaScript y APIs', 'enlace': None},
]


def now():
    return datetime.now(timezone.utc).isoformat()


def create_app(store=None):
    app = Flask(__name__, static_folder=str(ROOT / 'frontend/dist'), static_url_path='/assets-unused')
    app.secret_key = os.getenv('SESSION_SECRET') or secrets.token_hex(32)
    app.config.update(MAX_CONTENT_LENGTH=131072, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
    db = store or Store()
    settings = db.settings()
    catalog = db.courses(COURSES)
    catalog_updated = time.monotonic()
    catalog_lock = threading.Lock()

    def current_catalog():
        nonlocal catalog, catalog_updated
        with catalog_lock:
            if time.monotonic() - catalog_updated >= 15:
                catalog = db.courses(COURSES)
                catalog_updated = time.monotonic()
            return catalog
    listeners = set()
    listener_lock = threading.Lock()
    locks = {}
    lock_guard = threading.Lock()

    def chat_lock(chat_id):
        with lock_guard:
            return locks.setdefault(chat_id, threading.RLock())

    def publish():
        with listener_lock:
            for listener in list(listeners):
                try:
                    listener.put_nowait('changed')
                except queue.Full:
                    pass

    def save(chat):
        chat['updated_at'] = now()
        db.save(chat)
        publish()

    def append(chat, role, text):
        chat['messages'].append({'id': secrets.token_hex(6), 'role': role, 'text': text, 'at': now()})

    def deliver(chat, text, markup=None):
        if chat.get('telegram_id'):
            telegram.send(chat['telegram_id'], text, reply_markup=markup)

    def release_if_idle(chat):
        if chat.get('mode') != 'human':
            return False
        last = chat.get('operator_last_activity')
        if last is None:
            chat['operator_last_activity'] = time.time()
            save(chat)
            return False
        if time.time() - last < 300:
            return False
        chat.update(mode='ai', bot_chat=True, pending=False, waiting_for_advisor=False, revision=chat.get('revision',0)+1)
        notice = 'El asistente automático retomó la conversación tras cinco minutos sin actividad del operador. Puedes escribir tu consulta o usar los botones.'
        append(chat, 'assistant', notice)
        save(chat)
        try:
            deliver(chat, notice, bot_flow.home_keyboard())
        except Exception:
            chat['error'] = 'El bot retomó la atención, pero no se pudo entregar el aviso.'
            save(chat)
        return True

    def expire_handovers():
        for item in db.all():
            if item.get('mode') == 'human':
                with chat_lock(item['id']):
                    current = db.get(item['id'])
                    if current:
                        release_if_idle(current)

    def infer(chat_id, revision, prompt=None, markup=None):
        try:
            snapshot = db.get(chat_id)
            snapshot['messages'] = [m for m in snapshot['messages'] if not m.get('private')]
            if prompt:
                snapshot['messages'].append({'role': 'user', 'text': prompt})
            reference = bot_flow.available(current_catalog()) if snapshot.get('telegram_id') else current_catalog()
            if snapshot.get('telegram_id') and snapshot.get('selected_course'):
                reference = [course for course in reference if course['id'] == snapshot['selected_course']]
            result = agents.answer(snapshot, reference)
            with chat_lock(chat_id):
                chat = db.get(chat_id)
                # Taking over or a newer message invalidates in-flight inference.
                if chat['mode'] != 'ai' or chat['revision'] != revision:
                    return
                if result['handover'] and not advisor_available():
                    result['handover'] = False
                    result['reply'] += '\n\nAhora no hay un asesor disponible. Dejé tu solicitud registrada; puedes continuar con el bot o volver más tarde.'
                    chat['waiting_for_advisor'] = True
                elif result['handover']:
                    chat['waiting_for_advisor'] = False
                deliver(chat, result['reply'], markup)
                append(chat, 'assistant', result['reply'])
                chat.update(state=result['state'], summary=result['summary'], pending=False, error=None)
                if result['handover']:
                    chat['mode'] = 'human'
                save(chat)
        except Exception:
            app.logger.warning('Fallo de inferencia o entrega en una conversación.')
            with chat_lock(chat_id):
                chat = db.get(chat_id)
                if chat and chat['revision'] == revision:
                    chat.update(mode='human', pending=False, error='No se pudo generar o entregar la respuesta. Requiere atención humana.')
                    save(chat)

    def receive(chat_id, name, text, telegram_id=None, update_id=None, action=None):
        with chat_lock(chat_id):
            chat = db.get(chat_id) or {'id': chat_id, 'name': name[:80], 'telegram_id': telegram_id, 'mode': 'ai', 'state': 'interesado', 'summary': 'Conversación nueva', 'messages': [], 'revision': 0, 'updates': [], 'pending': False, 'error': None}
            if update_id and update_id in chat['updates']:
                return chat
            release_if_idle(chat)
            if update_id:
                chat['updates'] = (chat['updates'] + [update_id])[-100:]
            if telegram_id:
                text = bot_flow.label(action, current_catalog()) if action is not None else text
            collecting = bool(chat.get('registration_draft'))
            append(chat, 'user', text[:4000])
            if action is not None:
                chat['messages'][-1]['action'] = action
            if collecting:
                chat['messages'][-1]['private'] = True
            state, handover = agents.triage(text)
            chat['state'] = state
            chat['revision'] += 1
            if telegram_id:
                chat['pending'] = False
                if action in ['bot_chat', 'enrollments'] or (action or '').startswith('register:') or text.strip().lower() in ['/bot', '/inscribirme']:
                    chat['mode'] = 'ai'
                if chat['mode'] == 'human':
                    if not advisor_available() and not chat.get('absence_notified'):
                        chat.update(waiting_for_advisor=True, absence_notified=True)
                        notice = 'Ahora no hay un asesor disponible. Tu consulta quedó registrada. Puedes volver al asistente con /bot o inscribirte con /inscribirme, sin esperar al operador.'
                        try:
                            deliver(chat, notice, bot_flow.home_keyboard())
                            append(chat, 'assistant', notice)
                        except Exception:
                            chat['error'] = 'No se pudo entregar el aviso de ausencia por Telegram.'
                    save(chat)
                    return chat
                flow = bot_flow.route(chat, text, current_catalog(), action)
                if flow['handover'] and not advisor_available():
                    chat['waiting_for_advisor'] = True
                    flow.update(handover=False, infer=False, text='Ahora no hay un asesor disponible. Dejé tu solicitud de atención registrada. Puedes continuar consultando al bot o inscribirte sin esperar; el equipo verá tu conversación cuando vuelva.', markup=bot_flow.home_keyboard())
                elif flow['handover']:
                    chat['waiting_for_advisor'] = False
                chat['state'] = flow['state']
                chat['summary'] = f"Curso: {chat.get('selected_course', 'sin seleccionar')}. Perfil: {chat.get('profile', {})}. Última acción: {'Datos de inscripción recibidos' if collecting else text[:200]}"
                chat['mode'] = 'human' if flow['handover'] else 'ai'
                chat['pending'] = flow['infer']
                save(chat)
                if flow['infer']:
                    threading.Thread(target=infer, args=(chat_id, chat['revision'], flow.get('prompt'), flow['markup'] or bot_flow.home_keyboard()), daemon=True).start()
                else:
                    try:
                        deliver(chat, flow['text'], flow['markup'])
                        append(chat, 'assistant', flow['text'])
                        if collecting or chat.get('registration_draft'):
                            chat['messages'][-1]['private'] = True
                        chat['error'] = None
                    except Exception:
                        chat.update(mode='human', error='No se pudo entregar la respuesta por Telegram. Requiere atención humana.')
                    save(chat)
                return chat
            if handover:
                chat.update(mode='human', pending=False, summary='Solicita atención humana: ' + text[:240])
                notice = 'Te paso con una persona del equipo. Podrá ver esta conversación para ayudarte.'
                save(chat)
                try:
                    deliver(chat, notice)
                    append(chat, 'assistant', notice)
                except Exception:
                    chat['error'] = 'No se pudo entregar el aviso por Telegram.'
            chat['pending'] = chat['mode'] == 'ai'
            save(chat)
            if chat['pending']:
                threading.Thread(target=infer, args=(chat_id, chat['revision']), daemon=True).start()
            return chat

    app.extensions.update(store=db, receive=receive, infer=infer, expire_handovers=expire_handovers)

    @app.before_request
    def protect():
        if not request.path.startswith('/api/') or request.path in ['/api/login', '/api/health']:
            return
        password = os.getenv('DASHBOARD_PASSWORD', '')
        if password and not session.get('operator'):
            return jsonify(error='Inicia sesión para entrar al panel'), 401
        if request.method == 'POST':
            origin = request.headers.get('Origin')
            if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
                return jsonify(error='Origen no permitido'), 403

    @app.errorhandler(Exception)
    def handle_error(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        app.logger.warning('Operación fallida: %s', type(error).__name__)
        return jsonify(error='La operación no se completó. Revisa la conexión e inténtalo de nuevo.'), 502

    @app.after_request
    def prevent_dashboard_cache(response):
        if request.path.startswith('/api/') or request.path in ['/', '/index.html']:
            response.headers['Cache-Control'] = 'no-store, private'
            response.headers['Pragma'] = 'no-cache'
        return response

    @app.get('/api/session')
    def session_status():
        return jsonify(authenticated=True, name=session.get('operator_name', 'Administrador'), admin=session.get('admin', True))

    def advisor_available():
        if 'advisor_sessions' in settings:
            return any(p['expires_at'] > time.time() for p in settings['advisor_sessions'].values())
        return bool(settings.get('advisor_available', True)) and settings.get('advisor_expires_at', time.time() + 1) > time.time()

    @app.get('/api/settings')
    def read_settings():
        own = settings.get('advisor_sessions', {}).get(session.get('presence_id'), {})
        return jsonify(advisor_available=advisor_available(), self_available=own.get('expires_at', 0) > time.time(), operator_name=session.get('operator_name', 'Administrador'), admin=session.get('admin', True))

    @app.post('/api/presence')
    def presence():
        data = request.get_json(silent=True) or {}
        with chat_lock('_settings'):
            presence_id = session.setdefault('presence_id', secrets.token_hex(16))
            settings['advisor_sessions'] = {key: value for key,value in settings.get('advisor_sessions', {}).items() if value['expires_at'] > time.time()}
            if data.get('available'):
                settings['advisor_sessions'][presence_id] = {'name': session.get('operator_name', 'Administrador'), 'expires_at': time.time() + 90}
            else:
                settings['advisor_sessions'].pop(presence_id, None)
            db.save_settings(settings)
        publish()
        return jsonify(advisor_available=advisor_available())

    @app.get('/api/operators')
    @app.post('/api/operators')
    def operators():
        if not session.get('admin', False):
            return jsonify(error='Solo el administrador puede gestionar operadores'), 403
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            username = str(data.get('username', '')).strip().lower()
            password = str(data.get('password', ''))
            name = str(data.get('name', '')).strip()
            import re
            if not re.fullmatch(r'[a-z0-9_.-]{3,40}', username) or len(password) < 10 or not 2 <= len(name) <= 80:
                return jsonify(error='Usa usuario de 3–40 caracteres, nombre y contraseña de al menos 10 caracteres'), 400
            with chat_lock('_settings'):
                settings.setdefault('operators', {})[username] = {'name': name, 'hash': generate_password_hash(password)}
                db.save_settings(settings)
        return jsonify([{'username': username, 'name': account['name']} for username, account in settings.get('operators', {}).items()])

    @app.get('/api/analytics')
    def analytics():
        rows = db.all()
        registrations = [r for c in rows for r in c.get('registrations', [])]
        real = [r for r in registrations if not r.get('is_demo')]
        from decimal import Decimal
        return jsonify(conversations=len(rows), registrations=len(registrations), pending=sum(r['status']=='pending_payment' for r in registrations), paid=sum(r['status']=='paid' for r in registrations), demo=sum(bool(r.get('is_demo')) for r in registrations), conversion=round(100 * sum(bool(c.get('registrations')) for c in rows) / len(rows), 1) if rows else 0, confirmed_usd=str(sum((Decimal(r['price']['amount']) for r in real if r['status']=='paid' and r.get('price', {}).get('currency')=='USD' and r['price'].get('amount') is not None), Decimal(0))), waiting=sum(bool(c.get('waiting_for_advisor')) for c in rows))

    @app.post('/api/logout')
    def logout():
        presence_id = session.get('presence_id')
        if presence_id and presence_id in settings.get('advisor_sessions', {}):
            settings['advisor_sessions'].pop(presence_id, None)
            db.save_settings(settings)
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/health')
    def health():
        return jsonify(ok=True, storage='supabase' if db.remote else 'sqlite', llm=bool(os.getenv('GEMINI_API_KEY') or os.getenv('LLM_API_KEY') or os.getenv('OPENAI_API_KEY')), telegram=bool(os.getenv('TELEGRAM_BOT_TOKEN')), protected=bool(os.getenv('DASHBOARD_PASSWORD')))

    @app.post('/api/login')
    def login():
        supplied = str((request.get_json(silent=True) or {}).get('password', ''))
        username = str((request.get_json(silent=True) or {}).get('username', '')).strip().lower()
        account = settings.get('operators', {}).get(username)
        master = hmac.compare_digest(supplied.encode(), os.getenv('DASHBOARD_PASSWORD', '').encode()) and not username
        if not master and not (account and check_password_hash(account['hash'], supplied)):
            time.sleep(0.3)
            return jsonify(error='Contraseña incorrecta'), 401
        session['operator'] = True
        session['operator_name'] = 'Administrador' if master else account['name']
        session['admin'] = master
        session['presence_id'] = secrets.token_hex(16)
        return jsonify(ok=True)

    @app.get('/api/chats')
    def chats():
        rows = db.all()
        for chat in rows:
            chat['summary'] = operator_summary.summarize(chat, current_catalog())
            for registration in chat.get('registrations', []):
                registration['receipt_available'] = bool(registration.pop('receipt_pdf', None))
        return jsonify(sorted(rows, key=lambda chat: chat['updated_at'], reverse=True))

    @app.get('/api/courses')
    def courses():
        return jsonify(current_catalog())

    @app.post('/api/courses')
    @app.post('/api/courses/<int:course_id>')
    def write_course(course_id=None):
        nonlocal catalog, catalog_updated
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error='Datos de curso inválidos'), 400
        title = str(data.get('titulo', '')).strip()
        states = ['ABIERTO', 'PRÓXIMAMENTE', 'INSCRIPCIONES FINALIZADAS', 'POR CONFIRMAR', 'ARCHIVADO', 'DEMO']
        if not title or len(title) > 250 or data.get('estado') not in states:
            return jsonify(error='Indica un título y estado válidos'), 400
        details = data.get('details') or {}
        if not isinstance(details, dict) or any(not isinstance(details.get(k, []), list) for k in ['materiales', 'modulos', 'precios']):
            return jsonify(error='Los materiales, módulos y precios deben ser listas'), 400
        if any(not isinstance(item, str) for k in ['materiales', 'modulos'] for item in details.get(k, [])):
            return jsonify(error='Los materiales y módulos deben ser textos'), 400
        for key in ['registration_start', 'registration_end', 'course_start', 'course_end']:
            if details.get(key):
                try:
                    datetime.strptime(details[key], '%Y-%m-%d')
                except (ValueError, TypeError):
                    return jsonify(error='Las fechas deben tener formato AAAA-MM-DD'), 400
        if any(not isinstance(p, dict) or not all(isinstance(p.get(k), str) for k in ['perfil', 'valor', 'moneda']) for p in details.get('precios', [])):
            return jsonify(error='Tarifas inválidas'), 400
        existing = next((c for c in current_catalog() if c['id'] == course_id), None)
        if course_id is not None and not existing:
            return jsonify(error='Curso inexistente'), 404
        row = {k: data.get(k) for k in ['duracion', 'contenido', 'enlace']}
        row.update(titulo=title, estado=data['estado'], is_demo=bool(data.get('is_demo')), details={**details, 'last_edited_by': session.get('operator_name', 'Administrador'), 'last_edited_at': now()})
        saved = db.save_course(row, course_id)
        with catalog_lock:
            catalog = db.courses(COURSES)
            catalog_updated = time.monotonic()
        publish()
        return jsonify(saved), 200 if course_id else 201

    @app.post('/api/registrations')
    def manual_registration():
        data = request.get_json(silent=True) or {}
        course = next((c for c in current_catalog() if str(c['id']) == str(data.get('course_id'))), None)
        if not course or course.get('estado') not in ['ABIERTO', 'PRÓXIMAMENTE']:
            return jsonify(error='Elige una edición abierta o próxima. Puedes gestionar su estado desde Cursos.'), 400
        import hashlib
        chat_id = str(data.get('chat_id') or 'manual-' + hashlib.sha256(str(data.get('identification', '')).strip().encode()).hexdigest()[:20])
        with chat_lock('manual-registrations'), chat_lock(chat_id):
            chat = db.get(chat_id)
            if data.get('chat_id') and not chat:
                return jsonify(error='Conversación inexistente'), 404
            chat = chat or {'id': chat_id, 'name': str(data.get('name', '')), 'telegram_id': None, 'mode': 'human', 'state': 'caliente', 'messages': [], 'registrations': [], 'revision': 0, 'updates': [], 'pending': False, 'error': None, 'summary': 'Inscripción creada desde el dashboard'}
            enrollment.begin(chat, course)
            for key in ['name', 'identification', 'phone', 'email']:
                before = chat['registration_draft']['step']
                message, _ = enrollment.collect(chat, str(data.get(key, '')))
                if not chat.get('registration_draft') or chat['registration_draft']['step'] == before:
                    return jsonify(error=message), 400
            for other in db.all():
                if any(r['course_id'] == course['id'] and r['identification'] == chat['registration_draft']['identification'] for r in other.get('registrations', [])):
                    return jsonify(error='Esta cédula ya tiene una inscripción para el curso'), 409
            if (course.get('details') or {}).get('precios'):
                try:
                    enrollment.choose_price(chat, course, data.get('price_index', ''))
                except ValueError as error:
                    return jsonify(error=str(error)), 400
            row = enrollment.confirm(chat, course)
            row['source'] = 'dashboard'
            row['registered_by'] = session.get('operator_name', 'Administrador')
            if chat['mode'] == 'human':
                chat['operator_last_activity'] = time.time()
            append(chat, 'operator', f"Solicitud {row['id']} registrada para {course['titulo']}. Estado: pendiente de pago.")
            chat['state'] = 'caliente'
            save(chat)
            notified = None
            if chat.get('telegram_id'):
                try:
                    deliver(chat, enrollment.notice(row))
                    row['registration_notice'] = 'sent'
                    notified = True
                except Exception:
                    row['registration_notice'] = 'failed'
                    chat['error'] = 'Inscripción guardada; no se pudo entregar el aviso por Telegram.'
                    notified = False
                save(chat)
            return jsonify(registration=row, chat_id=chat_id, notified=notified), 201

    @app.get('/api/bot')
    def bot_info():
        return jsonify(url='https://t.me/' + os.getenv('TELEGRAM_BOT_USERNAME', 'FISEI_CTT_26_Bot'))

    @app.post('/api/chats/<chat_id>/registrations/<registration_id>/paid')
    def confirm_payment(chat_id, registration_id):
        with chat_lock(chat_id):
            chat = db.get(chat_id)
            row = next((r for r in (chat or {}).get('registrations', []) if r['id'] == registration_id), None)
            if not row:
                return jsonify(error='Inscripción inexistente'), 404
            if not row.get('receipt_pdf'):
                candidate = dict(row)
                candidate.update(status='paid', paid_at=row.get('paid_at') or now(), confirmed_by=row.get('confirmed_by') or session.get('operator_name', 'Administrador'), receipt_number='CTT-'+datetime.now(timezone.utc).strftime('%Y%m%d')+'-'+secrets.token_hex(4).upper())
                pdf = receipts.build(candidate)
                row.update(candidate, payment_reference=candidate['receipt_number'], receipt_pdf=base64.b64encode(pdf).decode(), receipt_delivery='pending' if chat.get('telegram_id') else 'not_linked')
                row.setdefault('audit', []).append({'action':'payment_receipt_issued','at':now(),'actor':session.get('operator_name','Administrador'),'receipt':row['receipt_number'],'price':dict(row.get('price') or {})})
                append(chat, 'operator', 'Pago confirmado. Comprobante generado: '+row['receipt_number'])
                if chat['mode'] == 'human':
                    chat['operator_last_activity'] = time.time()
                save(chat)
            notified = None
            if chat.get('telegram_id') and row.get('receipt_delivery') != 'sent':
                try:
                    telegram.send_document(chat['telegram_id'], base64.b64decode(row['receipt_pdf']), row['receipt_number']+'.pdf')
                    row['receipt_delivery'] = 'sent'
                    notified = True
                    chat['error'] = None
                except Exception:
                    row['receipt_delivery'] = 'failed'
                    notified = False
                    chat['error'] = 'Comprobante guardado y pago confirmado. Falló el envío del PDF; puedes reintentarlo.'
                save(chat)
            return jsonify(registration={k:v for k,v in row.items() if k!='receipt_pdf'}, notified=notified)

    @app.get('/api/chats/<chat_id>/registrations/<registration_id>/receipt')
    def download_receipt(chat_id, registration_id):
        chat = db.get(chat_id)
        row = next((r for r in (chat or {}).get('registrations',[]) if r['id']==registration_id),None)
        if not row or not row.get('receipt_pdf'):
            return jsonify(error='Todavía no hay comprobante generado'),404
        return Response(base64.b64decode(row['receipt_pdf']),mimetype='application/pdf',headers={'Content-Disposition':'attachment; filename="'+row['receipt_number']+'.pdf"'})

    @app.post('/api/demo')
    def demo():
        data = request.get_json(silent=True) or {}
        text = str(data.get('text', '')).strip()
        if not text or len(text) > 4000:
            return jsonify(error='Escribe entre 1 y 4000 caracteres'), 400
        chat_id = str(data.get('id') or 'demo-' + secrets.token_hex(5))
        if not chat_id.startswith('demo-'):
            return jsonify(error='El simulador solo acepta conversaciones demo'), 400
        return jsonify(receive(chat_id, str(data.get('name') or 'Visitante demo'), text))

    @app.post('/api/chats/<chat_id>/mode')
    def mode(chat_id):
        value = (request.get_json(silent=True) or {}).get('mode')
        if value not in ['ai', 'human']:
            return jsonify(error='Modo inválido'), 400
        with chat_lock(chat_id):
            chat = db.get(chat_id)
            if not chat:
                return jsonify(error='Conversación inexistente'), 404
            chat.update(mode=value, revision=chat['revision'] + 1, pending=False)
            if value == 'human':
                chat['operator_last_activity'] = time.time()
                chat['waiting_for_advisor'] = False
                chat['absence_notified'] = False
            save(chat)
            return jsonify(chat)

    @app.post('/api/chats/<chat_id>/reply')
    def reply(chat_id):
        text = str((request.get_json(silent=True) or {}).get('text', '')).strip()
        if not text or len(text) > 4000:
            return jsonify(error='Escribe entre 1 y 4000 caracteres'), 400
        with chat_lock(chat_id):
            chat = db.get(chat_id)
            if not chat:
                return jsonify(error='Conversación inexistente'), 404
            chat.update(mode='human', revision=chat['revision'] + 1, pending=False)
            chat['operator_last_activity'] = time.time()
            chat['waiting_for_advisor'] = False
            chat['absence_notified'] = False
            save(chat)
            deliver(chat, text)
            append(chat, 'operator', text)
            chat['error'] = None
            save(chat)
            return jsonify(chat)

    @app.get('/api/events')
    def events():
        listener = queue.Queue(maxsize=5)
        with listener_lock:
            listeners.add(listener)

        def stream():
            try:
                yield 'data: connected\n\n'
                while True:
                    try:
                        listener.get(timeout=15)
                        yield 'data: changed\n\n'
                    except queue.Empty:
                        yield ': heartbeat\n\n'
            finally:
                with listener_lock:
                    listeners.discard(listener)
        return Response(stream(), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.get('/')
    @app.get('/<path:path>')
    def frontend(path='index.html'):
        return send_from_directory(app.static_folder, path)

    return app


if __name__ == '__main__':
    from waitress import serve
    app = create_app()
    host = os.getenv('HOST', '127.0.0.1')
    if host != '127.0.0.1' and not os.getenv('DASHBOARD_PASSWORD'):
        raise RuntimeError('Configura DASHBOARD_PASSWORD antes de publicar el panel')
    if os.getenv('TELEGRAM_BOT_TOKEN'):
        telegram.configure()
        telegram.start_polling(app.extensions['receive'])
    def handover_monitor():
        while True:
            try:
                app.extensions['expire_handovers']()
            except Exception:
                app.logger.warning('No se pudo comprobar la inactividad de operadores.')
            time.sleep(10)
    threading.Thread(target=handover_monitor,daemon=True,name='handover-monitor').start()
    serve(app, host=host, port=int(os.getenv('PORT', '5000')), threads=24)
