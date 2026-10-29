import os
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.store import Store
from backend.bot_flow import route
from database.import_catalog import parse_course


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'DASHBOARD_PASSWORD': 'test'})
        self.env.start()
        self.store = Store(':memory:')
        self.app = create_app(self.store)
        self.client = self.app.test_client()

    def tearDown(self):
        self.store.db.close()
        self.env.stop()

    def test_authenticated_course_create_edit_and_manual_enrollment(self):
        course = {'titulo': 'Curso pruebas', 'estado': 'ABIERTO', 'details': {'requisitos': 'Bases de programación', 'materiales': ['Computador']}}
        self.assertEqual(self.client.post('/api/courses', json=course).status_code, 401)
        self.client.post('/api/login', json={'password': 'test'})
        result = self.client.post('/api/courses', json=course)
        self.assertEqual(result.status_code, 201)
        cid = result.json['id']
        course['titulo'] = 'Curso actualizado'
        self.assertEqual(self.client.post(f'/api/courses/{cid}', json=course).json['titulo'], 'Curso actualizado')
        self.assertEqual(self.client.get('/api/courses').json[0]['titulo'], 'Curso actualizado')
        person = {'course_id': cid, 'name': 'Ana Pérez', 'identification': '1710034065', 'phone': '0991234567', 'email': 'ana@example.com'}
        result = self.client.post('/api/registrations', json=person)
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json['registration']['status'], 'pending_payment')
        self.assertEqual(self.client.post('/api/registrations', json=person).status_code, 409)
        self.assertEqual(self.client.post('/api/registrations', json={**person, 'identification': 'invalid'}).status_code, 400)
        course['estado'] = 'ARCHIVADO'
        self.client.post(f'/api/courses/{cid}', json=course)
        self.assertEqual(self.client.post('/api/registrations', json=person).status_code, 400)

    def test_requirements_include_full_source_panel(self):
        html = '<h1 class="tituloCurso">Redes</h1><div class="pc-tab"><section><div class="tab1"><ul><li><strong>Cupo:</strong>20</li><li><strong>Beneficiarios:</strong>Docentes</li><li><strong>Conocimientos previos:</strong>TCP/IP</li></ul></div></section></div>'
        course = parse_course(html, 'https://example.edu/course.php', '2026-10-29')
        course['id'] = 7
        result = route({}, '', [course], 'requirements:7')
        for text in ['Cupo:', '20', 'Beneficiarios:', 'Docentes', 'TCP/IP']:
            self.assertIn(text, result['text'])
        self.assertFalse(result['infer'])

    def test_bot_direct_registration_entry(self):
        courses = [{'id': 7, 'titulo': 'Curso', 'estado': 'PRÓXIMAMENTE', 'details': {}}]
        menu = route({}, '/inscribirme', courses)
        self.assertEqual(menu['markup']['inline_keyboard'][0][0]['callback_data'], 'register:7')
        chat = {}
        result = route(chat, '', courses, 'register:7')
        self.assertIn('nombre completo', result['text'])
        self.assertEqual(chat['registration_draft']['step'], 'name')
