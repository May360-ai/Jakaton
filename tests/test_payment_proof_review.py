from io import BytesIO
import os
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.store import Store
from backend import payment_proofs


class PaymentProofReviewTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'DASHBOARD_PASSWORD':'test'});self.env.start()
        self.store=Store(':memory:');self.app=create_app(self.store);self.client=self.app.test_client()
        self.client.post('/api/login',json={'password':'test'})
        self.send=patch('backend.telegram.send',return_value={'message_id':100});self.send.start()
        self.deletions=patch('backend.telegram.delete_message');self.deletions.start()
        self.documents=patch('backend.telegram.send_document');self.documents.start()
        self.store.save({'id':'tg-1','telegram_id':1,'name':'Ana','mode':'human','state':'interesado','revision':0,'messages':[],'updates':[],'updated_at':'2026-10-29T00:00:00+00:00','registrations':[self.row('one'),self.row('two')]})

    def row(self,rid):
        return {'id':rid,'course_id':1 if rid=='one' else 2,'course_title':'Curso '+rid,'name':'Ana Pérez','identification':'1710034065','status':'pending_payment','created_at':'2026-10-29T00:00:00+00:00','price':{'amount':'120.00','currency':'USD','category':'Público'},'payment_demo':True}

    def tearDown(self):
        self.send.stop();self.deletions.stop();self.documents.stop();self.store.db.close();self.env.stop()

    def test_upload_review_reject_resubmit_and_validate(self):
        base='/api/chats/tg-1/registrations/one'
        self.assertEqual(self.client.post(base+'/paid',json={}).status_code,409)
        invalid=self.client.post(base+'/proofs',data={'file':(BytesIO(b'<html>not a PDF</html>'),'proof.pdf')})
        self.assertEqual(invalid.status_code,400)
        upload=self.client.post(base+'/proofs',data={'file':(BytesIO(b'%PDF-1.4 test'),'proof.pdf')})
        self.assertEqual(upload.status_code,201)
        self.assertEqual(self.store.get('tg-1')['registrations'][0]['status'],'pending_review')
        proof_id=upload.json['proof']['id']
        self.assertEqual(self.client.post(base+'/paid',json={'proof_id':'stale-file'}).status_code,409)
        self.assertEqual(self.app.test_client().get(base+'/proofs/'+proof_id).status_code,401)
        self.assertEqual(self.client.get(base+'/proofs/'+proof_id).mimetype,'application/pdf')
        self.assertEqual(self.client.post(base+'/reject',json={'reason':'No se lee el importe'}).status_code,200)
        self.assertEqual(self.store.get('tg-1')['registrations'][0]['status'],'pending_payment')
        self.assertEqual(self.client.post(base+'/paid',json={}).status_code,409)
        self.client.post(base+'/proofs',data={'file':(BytesIO(b'%PDF-1.4 replacement'),'new.pdf')})
        result=self.client.post(base+'/paid',json={})
        self.assertEqual(result.status_code,200)
        row=self.store.get('tg-1')['registrations'][0]
        self.assertEqual(row['enrollment_status'],'validated')
        self.assertEqual([p['status'] for p in row['proofs']],['rejected','approved'])
        self.assertNotIn('content',result.json['registration']['proofs'][-1])
        self.assertEqual(self.store.get('tg-1')['registrations'][1]['status'],'pending_payment')

    def test_telegram_asks_which_course_and_never_uses_ai(self):
        file=payment_proofs.pack(b'%PDF-1.4 test')
        with patch('backend.telegram.download_proof',return_value=file),patch('backend.agents.answer') as ai:
            chat=self.app.extensions['receive']('tg-1','Ana','Archivo adjunto',1,'1',proof_file={'file_id':'file'})
            self.assertIn('unassigned_proof',chat)
            self.assertEqual([r['status'] for r in chat['registrations']],['pending_payment','pending_payment'])
            public=self.client.get('/api/chats').json[0]
            self.assertNotIn('content',public['unassigned_proof'])
            chat=self.app.extensions['receive']('tg-1','Ana','',1,'2',action='upload:two')
            self.assertEqual(chat['mode'],'human')
            self.assertEqual([r['status'] for r in chat['registrations']],['pending_payment','pending_review'])
            ai.assert_not_called()
        self.assertEqual(self.client.get('/api/analytics').json['review'],1)

    def test_wrong_type_or_oversized_proof_rejected(self):
        for content in [b'<script>bad</script>',b'x'*(5*1024*1024+1)]:
            with self.assertRaises(ValueError):payment_proofs.pack(content)

    def test_order_is_generated_and_sent_at_registration_not_at_payment(self):
        course={'id':7,'titulo':'Curso abierto','estado':'ABIERTO','details':{}}
        with patch.object(self.store,'courses',return_value=[course]):
            app=create_app(self.store)
        with patch('backend.telegram.send_document') as document:
            receive=app.extensions['receive']
            receive('tg-new','Ana','',2,'1',action='register:7')
            for i,text in enumerate(['Ana Pérez','1710034065','0991234567','ana@example.com'],2):
                receive('tg-new','Ana',text,2,str(i))
            chat=receive('tg-new','Ana','',2,'6',action='confirm_registration')
        row=chat['registrations'][0]
        self.assertEqual(row['status'],'pending_payment')
        self.assertTrue(document.call_args.args[1].startswith(b'%PDF'))
        self.assertTrue(document.call_args.args[2].startswith('ORD-'))
        self.assertIn('FICTICIO',row['bank']['bank'])
        self.assertNotIn('receipt_pdf',row)
