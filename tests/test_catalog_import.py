import unittest

from database.import_catalog import dates, parse_course
from backend.bot_flow import route


class CatalogImportTests(unittest.TestCase):
    def test_missing_requirements_are_not_invented(self):
        course = parse_course('<h1 class="tituloCurso">Python</h1>', 'https://example.edu/course.php', '2026-10-29')
        self.assertEqual(course['details']['requisitos_estado'], 'no_especificado')
        self.assertIn('no especifica', course['details']['requisitos'])

    def test_shared_year_dates(self):
        self.assertEqual(dates('15 de febrero al 02 de marzo de 2025'), ['2025-02-15', '2025-03-02'])
        self.assertEqual(dates('Del 01 al 15 de octubre de 2026'), ['2026-10-01', '2026-10-15'])

    def test_legacy_prices_and_expiration(self):
        html = '<h1 class="tituloCurso">Python</h1><div class="pc-tab"><section><div class="tab1"><ul><li><strong>Fechas de inscripciones:</strong>15 de febrero al 02 de marzo de 2025</li><li>30 USD Estudiantes UTA</li><li>Público: $50,00</li></ul></div></section></div>'
        course = parse_course(html, 'https://example.edu/curso1.php', '2026-10-29')
        self.assertEqual(course['estado'], 'INSCRIPCIONES FINALIZADAS')
        self.assertEqual([p['valor'] for p in course['details']['precios']], ['30.00', '50.00'])

    def test_long_content_is_paginated_without_llm(self):
        course = {'id': 1, 'titulo': 'Python', 'estado': 'ABIERTO', 'contenido': 'A' * 9000, 'details': {}}
        first = route({}, '', [course], 'content:1')
        second = route({}, '', [course], 'content:1:1')
        self.assertLess(len(first['text']), 4000)
        self.assertIn('Contenido (2/4)', second['text'])
        self.assertFalse(second['infer'])
