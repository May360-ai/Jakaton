"""Load the reviewed course fixture; preserves its database ID on subsequent runs."""
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.store import Store

ROOT = Path(__file__).resolve().parent.parent


def main():
    load_dotenv(ROOT / '.env')
    store = Store()
    if not store.remote:
        raise RuntimeError('Configura Supabase antes de cargar el curso')
    course = json.loads((ROOT / 'database/course16.json').read_text(encoding='utf-8'))
    rows = store._request('GET', table='courses', params={'enlace': 'eq.' + course['enlace'], 'select': 'id'})
    if rows:
        result = store._request('PATCH', table='courses', params={'id': 'eq.' + str(rows[0]['id'])}, json=course)
    else:
        result = store._request('POST', table='courses', json=course)
    print('Curso cargado:', result[0]['id'], result[0]['titulo'])
    loaded = store.courses([])
    verified = next(c for c in loaded if c['id'] == result[0]['id'])
    print('Verificado en Supabase:', len(verified['details']['modulos']), 'módulos;', len(verified['details']['precios']), 'tarifas')


if __name__ == '__main__':
    main()
