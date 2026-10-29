import json
import os
import sqlite3
import threading
from pathlib import Path

import requests


class Store:
    """A local demo store or the same document model persisted in Supabase."""

    def __init__(self, path=None):
        self.lock = threading.RLock()
        self.url = os.getenv('SUPABASE_URL', '').rstrip('/')
        self.key = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
        self.remote = bool(self.url and self.key) and path is None
        if not self.remote:
            self.db = sqlite3.connect(path or str(Path(__file__).parent / 'demo.sqlite3'), check_same_thread=False)
            self.db.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS local_courses (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
            self.db.commit()

    def _request(self, method, table='conversations', **kwargs):
        response = requests.request(method, self.url + '/rest/v1/' + table, headers={
            'apikey': self.key, 'Authorization': 'Bearer ' + self.key,
            'Prefer': 'resolution=merge-duplicates,return=representation',
        }, timeout=15, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else []

    def courses(self, fallback):
        if not self.remote:
            rows = [json.loads(row[0]) for row in self.db.execute('SELECT data FROM local_courses ORDER BY id')]
            return rows or fallback
        return self._request('GET', table='courses', params={'select': 'id,titulo,estado,duracion,contenido,enlace,is_demo,details', 'order': 'is_demo.asc,id.asc', 'limit': '100'})

    def save_course(self, course, course_id=None):
        with self.lock:
            if self.remote:
                rows = self._request('PATCH', table='courses', params={'id': 'eq.' + str(course_id)}, json=course) if course_id is not None else self._request('POST', table='courses', json=course)
                if not rows:
                    raise ValueError('Curso inexistente')
                return rows[0]
            course_id = course_id or self.db.execute('SELECT coalesce(max(id),100)+1 FROM local_courses').fetchone()[0]
            row = {**course, 'id': course_id}
            self.db.execute('INSERT INTO local_courses VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (course_id, json.dumps(row, ensure_ascii=False)))
            self.db.commit()
            return row

    def all(self):
        with self.lock:
            if self.remote:
                return [row['data'] for row in self._request('GET', params={'select': 'data', 'order': 'updated_at.desc', 'limit': '200'})]
            return [json.loads(row[0]) for row in self.db.execute('SELECT data FROM documents')]

    def get(self, chat_id):
        with self.lock:
            if self.remote:
                rows = self._request('GET', params={'id': 'eq.' + str(chat_id), 'select': 'data'})
                return rows[0]['data'] if rows else None
            row = self.db.execute('SELECT data FROM documents WHERE id=?', (str(chat_id),)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, chat):
        with self.lock:
            if self.remote:
                self._request('POST', json={'id': chat['id'], 'data': chat, 'updated_at': chat['updated_at']})
            else:
                self.db.execute('INSERT INTO documents VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (chat['id'], json.dumps(chat, ensure_ascii=False)))
                self.db.commit()
