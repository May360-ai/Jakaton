"""Deterministic Telegram navigation; only explicit questions request inference."""
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

from backend.agents import normalized, triage
from backend import enrollment


def button(label, action):
    return {'text': label, 'callback_data': action}


def keyboard(*rows):
    return {'inline_keyboard': list(rows)}


def home_keyboard():
    return keyboard([button('📚 Ver cursos', 'courses:0')],
                    [button('📝 Inscribirme en un curso', 'enrollments')],
                    [button('🧭 Ayúdame a elegir', 'guide')],
                    [button('🤖 Conversar con el bot', 'bot_chat')],
                    [button('🧾 Mis inscripciones', 'registrations')],
                    [button('📄 Mis documentos PDF', 'documents')],
                    [button('📤 Enviar comprobante', 'proofs')],
                    [button('❓ Preguntas frecuentes', 'faq'), button('👤 Hablar con asesor', 'human')])


def course_keyboard(course):
    cid = str(course['id'])
    rows = [[button('📖 Contenido', 'content:' + cid), button('🕒 Duración y disponibilidad', 'schedule:' + cid)],
            [button('📝 Inscribirme aquí', 'register:' + cid)],
            [button('💳 Precio e inscripción', 'enroll:' + cid)],
            [button('✅ Requisitos y certificado', 'requirements:' + cid)],
            [button('❓ Tengo una duda (IA)', 'ask:' + cid)],
            [button('👤 Hablar con asesor', 'human'), button('↩️ Más cursos', 'courses:0')]]
    return keyboard(*rows)


