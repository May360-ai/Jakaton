import os
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.store import Store


class SessionTests(unittest.TestCase):
    def test_anonymous_cannot_read_or_mutate_dashboard(self):
        with patch.dict(os.environ, {'DASHBOARD_PASSWORD': 'contraseña-prueba'}):
            store = Store(':memory:')
            app = create_app(store)
            client = app.test_client()
            for path in ['/api/session', '/api/chats', '/api/courses', '/api/events', '/api/bot']:
                result = client.get(path)
                self.assertEqual(result.status_code, 401, path)
                self.assertIn('no-store', result.headers['Cache-Control'])
            for path in ['/api/demo', '/api/chats/x/reply', '/api/chats/x/mode', '/api/chats/x/registrations/y/paid']:
                self.assertEqual(client.post(path, json={}).status_code, 401, path)
            self.assertEqual(client.post('/api/login', json={'password': 'contraseña-prueba'}).status_code, 200)
            self.assertEqual(client.get('/api/session').status_code, 200)
            client.post('/api/logout', json={})
            self.assertEqual(client.get('/api/chats').status_code, 401)
            self.assertEqual(app.test_client().get('/api/session').status_code, 401)
            store.db.close()
