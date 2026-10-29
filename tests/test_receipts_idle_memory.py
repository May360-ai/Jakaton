import base64
import os
import time
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.store import Store
from backend.bot_flow import route


class ReceiptIdleMemoryTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'DASHBOARD_PASSWORD':'test'});self.env.start()
        self.store=Store(':memory:');self.app=create_app(self.store);self.client=self.app.test_client()
        self.client.post('/api/login',json={'password':'test'})

    def tearDown(self):
        self.store.db.close();self.env.stop()

    def chat(self):
        return {'id':'tg-1','telegram_id':1,'name':'Ana','mode':'human','state':'caliente','revision':1,'messages':[],'updated_at':'2026-10-29T00:00:00+00:00','operator_last_activity':time.time(),'registrations':[{'id':'reg-1','course_id':1,'course_title':'Fundamentos de Python','name':'Ana Pérez','identification':'1710034065','status':'pending_review','proofs':[{'id':'proof-1','status':'received','mime':'application/pdf','content':'JVBERi0='}],'price':{'category':'Público','amount':'120.00','currency':'USD'},'is_demo':True}]}

    def test_pdf_saved_private_download_sent_once_and_retry(self):
        self.store.save(self.chat());url='/api/chats/tg-1/registrations/reg-1/paid'
        with patch('backend.telegram.send_document',side_effect=RuntimeError('offline')):
            result=self.client.post(url,json={})
        self.assertEqual(result.status_code,200);self.assertFalse(result.json['notified'])
        row=self.store.get('tg-1')['registrations'][0]
        self.assertTrue(base64.b64decode(row['receipt_pdf']).startswith(b'%PDF'))
        number=row['receipt_number'];self.assertEqual(row['status'],'paid')
        with patch('backend.telegram.send_document') as send:
            retry=self.client.post(url,json={});self.assertTrue(retry.json['notified'])
            self.client.post(url,json={});self.assertEqual(send.call_count,1)
        self.assertEqual(self.store.get('tg-1')['registrations'][0]['receipt_number'],number)
        pdf=self.client.get('/api/chats/tg-1/registrations/reg-1/receipt')
        self.assertEqual(pdf.mimetype,'application/pdf');self.assertTrue(pdf.data.startswith(b'%PDF'))
        self.assertEqual(self.app.test_client().get('/api/chats/tg-1/registrations/reg-1/receipt').status_code,401)
        public=self.client.get('/api/chats').json[0]['registrations'][0]
        self.assertNotIn('receipt_pdf',public);self.assertTrue(public['receipt_available'])

    def test_idle_release_after_five_minutes_not_before(self):
        chat=self.chat();chat['operator_last_activity']=time.time()-299;self.store.save(chat)
        with patch('backend.telegram.send'):
            self.app.extensions['expire_handovers']()
            self.assertEqual(self.store.get('tg-1')['mode'],'human')
            chat=self.store.get('tg-1');chat['operator_last_activity']=time.time()-301;self.store.save(chat)
            self.app.extensions['expire_handovers']()
        current=self.store.get('tg-1');self.assertEqual(current['mode'],'ai');self.assertTrue(current['bot_chat'])
        self.assertEqual(current['revision'],2)

    def test_pdf_generation_failure_does_not_confirm_payment(self):
        self.store.save(self.chat())
        with patch('backend.receipts.build',side_effect=RuntimeError('render failed')):
            result=self.client.post('/api/chats/tg-1/registrations/reg-1/paid',json={})
        self.assertEqual(result.status_code,502)
        self.assertEqual(self.store.get('tg-1')['registrations'][0]['status'],'pending_review')

    def test_operator_message_resets_timer(self):
        chat=self.chat();chat['operator_last_activity']=time.time()-250;self.store.save(chat)
        with patch('backend.telegram.send'):
            self.client.post('/api/chats/tg-1/reply',json={'text':'Estoy revisando tu solicitud'})
        self.assertGreater(self.store.get('tg-1')['operator_last_activity'],time.time()-5)

    def test_course_context_answers_without_forcing_menu(self):
        courses=[{'id':21,'titulo':'Python 2','estado':'PRÓXIMAMENTE','contenido':'Clases, objetos y archivos','details':{}}]
        chat={}
        route(chat,'',courses,'course:21')
        answer=route(chat,'que se va a enseñar en este curso',courses)
        self.assertIn('Clases, objetos y archivos',answer['text']);self.assertFalse(answer['infer'])
        follow=route(chat,'¿Para qué sirve la herencia?',courses)
        self.assertTrue(follow['infer']);self.assertEqual(chat['selected_course'],21)

    def test_course_memory_survives_app_recreation(self):
        courses=[{'id':21,'titulo':'Python 2','estado':'PRÓXIMAMENTE','contenido':'Clases, objetos y archivos','details':{}}]
        with patch.object(self.store,'courses',return_value=courses),patch('backend.telegram.send'),patch('backend.agents.answer') as model:
            first=create_app(self.store)
            first.extensions['receive']('tg-memory','Ana','',1,'1',action='course:21')
            second=create_app(self.store)
            chat=second.extensions['receive']('tg-memory','Ana','que se va a enseñar en este curso',1,'2')
            self.assertEqual(chat['selected_course'],21)
            self.assertIn('Clases, objetos y archivos',chat['messages'][-1]['text'])
            model.assert_not_called()
