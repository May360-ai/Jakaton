"""Deterministic Telegram navigation; only explicit questions request inference."""
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

from backend.agents import normalized, triage


def button(label, action):
    return {'text': label, 'callback_data': action}


def keyboard(*rows):
    return {'inline_keyboard': list(rows)}


def home_keyboard():
    return keyboard([button('📚 Ver cursos', 'courses:0')],
                    [button('🧭 Ayúdame a elegir', 'guide')],
                    [button('❓ Preguntas frecuentes', 'faq'), button('👤 Hablar con asesor', 'human')])


def course_keyboard(course):
    cid = str(course['id'])
    rows = [[button('📖 Contenido', 'content:' + cid), button('🕒 Duración y disponibilidad', 'schedule:' + cid)],
            [button('💳 Precio e inscripción', 'enroll:' + cid)],
            [button('✅ Requisitos y certificado', 'requirements:' + cid)],
            [button('❓ Tengo una duda (IA)', 'ask:' + cid)],
            [button('👤 Hablar con asesor', 'human'), button('↩️ Más cursos', 'courses:0')]]
    return keyboard(*rows)


def available(courses):
    valid = [c for c in courses if c.get('titulo') and c['titulo'].strip().upper() != 'S/N']
    real = [c for c in valid if not c.get('is_demo') and c.get('estado') != 'DEMO']
    return real or valid


def label(action, courses):
    root, _, arg = action.partition(':')
    names = {'home': 'Menú principal', 'courses': 'Ver cursos', 'guide': 'Ayúdame a elegir',
             'faq': 'Preguntas frecuentes', 'human': 'Solicitar asesor humano', 'course': 'Ver curso',
             'content': 'Ver contenido', 'schedule': 'Duración y disponibilidad', 'enroll': 'Precio e inscripción',
             'explain': 'Explicar curso con IA', 'ask': 'Hacer una pregunta', 'level': 'Experiencia', 'goal': 'Objetivo'}
    course = next((c for c in courses if str(c['id']) == arg), None)
    return names.get(root, 'Opción del menú') + (': ' + course['titulo'] if course else ': ' + arg if arg else '')


