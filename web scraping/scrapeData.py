import csv
import json
import urllib3
from bs4 import BeautifulSoup
import requests

# Deshabilitar advertencias si la web de la universidad tiene certificados SSL intermedios incompletos
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://ctt-talleresfisei.uta.edu.ec/course.php"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def scrapear_cursos():
  try:
    response = requests.get(URL, headers=headers, verify=False, timeout=15)
    response.raise_for_status()
  except requests.RequestException as e:
    print(f"Error al conectar con la página: {e}")
    return []

  soup = BeautifulSoup(response.text, "html.parser")
  cursos = []

  # Buscamos las tarjetas de cursos.
  # Usualmente están contenidas en elementos tipo col, card o bloques con clases específicas.
  # Buscaremos los contenedores que tengan información de horas o estado:
  cards = soup.find_all(
      lambda tag: tag.name in ["div", "article"]
      and (
          "ABIERTO" in tag.get_text() or "CERRADO" in tag.get_text()
      )  # Filtra los contenedores de curso
      and ("HORAS" in tag.get_text())
  )

  # Para evitar tomar contenedores padres repetidos, filtramos los elementos más internos
  tarjetas_unicas = []
  for card in cards:
    # Verificamos si este elemento no contiene a otro que ya cumpla la condición
    sub_cards = card.find_all(
        lambda t: t != card
        and ("ABIERTO" in t.get_text() or "CERRADO" in t.get_text())
        and "HORAS" in t.get_text()
    )
    if not sub_cards:
      tarjetas_unicas.append(card)

  print(f"Se encontraron {len(tarjetas_unicas)} tarjetas detectadas.\n")

  for i, card in enumerate(tarjetas_unicas, 1):
    texto_completo = [
        linea.strip()
        for linea in card.get_text(separator="\n").split("\n")
        if linea.strip()
    ]

    # Estado (ABIERTO / CERRADO)
    estado = (
        "ABIERTO"
        if "ABIERTO" in texto_completo
        else ("CERRADO" if "CERRADO" in texto_completo else "NO DEFINIDO")
    )

    # Duración en horas
    horas = next((t for t in texto_completo if "HORA" in t.upper()), "N/D")

    # Enlaces de inscripción si existen
    link_elem = card.find("a", href=True)
    enlace = (
        link_elem["href"]
        if link_elem
        else "https://ctt-talleresfisei.uta.edu.ec/"
    )
    if enlace and not enlace.startswith("http"):
      enlace = f"https://ctt-talleresfisei.uta.edu.ec/{enlace.lstrip('/')}"

    # Limpieza de títulos descartando metadatos conocidos
    lineas_titulo = [
        t
        for t in texto_completo
        if t
        not in [
            "ABIERTO",
            "CERRADO",
            horas,
            "INSCRIBIRME",
            "CURSOS",
            "Cursos Disponibles",
        ]
        and not t.startswith("CURSO ")
    ]

    # Nombre principal
    titulo = " - ".join(dict.fromkeys(lineas_titulo)) if lineas_titulo else "S/N"

    curso_data = {
        "id": i,
        "titulo": titulo,
        "estado": estado,
        "duracion": horas,
        "enlace": enlace,
    }
    cursos.append(curso_data)
    print(f"[{estado}] {titulo} | {horas}")

  return cursos


if __name__ == "__main__":
  datos = scrapear_cursos()

  # 1. Guardar en formato JSON
  with open("cursos_uta.json", "w", encoding="utf-8") as f:
    json.dump(datos, f, ensure_ascii=False, indent=4)

  # 2. Guardar en formato CSV
  if datos:
    keys = datos[0].keys()
    with open("cursos_uta.csv", "w", newline="", encoding="utf-8-sig") as f:
      dict_writer = csv.DictWriter(f, fieldnames=keys)
      dict_writer.writeheader()
      dict_writer.writerows(datos)

    print(
        "\n✅ Scraping completado con éxito. Archivos guardados como"
        " 'cursos_uta.csv' y 'cursos_uta.json'."
    )