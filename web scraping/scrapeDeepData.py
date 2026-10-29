import csv
import json
import time
import urllib3
import requests
from bs4 import BeautifulSoup

# Deshabilitar advertencias SSL de la UTA
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://ctt-talleresfisei.uta.edu.ec"
URL_PRINCIPAL = f"{BASE_URL}/course.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def descargar_pagina(url):
    """Realiza la petición HTTP GET manejando encoding UTF-8."""
    response = requests.get(url, headers=HEADERS, verify=False, timeout=15)
    response.raise_for_status()
    # Asegurar decodificación UTF-8 adecuada si el servidor no envía charset explícito
    if response.encoding and response.encoding.lower() == 'iso-8859-1':
        response.encoding = 'utf-8'
    return response.text


def extraer_tarjetas_principales(html_principal):
    """Extrae la lista básica de tarjetas de cursos del catálogo general."""
    soup = BeautifulSoup(html_principal, "html.parser")
    cursos = []

    inner_cards = soup.find_all(
        lambda tag: tag.name == "div"
        and ("ABIERTO" in tag.get_text() or "CERRADO" in tag.get_text())
        and "HORAS" in tag.get_text()
    )

    contenedores = []
    for card in inner_cards:
        sub_cards = card.find_all(
            lambda t: t != card
            and ("ABIERTO" in t.get_text() or "CERRADO" in t.get_text())
            and "HORAS" in t.get_text()
        )
        if not sub_cards:
            padre = card.parent if card.parent else card
            abuelo = padre.parent if padre.parent else padre
            contenedores.append(abuelo)

    for i, contenedor in enumerate(contenedores, 1):
        titulo_elem = contenedor.find("a", class_="h5")
        if titulo_elem:
            titulo = titulo_elem.get_text(strip=True)
        else:
            img_link = contenedor.find("a", attrs={"aria-label": True})
            titulo = img_link["aria-label"] if img_link else "S/N"

        texto_completo = contenedor.get_text()
        estado = "ABIERTO" if "ABIERTO" in texto_completo else ("CERRADO" if "CERRADO" in texto_completo else "NO DEFINIDO")

        lineas = [l.strip() for l in texto_completo.split("\n") if l.strip()]
        horas = next((t for t in lineas if "HORA" in t.upper()), "N/D")

        link_elem = contenedor.find("a", class_="h5", href=True)
        if not link_elem:
            link_elem = contenedor.find("a", href=True)
        enlace = link_elem["href"] if link_elem else f"{BASE_URL}/"
        if enlace and not enlace.startswith("http"):
            enlace = f"{BASE_URL}/{enlace.lstrip('/')}"

        img_elem = contenedor.find("img")
        if img_elem and img_elem.get("src"):
            src = img_elem["src"]
            imagen = src if src.startswith("http") else f"{BASE_URL}/{src.lstrip('/')}"
        else:
            imagen = ""

        cursos.append({
            "id": i,
            "titulo": titulo,
            "estado": estado,
            "duracion": horas,
            "enlace": enlace,
            "imagen": imagen
        })

    return cursos


