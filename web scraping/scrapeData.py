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

  # La estructura real de cada tarjeta es:
  #   div.rounded > a[img aria-label="TITULO"] + div.bg-secondary > div[ABIERTO/HORAS] + a.h5[titulo]
  # Buscamos los divs internos con estado y horas (el nivel más interno), luego subimos al padre.
  inner_cards = soup.find_all(
      lambda tag: tag.name == "div"
      and (
          "ABIERTO" in tag.get_text() or "CERRADO" in tag.get_text()
      )
      and "HORAS" in tag.get_text()
  )

  # Filtramos los elementos más internos (sin hijos que también cumplan la condición)
  contenedores = []
  for card in inner_cards:
    sub_cards = card.find_all(
        lambda t: t != card
        and ("ABIERTO" in t.get_text() or "CERRADO" in t.get_text())
        and "HORAS" in t.get_text()
    )
    if not sub_cards:
      # Subimos al contenedor padre real (div.rounded que tiene imagen + datos)
      padre = card.parent if card.parent else card
      contenedores.append(padre)

  print(f"Se encontraron {len(contenedores)} tarjetas detectadas.\n")

  for i, contenedor in enumerate(contenedores, 1):
    # --- Título: buscar el <a class="h5"> ---
    titulo_elem = contenedor.find("a", class_="h5")
    if titulo_elem:
      titulo = titulo_elem.get_text(strip=True)
    else:
      # Fallback: aria-label del enlace de la imagen
      img_link = contenedor.find("a", attrs={"aria-label": True})
      titulo = img_link["aria-label"] if img_link else "S/N"

    # --- Estado (ABIERTO / CERRADO) ---
    texto_completo = contenedor.get_text()
    if "ABIERTO" in texto_completo:
      estado = "ABIERTO"
    elif "CERRADO" in texto_completo:
      estado = "CERRADO"
    else:
      estado = "NO DEFINIDO"

    # --- Duración en horas ---
    lineas = [l.strip() for l in texto_completo.split("\n") if l.strip()]
    horas = next((t for t in lineas if "HORA" in t.upper()), "N/D")

    # --- Enlace del curso ---
    link_elem = contenedor.find("a", class_="h5", href=True)
    if not link_elem:
      link_elem = contenedor.find("a", href=True)
    enlace = link_elem["href"] if link_elem else "https://ctt-talleresfisei.uta.edu.ec/"
    if enlace and not enlace.startswith("http"):
      enlace = f"https://ctt-talleresfisei.uta.edu.ec/{enlace.lstrip('/')}"

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

    print("\nScraping completado con exito. Archivos guardados como 'cursos_uta.csv' y 'cursos_uta.json'.")
