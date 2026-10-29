"""Import linked university course pages without LLMs or fabricated fields."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv
import requests
import urllib3

from backend.agents import normalized
from backend.store import Store

ROOT = Path(__file__).resolve().parent.parent
BASE = 'https://ctt-talleresfisei.uta.edu.ec/'
MONTHS = {name: i for i, name in enumerate(['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'], 1)}


def dates(text):
    clean = normalized(text)
    matches = re.findall(r'(\d{1,2})\s+de\s+(\w+)(?:\s+(?:de|del)\s+(\d{4}))?', clean)
    years = re.findall(r'\b(20\d{2})\b', clean)
    if not years:
        return []
    result = []
    for day, month, year in matches:
        if month in MONTHS:
            result.append(datetime(int(year or years[-1]), MONTHS[month], int(day)).date().isoformat())
    if len(result) == 1:
        first = re.search(r'(\d{1,2})\s+al\s+\d{1,2}\s+de', clean)
        if first:
            result.insert(0, result[0][:8] + f'{int(first[1]):02}')
    return result


def parse_course(html, url, today):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.select_one('.tituloCurso')
    if not title:
        raise ValueError('La página no contiene un título de curso')
    title = title.get_text(' ', strip=True)
    fields = {}
    panel = soup.select_one('.pc-tab section .tab1')
    if panel:
        for li in panel.select('li'):
            strong = li.find(['strong', 'b'])
            if strong:
                key = strong.get_text(' ', strip=True)
                value = li.get_text(' ', strip=True)[len(key):].strip()
                fields[normalized(key).rstrip(':')] = value
    def field(*names):
        return next((fields[n] for n in names if fields.get(n)), None)
    def items(selector):
        return [x.get_text(' ', strip=True) for x in soup.select(selector)]
    description = soup.select_one('.descripcion p')
    details = {'source_url': url, 'source_checked_on': today, 'source_fields': fields,
               'modulos': items('.pc-tab section .tab3 li'), 'materiales': items('.pc-tab section .tab2 li'),
               'objetivos': items('.objetivos li'), 'precios': []}
    for target, names in {
        'horario': ('horario', 'horarios'), 'modalidad': ('modalidad',), 'lugar': ('lugar del evento',),
        'cupo': ('cupo de inscripciones', 'el cupo de inscripciones'), 'requisitos': ('conocimientos previos',),
        'certificado': ('tipo de capacitacion',),
    }.items():
        value = field(*names)
        if value:
            details[target] = value
    if panel:
        for li in panel.select('li'):
            value = li.get_text(' ', strip=True)
            price = re.match(r'(.+?):\s*\$\s*(\d+(?:[.,]\d{1,2})?)\s*$', value)
            if price:
                details['precios'].append({'perfil': price[1].strip(), 'valor': f'{float(price[2].replace(",", ".")):.2f}', 'moneda': 'USD'})
            else:
                price = re.match(r'\$?\s*(\d+(?:[.,]\d{1,2})?)\s+USD\s+(.+)$', value, re.I)
                if price:
                    details['precios'].append({'perfil': price[2].strip(), 'valor': f'{float(price[1].replace(",", ".")):.2f}', 'moneda': 'USD'})
        note = panel.find('small')
        if note:
            details['certificado'] = (details.get('certificado', '') + ' ' + note.get_text(' ', strip=True)).strip()
    registrations = field('fechas de inscripciones', 'fecha de inscripciones', 'inscripciones') or ''
    period = field('fecha del curso', 'fechas del curso') or ''
    details['registration_dates_text'] = registrations
    details['course_dates_text'] = period
    for prefix, text in [('registration', registrations), ('course', period)]:
        parsed = dates(text)
        if len(parsed) == 2:
            details[prefix + '_start'], details[prefix + '_end'] = parsed
    link = soup.select_one('.registro a[href], #inscripcion a[href]')
    if link:
        candidate = urljoin(url, link['href'])
        if urlparse(candidate).scheme == 'https':
            details['registration_url'] = candidate
    end = details.get('registration_end')
    start = details.get('registration_start')
    # A form alone does not prove that registration is currently open.
    state = 'INSCRIPCIONES FINALIZADAS' if end and end < today else 'PRÓXIMAMENTE' if start and start > today else 'ABIERTO' if start and end and start <= today <= end else 'POR CONFIRMAR'
    return {'titulo': title, 'estado': state, 'duracion': field('horas de capacitacion'),
            'contenido': description.get_text(' ', strip=True) if description else None,
            'enlace': url, 'is_demo': False, 'details': details}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-unverified-source-tls', action='store_true', help='For the public university source only; Supabase TLS is always verified')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    store = Store()
    if not store.remote:
        raise RuntimeError('Configura Supabase antes de importar')
    today = datetime.now(timezone(timedelta(hours=-5))).date().isoformat()
    def fetch(url):
        if urlparse(url).hostname != urlparse(BASE).hostname:
            raise ValueError('Solo se permiten páginas de la universidad')
        if args.allow_unverified_source_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(url, timeout=(8, 25), verify=not args.allow_unverified_source_tls)
        response.raise_for_status()
        return response.content.decode('utf-8')
    links = set()
    for page in ['course.php', 'index.php']:
        soup = BeautifulSoup(fetch(urljoin(BASE, page)), 'html.parser')
        for a in soup.select('a[href]'):
            url = urljoin(BASE, a['href'])
            if urlparse(url).hostname == urlparse(BASE).hostname and re.fullmatch(r'/curso\d+\.php', urlparse(url).path):
                links.add(url.split('?')[0].split('#')[0])
    records, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {pool.submit(fetch, url): url for url in sorted(links)}
        for task in as_completed(tasks):
            url = tasks[task]
            try:
                records.append(parse_course(task.result(), url, today))
            except Exception as error:
                errors.append({'url': url, 'error': type(error).__name__})
    records.sort(key=lambda c: int(re.search(r'curso(\d+)', c['enlace'])[1]))
    existing = {c['enlace']: c for c in store.courses([])}
    for course in records:
        # Preserve previously reviewed details when a legacy page omits a field.
        old = existing.get(course['enlace'])
        if old:
            details = {**(old.get('details') or {}), **{k: v for k, v in course['details'].items() if v not in [None, '', [], {}]}}
            course['details'] = details
            store._request('PATCH', table='courses', params={'id': 'eq.' + str(old['id'])}, json=course)
        else:
            store._request('POST', table='courses', json=course)
    (ROOT / 'database/catalog.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report = {'discovered': len(links), 'imported': len(records), 'errors': errors,
              'courses': [{'title': c['titulo'], 'state': c['estado'], 'modules': len(c['details']['modulos']), 'prices': len(c['details']['precios'])} for c in records]}
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
