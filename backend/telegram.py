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


def send(chat_id, text):
    return call('sendMessage', {'chat_id': chat_id, 'text': text[:4000]})


def start_polling(receive):
    def run():
        offset = 0
        while True:
            try:
                updates = call('getUpdates', {'offset': offset, 'timeout': 25, 'allowed_updates': ['message']})
                for update in updates:
                    message = update.get('message', {})
                    if message.get('text') and message.get('chat', {}).get('type') == 'private':
                        receive('tg-' + str(message['chat']['id']), message.get('from', {}).get('first_name', 'Telegram'), message['text'], message['chat']['id'], str(update['update_id']))
                    offset = update['update_id'] + 1
            except Exception:
                log.warning('Recepción de Telegram interrumpida; se reintentará en 5 segundos.')
                time.sleep(5)
    threading.Thread(target=run, daemon=True, name='telegram-polling').start()
