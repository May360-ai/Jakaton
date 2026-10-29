import os
import time
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.store import Store
from backend.bot_flow import route
from backend import telegram


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'DASHBOARD_PASSWORD':'master-test','GEMINI_API_KEY':'test'})
        self.env.start()
        self.documents = patch('backend.telegram.send_document')
        self.documents.start()
        self.store = Store(':memory:')
        self.app = create_app(self.store)
        self.client = self.app.test_client()
        self.client.post('/api/login',json={'password':'master-test'})

    def tearDown(self):
        self.store.db.close()
        self.documents.stop()
        self.env.stop()

    def course(self, title, amount='120.00'):
        return self.client.post('/api/courses', json={'titulo':title,'estado':'ABIERTO','details':{'precios':[{'perfil':'Público general','valor':amount,'moneda':'USD'}]}}).json

    def person(self, course):
        return {'course_id':course['id'],'name':'Ana Pérez','identification':'1710034065','phone':'0991234567','email':'ana@example.com','price_index':0}

    def test_person_multiple_courses_independent_prices_and_payments(self):
        first,second = self.course('Python','120.00'), self.course('Redes','180.00')
        one = self.client.post('/api/registrations',json=self.person(first))
        two = self.client.post('/api/registrations',json=self.person(second))
        self.assertEqual(one.status_code,201)
        self.assertEqual(two.status_code,201)
        self.assertEqual(one.json['chat_id'],two.json['chat_id'])
        self.assertEqual(self.client.post('/api/registrations',json=self.person(first)).status_code,409)
        self.client.post('/api/operators',json={'username':'operadora','name':'Ana Operadora','password':'individual-test-pass'})
        operator=self.app.test_client()
        self.assertEqual(operator.post('/api/login',json={'username':'operadora','password':'individual-test-pass'}).status_code,200)
        self.assertEqual(operator.get('/api/operators').status_code,403)
        paid=operator.post(f"/api/chats/{one.json['chat_id']}/registrations/{one.json['registration']['id']}/paid",json={'reference':'REC-120'})
        self.assertEqual(paid.json['registration']['confirmed_by'],'Ana Operadora')
        self.assertEqual(paid.json['registration']['audit'][0]['price']['amount'],'120.00')
        rows=self.store.get(one.json['chat_id'])['registrations']
        self.assertEqual([r['status'] for r in rows],['paid','pending_payment'])
        self.assertEqual([r['price']['amount'] for r in rows],['120.00','180.00'])
        metrics=operator.get('/api/analytics').json
        self.assertEqual(metrics['confirmed_usd'],'120.00')
        self.assertEqual(metrics['conversion'],100.0)

    def test_bot_requires_price_and_retains_snapshot(self):
        course=self.course('Python')
        chat={}
        route(chat,'',[course],'register:'+str(course['id']))
        for text in ['Ana Pérez','1710034065','0991234567','ana@example.com']:
            route(chat,text,[course])
        denied=route(chat,'',[course],'confirm_registration')
        self.assertIn('selecciona',denied['text'])
        route(chat,'',[course],'tariff:0')
        route(chat,'',[course],'confirm_registration')
        course['details']['precios'][0]['valor']='200.00'
        self.assertEqual(chat['registrations'][0]['price']['amount'],'120.00')

    def test_same_person_can_register_multiple_courses_in_bot(self):
        courses=[self.course('Python','120.00'),self.course('Redes','180.00')]
        chat={}
        for course in courses:
            route(chat,'',courses,'register:'+str(course['id']))
            for text in ['Ana Pérez','1710034065','0991234567','ana@example.com']:
                route(chat,text,courses)
            route(chat,'',courses,'tariff:0')
            route(chat,'',courses,'confirm_registration')
        self.assertEqual(len(chat['registrations']),2)
        self.assertEqual({r['identification'] for r in chat['registrations']},{'1710034065'})
        self.assertEqual([r['price']['amount'] for r in chat['registrations']],['120.00','180.00'])

    def test_advisor_absent_keeps_bot_available_and_queues_request(self):
        self.client.post('/api/presence',json={'available':False})
        with patch('backend.telegram.send'):
            chat=self.app.extensions['receive']('tg-1','Ana','Quiero un humano',1,'1')
        self.assertEqual(chat['mode'],'ai')
        self.assertTrue(chat['waiting_for_advisor'])
        self.assertIn('no hay un asesor',chat['messages'][-1]['text'])
        self.assertEqual(self.client.get('/api/analytics').json['waiting'],1)
        self.assertTrue(self.client.post('/api/presence',json={'available':True}).json['advisor_available'])
        self.assertFalse(any(c['id'].startswith('_system') for c in self.store.all()))

    def test_registration_notice_sent_and_failure_preserves_record(self):
        course=self.course('Python')
        self.store.save({'id':'tg-1','name':'Ana','telegram_id':1,'mode':'human','state':'interesado','messages':[],'revision':0,'registrations':[],'updated_at':'2026-10-29T00:00:00+00:00'})
        with patch('backend.telegram.send',side_effect=RuntimeError('offline')):
            result=self.client.post('/api/registrations',json={**self.person(course),'chat_id':'tg-1'})
        self.assertEqual(result.status_code,201)
        self.assertFalse(result.json['notified'])
        self.assertEqual(len(self.store.get('tg-1')['registrations']),1)

    def test_two_advisors_do_not_disconnect_each_other(self):
        self.client.post('/api/operators',json={'username':'second','name':'Segundo Operador','password':'second-password-test'})
        other=self.app.test_client()
        other.post('/api/login',json={'username':'second','password':'second-password-test'})
        self.client.post('/api/presence',json={'available':True})
        other.post('/api/presence',json={'available':True})
        self.client.post('/api/logout',json={})
        self.assertTrue(other.get('/api/settings').json['advisor_available'])
        self.assertTrue(other.get('/api/settings').json['self_available'])
        other.post('/api/presence',json={'available':False})
        self.assertFalse(other.get('/api/settings').json['advisor_available'])

