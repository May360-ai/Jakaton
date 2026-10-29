"""Build an operator briefing from recorded facts, without inference or personal IDs."""


def summarize(chat, courses):
    selected = next((c for c in courses if c['id'] == chat.get('selected_course')), None)
    course = selected['titulo'] if selected else None
    rows = chat.get('registrations') or []
    registration = next((r for r in reversed(rows) if r['course_id'] == chat.get('selected_course')), rows[-1] if rows else None)
    if not course and registration:
        course = registration['course_title']
    parts = ['Curso de interés: ' + course if course else 'Todavía no ha seleccionado un curso.']
    profile = chat.get('profile') or {}
    levels = {'principiante': 'Está empezando', 'intermedio': 'Tiene conocimientos básicos', 'avanzado': 'Busca especializarse'}
    goals = {'empleo': 'mejorar su perfil laboral', 'proyecto': 'aplicarlo en un proyecto', 'aprendizaje': 'aprender por interés personal'}
    if profile.get('level') in levels:
        parts.append('Experiencia: ' + levels[profile['level']] + '.')
    if profile.get('goal') in goals:
        parts.append('Objetivo: ' + goals[profile['goal']] + '.')
    if registration:
        parts.append('Inscripción: ' + ('pago confirmado.' if registration['status'] == 'paid' else 'comprobante pendiente de revisión.' if registration['status']=='pending_review' else 'pendiente de pago y comprobante.'))
    elif chat.get('registration_draft'):
        parts.append('Inscripción: está completando sus datos; todavía no confirmada.')
    elif chat.get('state') == 'caliente':
        parts.append('Intención: quiere inscribirse; aún no hay solicitud registrada.')
    candidates = [m['text'].strip() for m in chat.get('messages', []) if m.get('role') == 'user' and not m.get('private') and not m.get('action') and not m.get('text', '').startswith('/')]
    labels = ('Opción del menú', 'Ver curso', 'Ver contenido', 'Duración y disponibilidad', 'Precio e inscripción', 'Seleccionar', 'Experiencia:', 'Objetivo:', 'Menú principal', 'Solicitar asesor', 'Hacer una pregunta', 'Requisitos y certificado')
    candidates = [text for text in candidates if not text.startswith(labels)]
    if candidates:
        parts.append('Última consulta: ' + candidates[-1][:200])
    if registration and registration['status'] == 'pending_review':
        next_step = 'Revisar el archivo del comprobante y validar o rechazar la inscripción.'
    elif registration and registration['status'] == 'pending_payment':
        next_step = 'Solicitar una imagen o PDF del comprobante para que el encargado pueda revisarlo.'
    elif chat.get('registration_draft'):
        next_step = 'Ayudar a completar la solicitud de inscripción.'
    elif chat.get('mode') == 'human':
        next_step = 'Retomar la consulta; el bot está pausado.'
    elif not course:
        next_step = 'Preguntar qué quiere aprender y orientar hacia un curso.'
    elif chat.get('state') == 'caliente':
        next_step = 'Confirmar la edición e iniciar la inscripción desde este chat.'
    else:
        next_step = 'Resolver sus dudas sobre el curso y confirmar su objetivo.'
    parts.append('Siguiente paso: ' + next_step)
    return '\n\n'.join(parts)
