import os
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.store import Store
from backend.bot_flow import route

COURSE = {'id': 9, 'titulo': 'Curso próximo', 'estado': 'PRÓXIMAMENTE', 'details': {}, 'duracion': '32 horas'}


class EnrollmentTests(unittest.TestCase):
    def test_registration_validation_cancel_and_duplicate(self):
        chat = {}
        route(chat, '', [COURSE], 'register:9')
        route(chat, 'Ana Pérez', [COURSE])
        invalid = route(chat, '0000000000', [COURSE])
        self.assertIn('no pasa', invalid['text'])
        route(chat, '1710034065', [COURSE])
        self.assertEqual(chat['registration_draft']['step'], 'phone')
        route(chat, '0991234567', [COURSE])
        route(chat, 'ana@example.com', [COURSE])
        self.assertEqual(chat['registration_draft']['step'], 'confirm')
        result = route(chat, '', [COURSE], 'confirm_registration')
        self.assertIn('PENDIENTE DE PAGO', result['text'])
        self.assertEqual(chat['registrations'][0]['status'], 'pending_payment')
        route(chat, '', [COURSE], 'confirm_registration')
        self.assertEqual(len(chat['registrations']), 1)
        self.assertIn('Ya tienes', route(chat, '', [COURSE], 'register:9')['text'])

    def test_expired_course_cannot_register(self):
        chat = {}
        route(chat, '', [{**COURSE, 'estado': 'INSCRIPCIONES FINALIZADAS'}], 'register:9')
        self.assertNotIn('registration_draft', chat)

    def test_chat_mode_stays_active_and_menu_exits(self):
        chat = {}
        route(chat, '', [COURSE], 'bot_chat')
        self.assertTrue(route(chat, '¿Qué puedo aprender?', [COURSE])['infer'])
        self.assertTrue(route(chat, '¿Y cuánto dura?', [COURSE])['infer'])
        route(chat, '', [COURSE], 'home')
        self.assertFalse(route(chat, 'Hola', [COURSE])['infer'])

    def test_persist_registration_payment_and_auth(self):
        with patch.dict(os.environ, {'DASHBOARD_PASSWORD': 'secret'}), patch('backend.telegram.send') as send, patch('backend.telegram.send_document'), patch('backend.agents.answer') as answer:
            store = Store(':memory:')
            with patch.object(store, 'courses', return_value=[COURSE]):
                app = create_app(store)
            receive = app.extensions['receive']
            receive('tg-1', 'Ana', '', 1, '1', action='register:9')
            for i, text in enumerate(['Ana Pérez', '1710034065', '0991234567', 'ana@example.com'], 2):
                receive('tg-1', 'Ana', text, 1, str(i))
            receive('tg-1', 'Ana', '', 1, '6', action='confirm_registration')
            answer.assert_not_called()
            chat = store.get('tg-1')
            row = chat['registrations'][0]
            self.assertTrue(all(m.get('private') for m in chat['messages'] if 'ana@example.com' in m['text']))
            client = app.test_client()
            url = f"/api/chats/tg-1/registrations/{row['id']}/paid"
            self.assertEqual(client.post(url, json={'reference': 'REC-1'}).status_code, 401)
            client.post('/api/login', json={'password': 'secret'})
            self.assertEqual(client.post('/api/chats/tg-1/registrations/missing/paid', json={}).status_code, 404)
            result = client.post(url, json={'reference': 'REC-1'})
            self.assertEqual(result.json['registration']['status'], 'paid')
            self.assertEqual(store.get('tg-1')['registrations'][0]['receipt_number'], result.json['registration']['receipt_number'])
            count = send.call_count
            client.post(url, json={'reference': 'REC-1'})
            self.assertEqual(send.call_count, count)
            store.db.close()
