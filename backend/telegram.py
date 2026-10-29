import logging
import os
import threading
import time

import requests

log = logging.getLogger(__name__)


def call(method, payload, timeout=40):
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise RuntimeError('Falta TELEGRAM_BOT_TOKEN')
    # Never log the request URL: it contains the bot token.
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/{method}', json=payload, timeout=timeout)
        body = response.json()
        if not response.ok or not body.get('ok'):
            raise RuntimeError('Telegram rechazó la operación')
        return body['result']
    except requests.RequestException:
        raise RuntimeError('No se pudo conectar con Telegram') from None


def send(chat_id, text, reply_markup=None):
    payload = {'chat_id': chat_id, 'text': text[:4000]}
    if reply_markup:
        payload['reply_markup'] = reply_markup
    return call('sendMessage', payload)


def configure():
    call('setMyCommands', {'commands': [
        {'command': 'start', 'description': 'Abrir el menú principal'},
        {'command': 'cursos', 'description': 'Explorar cursos y contenido'},
        {'command': 'asesor', 'description': 'Solicitar atención humana'},
        {'command': 'ayuda', 'description': 'Preguntas frecuentes'},
        {'command': 'bot', 'description': 'Conversar con el asistente automático'},
    ]})
    call('setChatMenuButton', {'menu_button': {'type': 'commands'}})


def dispatch(update, receive):
    callback = update.get('callback_query')
    if callback:
        message = callback.get('message', {})
        if message.get('chat', {}).get('type') != 'private' or callback.get('from', {}).get('id') != message['chat']['id']:
            return
        try:
            call('answerCallbackQuery', {'callback_query_id': callback['id']}, timeout=8)
        except Exception:
            log.warning('No se pudo confirmar un botón de Telegram.')
        receive('tg-' + str(message['chat']['id']), callback['from'].get('first_name', 'Telegram'), '', message['chat']['id'], str(update['update_id']), action=callback.get('data', '')[:64])
        return
    message = update.get('message', {})
    if message.get('chat', {}).get('type') == 'private':
        text = message.get('text') or '/ayuda'
        receive('tg-' + str(message['chat']['id']), message.get('from', {}).get('first_name', 'Telegram'), text, message['chat']['id'], str(update['update_id']))


def start_polling(receive):
    def run():
        offset = 0
        while True:
            try:
                updates = call('getUpdates', {'offset': offset, 'timeout': 25, 'allowed_updates': ['message', 'callback_query']})
                for update in updates:
                    dispatch(update, receive)
                    offset = update['update_id'] + 1
            except Exception:
                log.warning('Recepción de Telegram interrumpida; se reintentará en 5 segundos.')
                time.sleep(5)
    threading.Thread(target=run, daemon=True, name='telegram-polling').start()