def route(chat, text, courses, action=None):
    courses = available(courses)
    state, handover = triage(text)
    result = {'text': '', 'markup': home_keyboard(), 'infer': False, 'handover': False, 'state': state}
    clean = normalized(text.strip())
    if handover or action == 'human' or clean == '/asesor':
        chat['awaiting_question'] = False
        result.update(text='Te paso con un asesor. La atención automática queda pausada; no necesitas repetir tu consulta.', handover=True,
                      markup=None)
        return result
    if action is None and (chat.pop('awaiting_question', False)) and not clean.startswith('/'):
        result.update(infer=True, markup=None)
        return result
    if action is None:
        if clean in ['/cursos', 'cursos', 'ver cursos']:
            action = 'courses:0'
        elif clean in ['/ayuda', '/help', 'ayuda']:
            action = 'faq'
        else:
            action = 'home'
    root, _, arg = action.partition(':')
    chat['awaiting_question'] = False
    if root == 'home':
        result['text'] = '¡Hola! Soy el asistente de capacitación FISEI. Puedes consultar todo desde los botones. ¿Qué te gustaría hacer?'
    elif root == 'courses':
        try:
            page = max(0, min(int(arg), max(0, (len(courses) - 1) // 6)))
        except ValueError:
            page = 0
        batch = courses[page * 6:(page + 1) * 6]
        rows = [[button(('🧪 ' if c.get('is_demo') or c.get('estado') == 'DEMO' else '') + c['titulo'][:48], 'course:' + str(c['id']))] for c in batch]
        nav = []
        if page:
            nav.append(button('← Anteriores', 'courses:' + str(page - 1)))
        if (page + 1) * 6 < len(courses):
            nav.append(button('Siguientes →', 'courses:' + str(page + 1)))
        if nav:
            rows.append(nav)
        rows.append([button('🏠 Menú principal', 'home')])
        result.update(text='Selecciona un curso. 🧪 identifica ejemplos de demostración, no ofertas oficiales.' if batch else 'Todavía no hay cursos publicados. Puedes consultar con un asesor.', markup=keyboard(*rows))
    elif root == 'guide':
        result.update(text='¿Qué experiencia tienes? Elige una opción para que el asesor conozca tu punto de partida.',
                      markup=keyboard([button('🌱 Estoy empezando', 'level:principiante')], [button('🛠 Ya tengo bases', 'level:intermedio')], [button('🎓 Quiero especializarme', 'level:avanzado')], [button('🏠 Menú', 'home')]))
    elif root == 'level' and arg in ['principiante', 'intermedio', 'avanzado']:
        chat.setdefault('profile', {})['level'] = arg
        result.update(text='¿Cuál es tu objetivo principal?', markup=keyboard([button('💼 Mejorar mi perfil laboral', 'goal:empleo')], [button('🚀 Aplicarlo en un proyecto', 'goal:proyecto')], [button('📚 Aprender por interés', 'goal:aprendizaje')], [button('↩️ Volver', 'guide')]))
    elif root == 'goal' and arg in ['empleo', 'proyecto', 'aprendizaje']:
        chat.setdefault('profile', {})['goal'] = arg
        result.update(text='Listo: guardé tu objetivo para el asesor. Explora los cursos y consulta su contenido para decidir.', markup=keyboard([button('📚 Explorar cursos', 'courses:0')], [button('👤 Quiero orientación humana', 'human')], [button('🏠 Menú', 'home')]))
    elif root == 'faq':
        result.update(text='¿Qué información necesitas?', markup=keyboard([button('💳 Precios y descuentos', 'info:price')], [button('📝 Cómo inscribirme', 'info:enroll')], [button('📍 Modalidad y horarios', 'info:schedule')], [button('👤 Asesor', 'human'), button('🏠 Menú', 'home')]))
    elif root == 'info':
        answers = {'price': 'Solo comunicamos precios publicados. No tenemos descuentos ni gratuidad autorizados; un asesor puede confirmar las condiciones.',
                   'enroll': 'Selecciona un curso y pulsa «Precio e inscripción». Solo aparecerá un enlace si está cargado y el curso está abierto. Esto no confirma una matrícula ni un pago.',
                   'schedule': 'Los horarios y la modalidad deben estar confirmados en el catálogo. Si no están publicados, puedes pedirlos al asesor.'}
        result['text'] = answers.get(arg, 'Elige una opción del menú.')
    elif root in ['course', 'content', 'schedule', 'enroll', 'requirements', 'explain', 'ask']:
        course = next((c for c in courses if str(c['id']) == arg), None)
        if not course:
            result['text'] = 'Este curso ya no está disponible en el catálogo. Vuelve a consultar los cursos.'
            return result
        chat['selected_course'] = course['id']
        demo = bool(course.get('is_demo') or course.get('estado') == 'DEMO')
        note = '\n\n🧪 Ejemplo de demostración, sin oferta oficial confirmada.' if demo else ''
        details = course.get('details') or {}
        title = course['titulo'][:150]
        result['markup'] = course_keyboard(course)
        if root == 'course':
            result['text'] = f"{title}\n\nDuración: {course.get('duracion') or 'Por confirmar'}\nEstado: {course.get('estado') or 'Por confirmar'}\n\n¿Qué quieres consultar?" + note
        elif root == 'content':
            modules = details.get('modulos', [])
            result['text'] = title + '\n\n' + str(course.get('contenido') or 'El contenido detallado aún no está publicado. Un asesor puede confirmarlo.')[:1600] + ('\n\nMódulos:\n' + '\n'.join(f'{i}. {m}' for i, m in enumerate(modules, 1)) if modules else '') + note
        elif root == 'schedule':
            result['text'] = f"{title}\n\nDuración: {course.get('duracion') or 'Por confirmar'}\nEstado: {course.get('estado') or 'Por confirmar'}\nModalidad: {details.get('modalidad', 'Por confirmar')}\nFechas del curso: {details.get('course_start', 'Por confirmar')} al {details.get('course_end', 'Por confirmar')}\n\nHorario: {details.get('horario', 'Consultar con el asesor')}\n\nLugar: {details.get('lugar', 'Por confirmar')}" + note
        elif root == 'requirements':
            result['text'] = title + '\n\n' + details.get('requisitos', 'Requisitos: por confirmar.') + '\n\nMateriales: ' + ', '.join(details.get('materiales', [])) + '\n\n' + details.get('certificado', 'Certificado: por confirmar.') + note
        elif root == 'enroll':
            result['state'] = 'caliente'
            link = details.get('registration_url') or course.get('enlace') or ''
            parsed = urlparse(link)
            can_enroll = not demo and str(course.get('estado', '')).upper() == 'ABIERTO' and parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username
            today = datetime.now(timezone(timedelta(hours=-5))).date().isoformat()
            expired = bool(details.get('registration_end') and details['registration_end'] < today)
            not_started = bool(details.get('registration_start') and details['registration_start'] > today)
            can_enroll = can_enroll and not expired and not not_started
            rows = [[button('👤 Confirmar con un asesor', 'human')], [button('↩️ Volver al curso', 'course:' + arg)]]
            if can_enroll:
                rows.insert(0, [{'text': '📝 Abrir inscripción oficial', 'url': link}])
            prices = details.get('precios', [])
            price_text = '\n'.join(p['perfil'] + ': ' + p['valor'] + ' ' + p['moneda'] for p in prices) or 'Precio: por confirmar con el asesor.'
            registration = ('Inscripciones finalizadas el ' + details['registration_end'] + '. Consulta próximas ediciones con el asesor.') if expired else ('Inscripciones desde el ' + details['registration_start']) if not_started else 'Puedes consultar el enlace de inscripción publicado.' if can_enroll else 'La inscripción debe confirmarla un asesor.'
            result.update(text=title + '\n\n' + price_text + '\n\n' + registration + '\nNo se autorizan descuentos desde el bot.' + note, markup=keyboard(*rows))
        elif root in ['ask', 'explain']:
            chat['awaiting_question'] = True
            result['text'] = 'Escribe tu duda sobre ' + title + '. Usaré la IA solo para esta explicación. También puedes volver al menú sin escribir.'
    else:
        result['text'] = 'Esta opción ya no está disponible. Elige un botón del menú.'
    return result
