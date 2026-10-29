import hmac
import json
import logging
import os
import queue
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory, session

from backend import agents, telegram, bot_flow
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
    app.config.update(MAX_CONTENT_LENGTH=16384, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
    db = store or Store()
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

    def infer(chat_id, revision, prompt=None, markup=None):
        try:
            snapshot = db.get(chat_id)
            if prompt:
                snapshot['messages'].append({'role': 'user', 'text': prompt})
            reference = bot_flow.available(current_catalog()) if snapshot.get('telegram_id') else current_catalog()
            result = agents.answer(snapshot, reference)
            with chat_lock(chat_id):
                chat = db.get(chat_id)
                # Taking over or a newer message invalidates in-flight inference.
                if chat['mode'] != 'ai' or chat['revision'] != revision:
                    return
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
            if update_id:
                chat['updates'] = (chat['updates'] + [update_id])[-100:]
            if telegram_id:
                text = bot_flow.label(action, current_catalog()) if action is not None else text
            append(chat, 'user', text[:4000])
            state, handover = agents.triage(text)
            chat['state'] = state
            chat['revision'] += 1
            if telegram_id:
                chat['pending'] = False
                if chat['mode'] == 'human':
                    save(chat)
                    return chat
                flow = bot_flow.route(chat, text, current_catalog(), action)
                chat['state'] = flow['state']
                chat['summary'] = f"Curso: {chat.get('selected_course', 'sin seleccionar')}. Perfil: {chat.get('profile', {})}. Última acción: {text[:200]}"
                chat['mode'] = 'human' if flow['handover'] else 'ai'
                chat['pending'] = flow['infer']
                save(chat)
                if flow['infer']:
                    threading.Thread(target=infer, args=(chat_id, chat['revision'], flow.get('prompt'), flow['markup'] or bot_flow.home_keyboard()), daemon=True).start()
                else:
                    try:
                        deliver(chat, flow['text'], flow['markup'])
                        append(chat, 'assistant', flow['text'])
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

    app.extensions.update(store=db, receive=receive, infer=infer)

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

    @app.get('/api/health')
    def health():
        return jsonify(ok=True, storage='supabase' if db.remote else 'sqlite', llm=bool(os.getenv('GEMINI_API_KEY') or os.getenv('LLM_API_KEY') or os.getenv('OPENAI_API_KEY')), telegram=bool(os.getenv('TELEGRAM_BOT_TOKEN')), protected=bool(os.getenv('DASHBOARD_PASSWORD')))

    @app.post('/api/login')
    def login():
        supplied = str((request.get_json(silent=True) or {}).get('password', ''))
        if not hmac.compare_digest(supplied, os.getenv('DASHBOARD_PASSWORD', '')):
            time.sleep(0.3)
            return jsonify(error='Contraseña incorrecta'), 401
        session['operator'] = True
        return jsonify(ok=True)

    @app.get('/api/chats')
    def chats():
        return jsonify(sorted(db.all(), key=lambda chat: chat['updated_at'], reverse=True))

    @app.get('/api/courses')
    def courses():
        return jsonify(current_catalog())

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
    serve(app, host=host, port=int(os.getenv('PORT', '5000')), threads=24)