def extraer_detalle_curso(html_detalle):
    """
    Parsea la página individual del curso (ej: curso16.php) extrayendo:
    - descripcion
    - objetivos (lista)
    - pensum / temario (lista)
    - requisitos
    - horarios
    - inversion / costos
    - fechas, modalidad y detalles complementarios
    """
    soup = BeautifulSoup(html_detalle, "html.parser")

    # 1. Descripción
    descripcion = ""
    desc_elem = soup.find("div", class_="descripcion") or soup.find("div", class_="intro")
    if desc_elem:
        # Remover título DESCRIPCIÓN si está al inicio
        texto = desc_elem.get_text(separator="\n").strip()
        lineas = [l.strip() for l in texto.split("\n") if l.strip()]
        if lineas and lineas[0].upper().startswith("DESCRIPCI"):
            lineas = lineas[1:]
        descripcion = " ".join(lineas)

    # 2. Objetivos
    objetivos = []
    obj_elem = soup.find("div", class_="objetivos")
    if obj_elem:
        items = obj_elem.find_all("li")
        if items:
            objetivos = [li.get_text(strip=True) for li in items if li.get_text(strip=True)]
        else:
            lineas = [l.strip() for l in obj_elem.get_text(separator="\n").split("\n") if l.strip()]
            objetivos = [l for l in lineas if not l.upper().startswith("OBJETIVO")]

    # 3. Pestañas (pc-tab): tab1=Requisitos/Detalles, tab2=Materiales, tab3=Contenidos
    tab1 = soup.find("div", class_="tab1")
    tab2 = soup.find("div", class_="tab2")
    tab3 = soup.find("div", class_="tab3")

    # 3.1 Pensum / Temario (tab3)
    pensum = []
    if tab3:
        items = tab3.find_all(["li", "p"])
        for item in items:
            txt = item.get_text(strip=True)
            if txt and txt not in pensum:
                pensum.append(txt)
        if not pensum:
            pensum = [l.strip() for l in tab3.get_text(separator="\n").split("\n") if l.strip()]

    # 3.2 Requisitos y Materiales (tab2 + tab1 conocimientos previos)
    requisitos_lista = []
    if tab2:
        mat_items = [li.get_text(strip=True) for li in tab2.find_all("li") if li.get_text(strip=True)]
        if mat_items:
            requisitos_lista.extend(mat_items)

    horario = "N/D"
    inversion = []
    fechas_inscripcion = "N/D"
    fecha_curso = "N/D"
    modalidad = "N/D"
    tipo_capacitacion = "N/D"
    cupo = "N/D"
    conocimientos_previos = "Ninguno"

    if tab1:
        lis = tab1.find_all("li")
        modo_precio = False

        for li in lis:
            strong = li.find("strong")
            txt = li.get_text(strip=True)

            if strong:
                etiqueta = strong.get_text(strip=True).upper()
                valor = txt.replace(strong.get_text(strip=True), "").strip().lstrip(":").strip()

                if "HORARIO" in etiqueta:
                    horario = valor
                    modo_precio = False
                elif "PRECIO" in etiqueta or "COSTO" in etiqueta:
                    modo_precio = True
                    if valor:
                        inversion.append(valor)
                elif "FECHAS DE INSCRIP" in etiqueta:
                    fechas_inscripcion = valor
                    modo_precio = False
                elif "FECHA DEL CURSO" in etiqueta:
                    fecha_curso = valor
                    modo_precio = False
                elif "TIPO DE CAPACITA" in etiqueta:
                    tipo_capacitacion = valor
                    modo_precio = False
                elif "MODALIDAD" in etiqueta:
                    modalidad = valor
                    modo_precio = False
                elif "CUPO" in etiqueta:
                    cupo = valor
                    modo_precio = False
                elif "CONOCIMIENTOS PREVIOS" in etiqueta:
                    conocimientos_previos = valor
                    modo_precio = False
                else:
                    modo_precio = False
            else:
                # Si estamos dentro de la lista de precios desglosados (UTA, Docentes, General)
                if modo_precio:
                    if any(c in txt for c in ["$", "USD", "Estudiantes", "Público", "Graduados"]):
                        inversion.append(txt)
                    else:
                        modo_precio = False

    if conocimientos_previos and conocimientos_previos.upper() != "NINGUNO":
        requisitos_lista.append(f"Conocimientos previos: {conocimientos_previos}")
    requisitos = "; ".join(requisitos_lista) if requisitos_lista else "Ninguno reportado"

    precio_final = " | ".join(inversion) if inversion else "Consultar directamente con CTT"

    return {
        "descripcion": descripcion,
        "objetivos": objetivos,
        "pensum": pensum,
        "requisitos": requisitos,
        "horarios": horario,
        "inversion": precio_final,
        "tipo_capacitacion": tipo_capacitacion,
        "fechas_inscripcion": fechas_inscripcion,
        "fecha_curso": fecha_curso,
        "modalidad": modalidad,
        "cupo": cupo
    }