def available(courses):
    today = datetime.now(timezone(timedelta(hours=-5))).date().isoformat()
    return [c for c in courses
            if c.get('titulo') and c['titulo'].strip().upper() != 'S/N'
            and c.get('estado') in ['ABIERTO', 'PRÓXIMAMENTE']
            and not ((c.get('details') or {}).get('registration_end')
                     and c['details']['registration_end'] < today)]


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
    if action == 'proofs':
        from backend.payment_proofs import eligible
        rows=eligible(chat)
        result['text']='Selecciona la inscripción y adjunta una imagen JPG/PNG o PDF del comprobante (hasta 5 MB). El encargado debe revisarlo; enviarlo no valida la inscripción.' if rows else 'No tienes inscripciones pendientes de comprobante.'
        result['markup']=keyboard(*[[button(r['course_title'][:55],'upload:'+r['id'])] for r in rows],[button('🏠 Menú','home')])
        return result
    if action and action.startswith('upload:'):
        from backend.payment_proofs import eligible
        rid=action.partition(':')[2]
        row=next((r for r in eligible(chat) if r['id']==rid),None)
        if not row:
            result['text']='Esta inscripción no admite otro comprobante.'
        elif chat.get('unassigned_proof'):
            result.update(attach_proof=rid,text='Comprobante recibido para '+row['course_title']+'. Queda pendiente de revisión por el encargado.',preserve=True)
        else:
            chat['proof_target']=rid
            result.update(text='Adjunta ahora la imagen o PDF del comprobante de '+row['course_title']+'. No realizar pagos reales con los datos ficticios de esta demostración.',preserve=True)
        return result
    if action == 'documents' or clean == '/documentos':
        rows = chat.get('registrations') or []
        result['text'] = 'Selecciona el PDF de tu curso. La orden no acredita pago; el comprobante solo aparece cuando el equipo confirma el pago.' if rows else 'Todavía no tienes inscripciones con documentos disponibles.'
        buttons = []
        for row in rows:
            buttons.append([button('📋 Orden · '+row['course_title'][:35], 'document:order:'+row['id'])])
            if row['status'] == 'paid':
                buttons.append([button('✅ Comprobante · '+row['course_title'][:30], 'document:receipt:'+row['id'])])
            else:
                buttons.append([button('📤 Adjuntar · '+row['course_title'][:35], 'upload:'+row['id'])])
        result['markup'] = keyboard(*buttons, [button('🏠 Menú', 'home')])
        return result
    if action and action.startswith('document:'):
        pieces = action.split(':')
        row = next((r for r in chat.get('registrations', []) if len(pieces)==3 and r['id']==pieces[2]), None)
        if not row or pieces[1] not in ['order','receipt'] or (pieces[1]=='receipt' and row['status']!='paid'):
            result['text'] = 'Ese documento no está disponible en tus inscripciones.'
        else:
            result['text'] = 'Aquí está el PDF de '+row['course_title']+'.'
            result['document'] = {'id':row['id'],'kind':pieces[1]}
            result['preserve'] = True
        return result
    if action and action.startswith('tariff:'):
        draft = chat.get('registration_draft') or {}
        course = next((c for c in courses if c['id'] == draft.get('course_id')), None)
        try:
            if not course or draft.get('step') != 'confirm':
                raise ValueError('Inicia primero una inscripción')
            price = enrollment.choose_price(chat, course, action.partition(':')[2])
            result['text'] = f"Tarifa elegida: {price['category']} — {price['amount']} {price['currency']}.\nConfirma tu solicitud para guardarla pendiente de pago. La categoría será verificada por el centro."
            result['markup'] = keyboard([button('✅ Confirmar solicitud', 'confirm_registration')], [button('Cancelar', 'cancel_registration')])
        except ValueError as error:
            result['text'] = str(error)
        return result
    if action == 'cancel_registration' or clean == '/cancelar':
        chat.pop('registration_draft', None)
        result['text'] = 'Solicitud cancelada. Puedes volver a elegir un curso.'
        return result
    if chat.get('registration_draft') and action is None and not clean.startswith('/'):
        result['text'], ready = enrollment.collect(chat, text)
        result['markup'] = keyboard([button('✅ Confirmar solicitud', 'confirm_registration')], [button('Cancelar', 'cancel_registration')]) if ready else keyboard([button('Cancelar', 'cancel_registration')])
        if ready:
            course = next((c for c in courses if c['id'] == chat['registration_draft']['course_id']), None)
            prices = ((course or {}).get('details') or {}).get('precios') or []
            if prices:
                result['text'] += '\n\nSelecciona la categoría que te corresponde. El centro verificará tu condición antes del pago.'
                result['markup'] = keyboard(*[[button(f"{p['perfil'][:38]} · {p['valor']} {p.get('moneda', 'USD')}", 'tariff:' + str(i))] for i, p in enumerate(prices)], [button('Cancelar', 'cancel_registration')])
        return result
    if action == 'confirm_registration':
        draft = chat.get('registration_draft') or {}
        course = next((c for c in courses if c['id'] == draft.get('course_id')), None)
        prices = ((course or {}).get('details') or {}).get('precios') or []
        if prices and not draft.get('price'):
            result['text'] = 'Antes de confirmar, selecciona tu categoría y tarifa.'
            result['markup'] = keyboard(*[[button(f"{p['perfil'][:38]} · {p['valor']} {p.get('moneda', 'USD')}", 'tariff:' + str(i))] for i,p in enumerate(prices)])
            return result
        row = enrollment.confirm(chat, course) if course else None
        result['text'] = (f"Solicitud {row['id']} registrada para {row['course_title']}.\nEstado: PENDIENTE DE PAGO.\nAcércate al CTT-FISEI, Campus Huachi, para verificar disponibilidad y realizar el pago de tu inscripción. El equipo confirmará el pago en el sistema. No realices pagos por este chat.\nTu cupo y la apertura de la edición deben ser confirmados por el centro." if row else 'Esta solicitud ya fue procesada o venció. Consulta Mis inscripciones.')
        result['state'] = 'caliente'
        if row and course.get('is_demo'):
            result['text'] = f"Solicitud de DEMOSTRACIÓN {row['id']} registrada para {row['course_title']}. Estado: PENDIENTE DE PAGO (simulado). No debes realizar ningún pago real por esta prueba."
        if row:
            result['text'] = enrollment.notice(row)
            result['send_order'] = row['id']
            result['markup'] = keyboard([button('📤 Enviar comprobante','upload:'+row['id'])],[button('📄 Mis documentos PDF','documents')],[button('🏠 Menú','home')])
        return result
    if action == 'registrations':
        rows = chat.get('registrations', [])
        result['text'] = '\n\n'.join(f"{r['course_title']}\nCódigo: {r['id']}\nEstado: {'Inscripción validada' if r['status'] == 'paid' else 'Comprobante pendiente de revisión' if r['status']=='pending_review' else 'Pendiente de comprobante de pago'}" for r in rows) or 'Todavía no tienes solicitudes registradas.'
        return result
    if action == 'bot_chat' or clean == '/bot':
        chat.pop('registration_draft', None)
        chat['bot_chat'] = True
        result.update(text='Ya puedes conversar con el asistente. Escribe tu pregunta; usaré el catálogo para responder. Para volver a los botones, pulsa Menú principal.', markup=keyboard([button('🏠 Menú principal', 'home')], [button('👤 Solicitar asesor', 'human')]))
        return result
    if handover or action == 'human' or clean == '/asesor':
        chat['awaiting_question'] = False
        result.update(text='Te paso con un asesor. La atención automática queda pausada; no necesitas repetir tu consulta.', handover=True,
                      markup=None)
        return result
    if action is None and (chat.pop('awaiting_question', False) or chat.get('bot_chat')) and not clean.startswith('/'):
        result.update(infer=True, markup=None)
        return result
    if action is None and clean and not clean.startswith('/') and clean not in ['hola','gracias','ok','listo','menu','cursos','ver cursos','inscribirme','ayuda']:
        selected = next((c for c in courses if c['id'] == chat.get('selected_course')), None)
        if selected:
            cid = str(selected['id'])
            if any(word in clean for word in ['ensena','ensenar','aprende','aprender','contenido','temario','que se ve']):
                action = 'content:' + cid
            elif any(word in clean for word in ['precio','cuesta','costo','valor']):
                action = 'enroll:' + cid
            elif any(word in clean for word in ['horario','duracion','cuanto dura','modalidad','presencial','virtual']):
                action = 'schedule:' + cid
            elif any(word in clean for word in ['requisito','necesito saber','certificado','materiales']):
                action = 'requirements:' + cid
            elif any(word in clean for word in ['quiero inscrib','como me inscrib','registrarme']):
                action = 'register:' + cid
            else:
                chat['bot_chat'] = True
                result.update(infer=True,markup=course_keyboard(selected))
                return result
        elif any(word in clean for word in ['que ','como ','por que','puedo ','explica','duda','?']):
            result.update(infer=True,markup=home_keyboard())
            return result
    if action is None:
        if clean in ['/inscribirme', 'inscribirme']:
            action = 'enrollments'
        elif clean in ['/cursos', 'cursos', 'ver cursos']:
            action = 'courses:0'
        elif clean in ['/ayuda', '/help', 'ayuda']:
            action = 'faq'
        else:
            action = 'home'
    root, _, arg = action.partition(':')
    chat.pop('registration_draft', None)
    chat['bot_chat'] = False
    chat['awaiting_question'] = False
    if root == 'home':
        result['text'] = '¡Hola! Soy el asistente de capacitación FISEI. Puedes consultar todo desde los botones. ¿Qué te gustaría hacer?'
    elif root == 'enrollments':
        eligible = [c for c in courses if c.get('estado') in ['ABIERTO', 'PRÓXIMAMENTE']]
        result['text'] = 'Selecciona un curso para inscribirte desde este chat. Tu solicitud quedará pendiente de pago.' if eligible else 'No hay ediciones abiertas o próximas que admitan inscripción. Puedes explorar el catálogo o consultar al centro.'
        result['markup'] = keyboard(*[[button(c['titulo'][:55], 'register:' + str(c['id']))] for c in eligible[:30]], [button('🏠 Menú principal', 'home')])
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
    elif root in ['course', 'content', 'schedule', 'enroll', 'register', 'requirements', 'explain', 'ask']:
        content_page = 0
        if root in ['content', 'requirements'] and ':' in arg:
            arg, page = arg.split(':', 1)
            try:
                content_page = max(0, int(page))
            except ValueError:
                content_page = 0
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
        if root == 'register':
            if course.get('estado') not in ['ABIERTO', 'PRÓXIMAMENTE']:
                result['text'] = 'Esta edición no admite solicitudes: sus fechas terminaron o faltan datos de apertura. Selecciona un curso próximo o abierto.'
            elif any(r['course_id'] == course['id'] for r in chat.get('registrations', [])):
                result['text'] = 'Ya tienes una solicitud para este curso. Consulta Mis inscripciones en el menú.'
            else:
                result['text'] = enrollment.begin(chat, course)
                result['markup'] = keyboard([button('Cancelar', 'cancel_registration')])
        elif root == 'course':
            result['text'] = f"{title}\n\nDuración: {course.get('duracion') or 'Por confirmar'}\nEstado: {course.get('estado') or 'Por confirmar'}\n\n¿Qué quieres consultar?" + note
        elif root == 'content':
            modules = details.get('modulos', [])
            full = str(course.get('contenido') or 'El contenido detallado aún no está publicado. Un asesor puede confirmarlo.') + ('\n\nContenido:\n' + '\n'.join(f'{i}. {m}' for i, m in enumerate(modules, 1)) if modules else '')
            pages = []
            while full:
                cut = full.rfind('\n', 0, 2900) if len(full) > 2900 else len(full)
                if cut <= 0:
                    cut = min(2900, len(full))
                pages.append(full[:cut])
                full = full[cut:].lstrip('\n')
            content_page = min(content_page, len(pages) - 1)
            result['text'] = title + f'\n\nContenido ({content_page + 1}/{len(pages)})\n\n' + pages[content_page] + note
            nav = []
            if content_page:
                nav.append(button('← Contenido anterior', f'content:{arg}:{content_page - 1}'))
            if content_page + 1 < len(pages):
                nav.append(button('Más contenido →', f'content:{arg}:{content_page + 1}'))
            if nav:
                result['markup']['inline_keyboard'].insert(0, nav)
        elif root == 'schedule':
            result['text'] = f"{title}\n\nDuración: {course.get('duracion') or 'Por confirmar'}\nEstado: {course.get('estado') or 'Por confirmar'}\nModalidad: {details.get('modalidad', 'Por confirmar')}\nFechas del curso: {details.get('course_start', 'Por confirmar')} al {details.get('course_end', 'Por confirmar')}\n\nHorario: {details.get('horario', 'Consultar con el asesor')}\n\nLugar: {details.get('lugar', 'Por confirmar')}" + note
        elif root == 'requirements':
            full = (details.get('requisitos_detalles_texto') or 'Conocimientos previos:\n' + details.get('requisitos', 'No especificados en la ficha oficial.')) + '\n\nMateriales y equipos:\n' + ('\n'.join('• ' + item for item in details.get('materiales', [])) or 'No especificados en la ficha oficial.')
            pages = [full[i:i+2800] for i in range(0, len(full), 2800)]
            content_page = min(content_page, len(pages)-1)
            result['text'] = title + f'\n\nRequisitos y detalles ({content_page+1}/{len(pages)})\n' + pages[content_page] + note
            nav = []
            if content_page:
                nav.append(button('← Anterior', f'requirements:{arg}:{content_page-1}'))
            if content_page+1 < len(pages):
                nav.append(button('Más requisitos →', f'requirements:{arg}:{content_page+1}'))
            if nav:
                result['markup']['inline_keyboard'].insert(0, nav)
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
            if course.get('estado') in ['ABIERTO', 'PRÓXIMAMENTE'] and not expired:
                rows.insert(0, [button('📝 Registrar solicitud · pendiente de pago', 'register:' + arg)])
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
