import os
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.store import Store
from backend.bot_flow import route


class CourseDocumentsTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'DASHBOARD_PASSWORD':'test'});self.env.start()
        self.store=Store(':memory:');self.app=create_app(self.store);self.client=self.app.test_client()
        self.client.post('/api/login',json={'password':'test'})
        self.store.save({'id':'tg-1','telegram_id':1,'name':'Ana','mode':'human','state':'interesado','revision':0,'messages':[],'updates':[],'updated_at':'2026-10-29T00:00:00+00:00','registrations':[{'id':'one','course_id':1,'course_title':'Python','name':'Ana Pérez','identification':'1710034065','status':'pending_payment','created_at':'2026-10-29T00:00:00+00:00','price':{'amount':'120.00','currency':'USD','category':'Público'}},{'id':'two','course_id':2,'course_title':'Redes','name':'Ana Pérez','identification':'1710034065','status':'paid','paid_at':'2026-10-29T00:00:00+00:00','price':{}}]})

    def tearDown(self):
        self.store.db.close();self.env.stop()

    def test_order_inline_private_and_never_confirms_payment(self):
        url='/api/chats/tg-1/registrations/one/order'
        self.assertEqual(self.app.test_client().get(url).status_code,401)
        result=self.client.get(url+'?inline=1')
        self.assertEqual(result.status_code,200)
        self.assertTrue(result.data.startswith(b'%PDF'))
        self.assertTrue(result.headers['Content-Disposition'].startswith('inline;'))
        self.assertEqual(self.store.get('tg-1')['registrations'][0]['status'],'pending_payment')
        public=self.client.get('/api/chats').json[0]['registrations'][0]
        self.assertTrue(public['order_available']);self.assertNotIn('order_pdf',public)

    def test_my_documents_is_per_course_and_blocks_foreign_or_unpaid_receipts(self):
        chat=self.store.get('tg-1')
        result=route(chat,'',[],'documents')
        actions=[b['callback_data'] for row in result['markup']['inline_keyboard'] for b in row]
        self.assertIn('document:order:one',actions)
        self.assertIn('document:receipt:two',actions)
        self.assertNotIn('document:receipt:one',actions)
        self.assertNotIn('document',route(chat,'',[],'document:receipt:one'))
        self.assertNotIn('document',route(chat,'',[],'document:order:other-person'))

    def test_request_pdf_from_telegram_keeps_human_mode_and_pdf_out_of_cleanup(self):
        with patch('backend.telegram.send_document') as pdf,patch('backend.telegram.send',return_value={'message_id':123}),patch('backend.telegram.delete_message'),patch('backend.agents.answer') as model:
            chat=self.app.extensions['receive']('tg-1','Ana','',1,'100',action='document:receipt:two')
        self.assertEqual(chat['mode'],'human')
        self.assertEqual(pdf.call_count,1)
        self.assertTrue(pdf.call_args.args[1].startswith(b'%PDF'))
        self.assertEqual(chat.get('telegram_navigation_ids'),[])
        model.assert_not_called()
        self.assertEqual(chat['registrations'][0]['status'],'pending_payment')