def scraping_profundo_cursos_uta():
    """Ejecución del algoritmo completo de Scraping Profundo."""
    print("=" * 70)
    print("INICIANDO ALGORITMO: ScrapingProfundoCursosUTA")
    print("=" * 70)

    lista_cursos_completos = []

    # PASO 1: Descargar página principal y extraer lista inicial
    print(f"\n[PASO 1] Descargando catálogo principal: {URL_PRINCIPAL}")
    try:
        html_principal = descargar_pagina(URL_PRINCIPAL)
        lista_cursos_inicial = extraer_tarjetas_principales(html_principal)
        print(f"-> Se detectaron {len(lista_cursos_inicial)} cursos en total.")
    except Exception as e:
        print(f"Error crítico al descargar la página principal: {e}")
        return []

    # PASO 2: Recorrer cada curso y visitar su página individual
    print("\n[PASO 2] Procesando páginas de detalle individualmente...")
    for idx, curso in enumerate(lista_cursos_inicial, 1):
        titulo = curso["titulo"]
        url_detalle = curso["enlace"]
        print(f"\n[{idx}/{len(lista_cursos_inicial)}] Procesando: {titulo}")
        print(f"      URL: {url_detalle}")

        try:
            # Solo navegar si la URL apunta a una página individual interna
            if "curso" in url_detalle:
                html_detalle = descargar_pagina(url_detalle)
                detalle = extraer_detalle_curso(html_detalle)
            else:
                detalle = {
                    "descripcion": titulo,
                    "objetivos": [],
                    "pensum": [],
                    "requisitos": "N/D",
                    "horarios": "N/D",
                    "inversion": "N/D",
                    "fechas_inscripcion": "N/D",
                    "fecha_curso": "N/D",
                    "modalidad": "N/D",
                    "cupo": "N/D"
                }

            curso_enriquecido = {
                "id": curso["id"],
                "titulo": curso["titulo"],
                "estado": curso["estado"],
                "duracion": curso["duracion"],
                "enlace": curso["enlace"],
                "imagen": curso["imagen"],
                "detalle": detalle
            }

            lista_cursos_completos.append(curso_enriquecido)
            print(f"      [OK] Objetivos: {len(detalle['objetivos'])} | Temas pensum: {len(detalle['pensum'])} | Inversion: {detalle['inversion'][:40]}...")

            # Respetar el servidor de la universidad
            time.sleep(1)

        except Exception as e:
            print(f"      [ERROR] Fallo al consultar {url_detalle}: {e}")
            # Guardamos el curso con sus datos básicos de respaldo
            curso_fallback = {
                **curso,
                "detalle": {
                    "descripcion": curso["titulo"],
                    "objetivos": [],
                    "pensum": [],
                    "requisitos": "N/D",
                    "horarios": "N/D",
                    "inversion": "N/D"
                }
            }
            lista_cursos_completos.append(curso_fallback)

    # PASO 3: Guardar el resultado final para el LLM / Backend
    output_filename = "cursos_fisei_completos.json"
    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(lista_cursos_completos, f, ensure_ascii=False, indent=4)

    # Resumen también en CSV para visualización rápida
    with open("cursos_fisei_completos.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Titulo", "Estado", "Duracion", "Tipo de Capacitacion", "Modalidad", "Horario", "Inversion", "Enlace"])
        for c in lista_cursos_completos:
            det = c.get("detalle", {})
            writer.writerow([
                c["id"], c["titulo"], c["estado"], c["duracion"],
                det.get("tipo_capacitacion", "N/D"),
                det.get("modalidad", "N/D"), det.get("horarios", "N/D"),
                det.get("inversion", "N/D"), c["enlace"]
            ])

    print("\n" + "=" * 70)
    print(f"Scraping completo finalizado con exito.")
    print(f"-> Archivos guardados: '{output_filename}' y 'cursos_fisei_completos.csv'")
    print("=" * 70)

    return lista_cursos_completos


if __name__ == "__main__":
    scraping_profundo_cursos_uta()
