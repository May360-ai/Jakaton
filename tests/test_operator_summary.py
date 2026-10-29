import unittest
from backend.operator_summary import summarize


class OperatorSummaryTests(unittest.TestCase):
    def test_summary_uses_course_names_and_next_action(self):
        chat = {'selected_course': 5, 'profile': {'level':'principiante','goal':'empleo'}, 'mode':'human','messages':[{'role':'user','text':'¿Puedo empezar sin saber programar?'},{'role':'user','text':'Opción del menú:5','action':'course:5'},{'role':'user','text':'1710034065','private':True}]}
        text = summarize(chat, [{'id':5,'titulo':'Fundamentos de Python 1'}])
        self.assertIn('Fundamentos de Python 1',text)
        self.assertIn('Está empezando',text)
        self.assertIn('mejorar su perfil laboral',text)
        self.assertIn('¿Puedo empezar',text)
        self.assertNotIn('1710034065',text)
        self.assertNotIn('{}',text)
        self.assertIn('bot está pausado',text)

    def test_pending_payment_next_step(self):
        text = summarize({'registrations':[{'course_id':2,'course_title':'Redes','status':'pending_payment'}]},[])
        self.assertIn('pendiente de pago',text)
        self.assertIn('comprobante',text)
