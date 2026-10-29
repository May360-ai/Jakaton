import os
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend import bot_flow, telegram
from backend.app import create_app
from backend.store import Store


COURSES = [{'id': 7, 'titulo': 'Python', 'estado': 'ABIERTO', 'duracion': '40 horas', 'contenido': 'Variables y archivos', 'enlace': 'https://example.edu/inscripcion', 'is_demo': False}]


class BotFlowTests(unittest.TestCase):
    def test_real_course_has_published_prices_and_expired_registration(self):
        course = json.loads((Path(__file__).resolve().parent.parent / 'database/course16.json').read_text(encoding='utf-8'))
        course['id'] = 4
        result = bot_flow.route({}, '', [course], 'enroll:4')
        for price in ['120.00 USD', '150.00 USD', '180.00 USD']:
            self.assertIn(price, result['text'])
        self.assertIn('Inscripciones finalizadas el 2026-10-15', result['text'])
        self.assertFalse(any('url' in b for row in result['markup']['inline_keyboard'] for b in row))
        course['estado'] = 'ABIERTO'
        result = bot_flow.route({}, '', [course], 'enroll:4')
        self.assertFalse(any('url' in b for row in result['markup']['inline_keyboard'] for b in row))
        self.assertEqual(len(bot_flow.available([*COURSES, {**COURSES[0], 'id': 8, 'is_demo': True, 'estado': 'DEMO'}])), 1)

    def test_navigation_never_requests_llm(self):
        chat = {}
        for action in ['home', 'courses:0', 'course:7', 'content:7', 'schedule:7', 'enroll:7', 'guide', 'level:principiante', 'goal:empleo', 'faq', 'info:price']:
            result = bot_flow.route(chat, '', COURSES, action)
            self.assertFalse(result['infer'], action)
            self.assertTrue(result['text'], action)
        self.assertEqual(chat['profile'], {'level': 'principiante', 'goal': 'empleo'})

    def test_ai_only_for_requested_explanation_or_question(self):
        chat = {}
        self.assertFalse(bot_flow.route(chat, 'Hola quiero Python', COURSES)['infer'])
        self.assertFalse(bot_flow.route(chat, '', COURSES, 'explain:7')['infer'])
        self.assertFalse(bot_flow.route(chat, '', COURSES, 'ask:7')['infer'])
        self.assertTrue(bot_flow.route(chat, '¿Qué es una variable?', COURSES)['infer'])
        self.assertFalse(bot_flow.route(chat, 'Gracias', COURSES)['infer'])

    def test_demo_closed_or_unsafe_links_do_not_offer_enrollment(self):
        for changes in [{'is_demo': True}, {'estado': 'CERRADO'}, {'enlace': 'javascript:bad()'}]:
            result = bot_flow.route({}, '', [{**COURSES[0], **changes}], 'enroll:7')
            self.assertFalse(any('url' in b for row in result['markup']['inline_keyboard'] for b in row))
        result = bot_flow.route({}, '', COURSES, 'enroll:7')
        self.assertEqual(result['markup']['inline_keyboard'][0][0]['url'], 'https://example.edu/inscripcion')

    def test_stale_buttons_and_pagination(self):
        self.assertIn('ya no está', bot_flow.route({}, '', [], 'course:7')['text'])
        self.assertFalse(bot_flow.route({}, '', COURSES, 'courses:invalid')['infer'])
        courses = [{**COURSES[0], 'id': i} for i in range(20)]
        result = bot_flow.route({}, '', courses, 'courses:0')
        buttons = [b for row in result['markup']['inline_keyboard'] for b in row]
        self.assertEqual(sum(b.get('callback_data', '').startswith('course:') for b in buttons), 6)
        self.assertTrue(any(b.get('callback_data') == 'courses:1' for b in buttons))

    def test_callback_acknowledged_and_dispatched(self):
        receive = unittest.mock.Mock()
        update = {'update_id': 1, 'callback_query': {'id': 'query', 'data': 'courses:0', 'from': {'id': 123, 'first_name': 'Ana'}, 'message': {'chat': {'id': 123, 'type': 'private'}}}}
        with patch('backend.telegram.call') as call:
            telegram.dispatch(update, receive)
            call.assert_called_once_with('answerCallbackQuery', {'callback_query_id': 'query'}, timeout=8)
        self.assertEqual(receive.call_args.kwargs['action'], 'courses:0')

    def test_human_mode_cannot_be_overridden_by_old_buttons(self):
        with patch.dict(os.environ, {'DASHBOARD_PASSWORD': ''}), patch('backend.telegram.send'), patch('backend.agents.answer') as answer:
            store = Store(':memory:')
            app = create_app(store)
            receive = app.extensions['receive']
            receive('tg-test', 'Ana', '/start', 123, '1')
            receive('tg-test', 'Ana', '', 123, '2', action='human')
            receive('tg-test', 'Ana', '', 123, '3', action='home')
            self.assertEqual(store.get('tg-test')['mode'], 'human')
            answer.assert_not_called()
            store.db.close()

    def test_full_menu_persists_buttons_without_inference(self):
        with patch('backend.telegram.send') as send, patch('backend.agents.answer') as answer:
            store = Store(':memory:')
            with patch.object(store, 'courses', return_value=COURSES):
                app = create_app(store)
            receive = app.extensions['receive']
            receive('tg-test', 'Ana', '/start', 123, '1')
            for i, action in enumerate(['courses:0', 'course:7', 'content:7', 'schedule:7', 'enroll:7', 'requirements:7', 'ask:7'], 2):
                receive('tg-test', 'Ana', '', 123, str(i), action=action)
                self.assertTrue(send.call_args.kwargs['reply_markup']['inline_keyboard'])
            answer.assert_not_called()
            chat = store.get('tg-test')
            self.assertEqual(chat['selected_course'], 7)
            self.assertTrue(chat['awaiting_question'])
            self.assertFalse(chat['pending'])
            store.db.close()


if __name__ == '__main__':
    unittest.main()
