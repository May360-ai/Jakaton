import os
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.store import Store


class TelegramNavigationTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'DASHBOARD_PASSWORD':''});self.env.start()
        self.store=Store(':memory:')
        self.app=create_app(self.store)
        self.receive=self.app.extensions['receive']
        self.send=patch('backend.telegram.send');self.sender=self.send.start()
        self.docs=patch('backend.telegram.send_document');self.docs.start()
        self.delete=patch('backend.telegram.delete_message');self.deleter=self.delete.start()

    def tearDown(self):
        self.docs.stop();self.send.stop();self.delete.stop();self.store.db.close();self.env.stop()

    def test_menu_replaced_only_on_telegram_history_kept_and_restart_safe(self):
        self.sender.return_value={'message_id':10}
        self.receive('tg-1','Ana','/start',1,'1')
        self.sender.return_value={'message_id':11}
        restarted=create_app(self.store)
        restarted.extensions['receive']('tg-1','Ana','',1,'2',action='courses:0')
        self.deleter.assert_called_once_with(1,10)
        chat=self.store.get('tg-1')
        self.assertEqual(len(chat['messages']),4)
        self.assertEqual(chat['telegram_navigation_ids'],[11])

    def test_payment_and_registration_status_messages_never_deleted(self):
        self.sender.return_value={'message_id':10}
        self.receive('tg-1','Ana','/start',1,'1')
        self.sender.return_value={'message_id':11}
        self.receive('tg-1','Ana','',1,'2',action='registrations')
        self.sender.return_value={'message_id':12}
        self.receive('tg-1','Ana','',1,'3',action='home')
        self.assertEqual(self.deleter.call_args_list,[unittest.mock.call(1,10)])
        self.assertEqual(len(self.store.get('tg-1')['messages']),6)

    def test_failed_send_does_not_delete_previous_menu(self):
        self.sender.return_value={'message_id':10}
        self.receive('tg-1','Ana','/start',1,'1')
        self.sender.side_effect=RuntimeError('offline')
        self.receive('tg-1','Ana','',1,'2',action='home')
        self.deleter.assert_not_called()
        self.assertEqual(self.store.get('tg-1')['telegram_navigation_ids'],[10])

    def test_registration_review_and_payment_order_are_protected(self):
        course={'id':7,'titulo':'Curso abierto','estado':'ABIERTO','details':{}}
        with patch.object(self.store,'courses',return_value=[course]):
            app=create_app(self.store)
        receive=app.extensions['receive']
        self.sender.side_effect=[{'message_id':i} for i in range(100,108)]
        receive('tg-1','Ana','/start',1,'1')
        receive('tg-1','Ana','',1,'2',action='register:7')
        for i,text in enumerate(['Ana Pérez','1710034065','0991234567','ana@example.com'],3):
            receive('tg-1','Ana',text,1,str(i))
        receive('tg-1','Ana','',1,'7',action='confirm_registration')
        receive('tg-1','Ana','',1,'8',action='home')
        removed=[call.args[1] for call in self.deleter.call_args_list]
        self.assertNotIn(105,removed)
        self.assertNotIn(106,removed)
        chat=self.store.get('tg-1')
        self.assertEqual(len(chat['messages']),16)
        self.assertEqual(chat['registrations'][0]['status'],'pending_payment')

    def test_failed_delete_does_not_stop_bot(self):
        self.sender.return_value={'message_id':10}
        self.receive('tg-1','Ana','/start',1,'1')
        self.sender.return_value={'message_id':11}
        self.deleter.side_effect=RuntimeError('expired')
        chat=self.receive('tg-1','Ana','',1,'2',action='home')
        self.assertIsNone(chat['error'])
        self.assertEqual(chat['mode'],'ai')
        self.assertEqual(chat['telegram_navigation_ids'],[10,11])
