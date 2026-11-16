
"""Busca obras y ediciones en Open Library y normaliza sus metadatos."""

import os
import re
import unicodedata
from pathlib import Path
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import langcodes
import pycountry
from langcodes import LanguageTagError


# Cargar el .env del proyecto, aunque la aplicación se ejecute desde otra carpeta.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

# Parámetros comunes para las consultas HTTP a Open Library.
OPEN_LIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
REQUEST_HEADERS = {"User-Agent": "BookAgent/1.0"}


def _create_session() -> requests.Session:
    """Crea una sesión reutilizable con reintentos para errores transitorios.
    Se configura únicamente para GET porque las consultas a Open Library son
    de lectura; si la API devuelve un error temporal, requests puede repetirlas.
    """
    session = requests.Session()
    # Dos reintentos y una espera creciente evitan repetir inmediatamente una consulta cuando la API está limitada o presenta un error de servidor.
    retries = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    return session


def normalize_text(value: str | None) -> str:
    """Normaliza texto para comparar sin distinguir mayúsculas, tildes o signos.
    Se usa para comparar títulos, autores e idiomas que pueden aparecer con
    grafías distintas entre la solicitud y el catálogo.
    """
    # NFKD separa las letras de sus marcas diacríticas, que se eliminan abajo. casefold compara sin distinguir mayúsculas; NFKD separa letras y tildes.
    value = unicodedata.normalize("NFKD", (value or "").casefold())
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    return " ".join(re.findall(r"\w+", value))


def canonical_title(title: str) -> str:
    """Obtiene una forma del título adecuada para comparar identidad de obras.
    Se ignoran algunos descriptores de edición y artículos iniciales para
    que, por ejemplo, el mismo libro no parezca una obra distinta por su ficha.
    """
    normalized = normalize_text(title)
    # Los términos posteriores suelen describir la edición, no el título base.
    normalized = re.split(
        r"\b(?:translated|illustrated|annotated|abridged|"
        r"introduction|edited|edition|volume|vol)\b",
        normalized,
        maxsplit=1,
    )[0]
    # Los artículos iniciales no deben impedir que dos fichas coincidan.
    normalized = re.sub(
        r"^(the|a|an|el|la|los|las|un|una)\s+",
        "",
        normalized,
    )
    return normalized.strip()


def _language_code(
    language: str,
    alpha_3: bool = False,
) -> str | None:
    """Convierte nombres y códigos de idioma a ISO 639-1 o ISO 639-3.
    `alpha_3=True` se usa para consultar Open Library, que suele indexar
    idiomas con tres letras; en los demás casos se prefiere el código corto.
    """
    if not isinstance(language, str) or not language.strip():
        return None

    value = language.strip().casefold()

    # Primero resolver códigos conocidos directamente; así no se interpretan como nombres de idioma ni se pierde la variante bibliográfica de tres letras.
    item = None

    if value.isascii() and value.isalpha():
        if len(value) == 2:
            item = pycountry.languages.get(alpha_2=value)
        elif len(value) == 3:
            item = pycountry.languages.get(alpha_3=value)
            # Si el valor es un nombre (por ejemplo, "Spanish"), resolverlo como etiqueta.
            if item is None:
                item = pycountry.languages.get(bibliographic=value)

    if item is None:
        try:
            parsed = langcodes.find(language.strip())
            code = parsed.to_alpha3()
            item = pycountry.languages.get(alpha_3=code)
        except (LookupError, ValueError):
            return None

    if item is None:
        return None

    # Devolver el formato requerido por el llamador solo después de validar el idioma.
    return (
        getattr(item, "alpha_3", None)
        if alpha_3
        else getattr(item, "alpha_2", None)
    )

def _normalize_languages(values) -> list[str]:
    """Devuelve una lista única de códigos, aceptando formatos del catálogo.
    Open Library puede enviar un idioma como texto, diccionario o clave de ruta;
    los códigos desconocidos se conservan normalizados para no perder el dato.
    """
    # Un idioma individual se trata igual que una colección de idiomas.
    if isinstance(values, (str, dict)):
        values = [values]

    result = []

    for value in values or []:
        # Algunas respuestas representan el idioma con una clave y otras con un nombre.
        if isinstance(value, dict):
            value = value.get("key") or value.get("name") or ""

        if not isinstance(value, str) or not value.strip():
            continue

        value = value.strip()

        # Open Library usa claves como "/languages/rus".
        if value.startswith("/languages/"):
            value = value.rsplit("/", 1)[-1]

        # Si la etiqueta no es reconocible, preservar su forma en minúsculas.
        normalized = _language_code(value)
        code = normalized or value.casefold()

        if code not in result:
            result.append(code)

    return result

def _parse_year(value) -> int | None:
    """Extrae un año de publicación reconocible de un campo potencialmente mixto."""
    if value is None:
        return None

    match = re.search(r"\b(1[0-9]{3}|20[0-9]{2}|2100)\b", str(value))
    return int(match.group(1)) if match else None


def _parse_search_term(term: str) -> tuple[str, str | None]:
    """Separa título y autor si el usuario escribió `título - autor`.
    Si no hay un guion rodeado por espacios, se conserva todo el texto como
    título y se deja el autor sin especificar.
    """
    parts = re.split(r"\s+[—–-]\s+", term, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return term.strip(), None


class SearchResults(list):
    """Lista compatible con el código existente que también transporta el estado.
    Así, una consulta fallida puede seguir devolviendo una lista vacía sin
    confundirla con una búsqueda exitosa que no encontró libros.
    """

    def __init__(self, items=(), status="success"):
        """Inicializa la lista y agrega su estado sin cambiar su interfaz."""
        super().__init__(items)
        self.status = status


def _normalize_content_label(value: str) -> str:
    """Normaliza etiquetas para comparar materias en español e inglés."""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def classify_content_type(subjects: list[str] | None) -> str:
    """Infiere la categoría editorial a partir de las materias del catálogo.
    Las materias pueden mezclar idiomas y descriptores generales; por eso se
    normalizan antes de probar señales específicas y se devuelve `unknown`
    cuando no hay evidencia suficientemente clara.
    """
    # Eliminar valores vacíos y normalizar cada materia una sola vez; la clasificación posterior usa conjuntos y prefijos para tolerar variantes.
    normalized = {
        _normalize_content_label(subject)
        for subject in (subjects or [])
        if isinstance(subject, str) and subject.strip()
    }

    # La crítica literaria tiene prioridad sobre las materias genéricas de ficción o género policial.
    criticism_signals = {
        "history and criticism",
        "historia y critica",
        "historiay critica",
        "literary criticism",
        "critica literaria",
        "literary history",
        "historia literaria",
    }

    has_criticism = any(
        subject in criticism_signals
        or "history and criticism" in subject
        or "historia y critica" in subject
        or "literary criticism" in subject
        or "critica literaria" in subject
        for subject in normalized
    )

    if has_criticism:
        return "non_fiction"

    # Revisar las categorías especiales antes de ficción para no clasificar, por ejemplo, una colección de poemas como novela solo por una etiqueta amplia.
    poetry_signals = {
        "poem",
        "poems",
        "poetry",
        "poesia",
        "poema",
        "poemas",
    }
    if any(
        subject in poetry_signals
        or subject.startswith(("poetry ", "poemas ", "poesia "))
        or " poetry" in f" {subject}"
        or " poesia" in f" {subject}"
        for subject in normalized
    ):
        return "poetry"

    # Separar novela gráfica y cómic del tipo general de ficción.
    graphic_novel_signals = {
        "comic",
        "comics",
        "comic book",
        "comic books",
        "graphic novel",
        "graphic novels",
        "novela grafica",
        "novelas graficas",
    }
    if any(
        subject in graphic_novel_signals
        or subject.startswith(("graphic novels ", "novelas graficas "))
        or " graphic novel" in f" {subject}"
        or " novela grafica" in f" {subject}"
        for subject in normalized
    ):
        return "graphic_novel"

    # Ensayos, biografías y memorias son no ficción aunque la ficha tenga otras materias.
    non_fiction_signals = {
        "essay",
        "essays",
        "ensayo",
        "ensayos",
        "non fiction",
        "nonfiction",
        "biography",
        "biographies",
        "biografia",
        "biografias",
        "autobiography",
        "memoir",
        "memoirs",
        "memorias",
    }
    if any(
        subject in non_fiction_signals
        or subject.startswith(
            ("essays ", "ensayos ", "biography ", "biografias ")
        )
        for subject in normalized
    ):
        return "non_fiction"

    # La ficción es la última categoría positiva; las materias restantes no bastan.
    fiction_signals = {
        "fiction",
        "novel",
        "novels",
        "novela",
        "novelas",
        "short story",
        "short stories",
        "cuento",
        "cuentos",
        "short story collection",
        "coleccion de cuentos",
    }

    has_fiction = any(
        subject in fiction_signals
        or subject.startswith("fiction ")
        or subject.startswith("novel ")
        or subject.startswith("novela ")
        or subject.startswith("short stories ")
        or subject.startswith("cuentos ")
        for subject in normalized
    )

    return "fiction" if has_fiction else "unknown"


def classify_requested_content_type(value: str | None) -> str:
    """Mapea la preferencia expresada por el lector a una categoría interna.
    Esta conversión no clasifica una obra: traduce sinónimos de la solicitud
    a los valores usados después para filtrar candidatos.
    """
    # Una preferencia ausente o irreconocible no fuerza una categoría.
    if not value:
        return "unknown"

    normalized = _normalize_content_label(value)

    fiction_types = {
        "novel",
        "novels",
        "novela",
        "novelas",
        "fiction",
        "ficcion",
        "short story",
        "short stories",
        "cuento",
        "cuentos",
        "short story collection",
        "coleccion de cuentos",
}

    non_fiction_types = {
        "essay",
        "ensayo",
        "non fiction",
        "no ficcion",
        "literary criticism",
        "critica literaria",
    }
    poetry_types = {"poetry", "poema", "poemas", "poesia"}
    graphic_novel_types = {
        "comic",
        "comics",
        "comic book",
        "comic books",
        "graphic novel",
        "graphic novels",
        "novela grafica",
        "novelas graficas",
    }

    if normalized in fiction_types:
        return "fiction"

    if normalized in non_fiction_types:
        return "non_fiction"

    if normalized in poetry_types:
        return "poetry"

    if normalized in graphic_novel_types:
        return "graphic_novel"

    return "unknown"


def _open_library_document_to_dict(
    doc: dict,
    search_label: str,
) -> dict | None:
    """Convierte un resultado de búsqueda en el formato que consume el grafo.
    Los documentos sin título, autor o clave de obra se descartan porque no
    pueden identificarse ni recuperarse con seguridad en pasos posteriores.
    """
    title = (doc.get("title") or "").strip()
    authors = doc.get("author_name") or []
    work_id = doc.get("key")

    if not title or not authors or not work_id:
        return None

    # Los campos no incluidos en la búsqueda quedan vacíos, no se inventan.
    return {
        "title": title,
        "authors": authors,
        "published_date": _parse_year(doc.get("first_publish_year")),
        "language": _normalize_languages(doc.get("language")),
        "subjects": (doc.get("subject") or [])[:10],
        "pages": doc.get("number_of_pages_median"),
        "record_type": doc.get("type"),
        "open_library_id": work_id,
        "isbn": None,
        "publisher": None,
        "description": None,
        "matched_search_term": search_label,
        "source": "open_library",
        "content_type": classify_content_type(doc.get("subject")),
    }

def deduplicate_books(books: list[dict]) -> list[dict]:
    """Combina resultados repetidos de una misma obra y conserva su orden.
    La clave usa título canónico y primer autor; las materias e idiomas se
    fusionan sin duplicados y se completa una descripción si alguna ficha la trae.
    """
    # El diccionario guarda la ficha acumulada y `order` mantiene el orden en que la primera consulta encontró cada obra.
    deduplicated = {}
    order = []

    for book in books:
        title = book.get("title") or ""
        authors = book.get("authors") or []
        first_author = authors[0] if authors else ""

        if not title.strip():
            continue

        # No usar el año o el idioma en la clave: pueden variar entre ediciones de una misma obra y producir duplicados artificiales.
        key = (
            canonical_title(title),
            normalize_text(first_author),
        )

        if key not in deduplicated:
            deduplicated[key] = book.copy()
            order.append(key)
            continue

        existing = deduplicated[key]

        for field in ("language", "subjects"):
            existing[field] = list(dict.fromkeys(
                (existing.get(field) or [])
                + (book.get(field) or [])
            ))

        if not existing.get("description") and book.get("description"):
            existing["description"] = book["description"]

    return [deduplicated[key] for key in order]

def exclude_reference_books(
    books: list[dict],
    excluded_titles: list[str],
) -> list[dict]:
    """Quita de los candidatos las obras que el lector citó como referencia.
    Se comparan títulos canónicos para excluir también variantes de puntuación,
    acentos o artículos editoriales.
    """
    # Ignorar entradas vacías y normalizar títulos citados una única vez.
    excluded = {
        canonical_title(title)
        for title in excluded_titles
        if isinstance(title, str) and title.strip()
    }

    return [
        book
        for book in books
        if canonical_title(book.get("title", "")) not in excluded
    ]

def _build_queries(filters) -> list[dict]:
    """Construye las búsquedas que mejor representan los filtros del lector.
    Mantiene algunos tipos de consulta separados (por ejemplo, título y autor)
    y combina géneros con preferencias cuando la API acepta una consulta libre.
    """
    queries = []
    seen = set()

    def add_query(label, query, open_library_params):
        """Guarda una consulta deduplicada con nombre para diagnóstico.
        La etiqueta identifica la estrategia en los mensajes; los parámetros
        son los que se envían realmente al endpoint de búsqueda.
        """
        key = normalize_text(str(query))

        if not key or key in seen:
            return

        seen.add(key)
        queries.append({
            "label": label,
            "open_library": open_library_params,
        })

    def clean(values):
        """Normaliza listas recibidas antes de interpolarlas en la consulta."""
        result = []
        for value in values or []:
            if isinstance(value, str) and value.strip():
                result.append(value.strip())
        return result

    subjects = clean(filters.subjects)
    authors = clean(filters.authors)
    terms = clean(filters.search_terms)
    preferences = clean(filters.preference_terms)

    content_type = (
        filters.content_type.strip()
        if isinstance(filters.content_type, str)
        and filters.content_type.strip()
        else None
    )

    # Primera búsqueda: reunir los géneros indicados para encontrar obras que puedan coincidir con varios intereses, no solo con uno de ellos.
    if subjects:
        combined_subjects = " ".join(
            f'"{subject}"' for subject in subjects
        )
        query = combined_subjects

        if content_type:
            query += f' "{content_type}"'

        add_query(
            "géneros combinados",
            query,
            {"q": query},
        )

        # Añadir búsquedas individuales como alternativa menos restrictiva a la combinación, manteniendo el tipo de contenido si el lector lo indicó.
        for subject in subjects:
            query = f'subject:"{subject}"'

            if content_type:
                query += f' "{content_type}"'

            add_query(
                subject,
                query,
                {"q": query},
            )

  
    # Título y autor explícitos se consultan con campos bibliográficos dedicados; así se evita que Open Library los trate como palabras temáticas generales.
    for term in terms:
        title, author = _parse_search_term(term)

        if not title:
            continue

        query = f'intitle:"{title}"'
        params = {"title": title}

        if author:
            query += f' inauthor:"{author}"'
            params["author"] = author

        add_query(term, query, params)

    # Una preferencia por autor se representa como búsqueda de sus obras.
    for author in authors:
        query = f'inauthor:"{author}"'
        params = {"author": author}

        if content_type:
            query += f' "{content_type}"'
            params["q"] = content_type

        add_query(f"obras de {author}", query, params)

    # Combinar preferencias con géneros permite usar el contexto adicional sin convertir cada palabra de preferencia en una consulta demasiado amplia.
    # Si no hay géneros, se conserva la frase completa para que sus términos relacionados no se separen en búsquedas genéricas independientes.
    if preferences:
        preference_query = " ".join(
            f'"{term}"' for term in preferences
        )

        if subjects:
            query = (
                " ".join(f'"{subject}"' for subject in subjects)
                + f" {preference_query}"
            )
        else:
            query = preference_query

        if content_type:
            query += f' "{content_type}"'

        add_query(
            "preferencias combinadas",
            query,
            {"q": query},
        )

    return queries


def _search_open_library_query(params: dict, max_results: int, search_label: str) -> list[dict]:
    """Ejecuta una búsqueda individual y normaliza los documentos recibidos.
    El campo `fields` reduce la respuesta a los metadatos que usa la aplicación;
    el límite superior evita solicitar volúmenes excesivos a Open Library.
    """
    query_params = {
        **params,
        "limit": min(max(max_results, 1), 100),
        "fields": (
            "key,title,author_name,first_publish_year,"
            "language,subject,number_of_pages_median,"
            "type,ebook_access,has_fulltext"
        ),
    }

    # Cerrar siempre la sesión, tanto ante una respuesta correcta como ante error.
    session = _create_session()
    try:
        response = session.get(
            OPEN_LIBRARY_SEARCH_URL,
            params=query_params,
            headers=REQUEST_HEADERS,
            timeout=(5, 15),
        )
        response.raise_for_status()
        docs = response.json().get("docs") or []
    except (requests.RequestException, ValueError) as exc:
        print(f"Open Library falló para {search_label!r}: {exc}")
        return SearchResults([], status="error")
    finally:
        session.close()

    # Descartar registros incompletos durante la conversión a nuestro esquema.
    results = []
    for doc in docs:
        book = _open_library_document_to_dict(doc, search_label)
        if book:
            results.append(book)

    return SearchResults(results)

def search_books(filters, max_results: int = 10) -> list[dict]:
    """Ejecuta las consultas derivadas de los filtros y consolida los candidatos.
    Si ninguna consulta devuelve libros, distingue en el registro entre una API
    que falló y una respuesta válida sin coincidencias.
    """
    if max_results <= 0:
        return []

    # Limitar el máximo evita solicitudes excesivas a la API de catálogo.
    max_results = min(max_results, 60)
    queries = _build_queries(filters)

    # Si los filtros no producen consulta, no tiene sentido llamar a la API.
    if not queries:
        print("Discovery: no hay criterios de búsqueda.")
        return []

    results = []
    errors = 0

    # Cada consulta aporta resultados a la lista común; los fallos individuales no impiden conservar los resultados que sí se hayan podido obtener.
    for query in queries:
        books = _search_open_library_query(
            params=query["open_library"],
            max_results=min(max(max_results, 5), 20),
            search_label=query["label"],
        )
        results.extend(books)

        if getattr(books, "status", "success") == "error":
            errors += 1

    # Una misma obra puede aparecer en varias consultas; consolidarla aquí evita que las etapas de evaluación la puntúen como varios libros distintos.
    combined = deduplicate_books(results)
    if not combined and errors:
        print("Discovery: Open Library no respondió correctamente.")
    elif not combined:
        print("Discovery: Open Library no encontró libros.")

    return combined

def build_open_library_query(filters) -> str:
    """Construye una consulta textual compatible con llamadores anteriores.
    El flujo principal usa `_build_queries` para generar parámetros más precisos;
    esta función conserva una representación sencilla para integraciones legacy.
    """
    parts = []

    for subject in filters.subjects or []:
        parts.append(f'subject:"{subject}"')

    for author in filters.authors or []:
        parts.append(f'author:"{author}"')

    for term in filters.search_terms or []:
        parts.append(f'"{term}"')

    return " ".join(parts)


def get_open_library_work(open_library_id: str) -> dict | None:
    """Obtiene la ficha de una obra a partir de una clave `/works/...`.
    La descripción de Open Library puede ser texto o un objeto con `value`;
    aquí se normaliza a texto para que el resto del flujo use una sola forma.
    """
    # No aceptar claves de edición u otras rutas como si identificaran una obra.
    if not open_library_id or not open_library_id.startswith("/works/"):
        return None

    url = f"https://openlibrary.org{open_library_id}.json"
    session = _create_session()

    try:
        response = session.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=(5, 15),
        )
        response.raise_for_status()
        data = response.json()

    except (requests.RequestException, ValueError) as exc:
        print(f"Could not retrieve Open Library work {open_library_id}: {exc}")
        return None
    finally:
        session.close()

    # La API devuelve `description` como texto o como objeto {value: ...}.
    description = data.get("description")
    if isinstance(description, dict):
        description = description.get("value")

    return {
        "key": data.get("key"),
        "title": data.get("title"),
        "description": description,
        "subjects": data.get("subjects", []),
        "links": data.get("links", []),
    }

def get_book_pages(open_library_id: str) -> int | None:
    """Devuelve el primer número positivo de páginas de las ediciones consultadas.
    El dato pertenece a una edición, no necesariamente a todas las versiones
    de la obra; si el catálogo no lo informa, se devuelve `None`.
    """
    # El endpoint de ediciones solo se consulta para una clave de obra válida.
    if not open_library_id or not open_library_id.startswith("/works/"):
        return None

    url = f"https://openlibrary.org{open_library_id}/editions.json"
    session = _create_session()

    try:
        response = session.get(
            url,
            params={"limit": 20},
            headers=REQUEST_HEADERS,
            timeout=(5, 15),
        )
        response.raise_for_status()

        # Leer varias ediciones porque el recuento puede faltar en la primera.
        for edition in response.json().get("entries", []):
            pages = edition.get("number_of_pages")
            if isinstance(pages, int) and pages > 0:
                return pages

    except (requests.RequestException, ValueError) as exc:
        print(f"Could not retrieve page count for {open_library_id}: {exc}")
    finally:
        session.close()

    return None
    

def search_open_library_editions(title: str, author: str, preferred_languages: list[str], max_results: int = 20, open_library_id: str | None = None) -> list[dict]:
    """Busca ediciones verificables de la misma obra en idiomas solicitados.
    Primero consulta el índice por idioma y, si ya se conoce el ID de la obra
    y no hay resultados, revisa directamente el catálogo completo de ediciones.
    """
    # Sin identidad suficiente o con límite inválido no es posible verificar ediciones; no se envía una consulta ambigua al catálogo.
    if not title or not author or max_results <= 0:
        return []

    # Open Library indexa idiomas con ISO 639-3; convertir y quitar duplicados antes de generar una búsqueda por cada idioma solicitado.
    language_codes = list(dict.fromkeys(
        code
        for language in preferred_languages or []
        if (code := _language_code(language, alpha_3=True)) is not None
    ))
    if not language_codes:
        return []

    # Reutilizar el ID conocido es la forma más fiable de mantener la identidad exacta entre la obra y sus ediciones.
    if open_library_id:
        work_id = open_library_id.strip()
        if not work_id.startswith("/works/"):
            return []
    else:
        # Compatibilidad con las llamadas que todavía no proporcionan el ID.
        # Consultar por idioma y conservar únicamente ediciones de la obra exacta.
        session = _create_session()
        try:
            response = session.get(
                OPEN_LIBRARY_SEARCH_URL,
                params={
                    "title": title,
                    "author": author,
                    "limit": 20,
                    "fields": "key,title,author_name",
                },
                headers=REQUEST_HEADERS,
                timeout=(5, 15),
            )
            response.raise_for_status()
            works = response.json().get("docs") or []

            requested_title = canonical_title(title)
            requested_author = normalize_text(author)

            # Cuando el llamador no tiene ID, exigir coincidencia de título y autor antes de aceptar una obra candidata como punto de partida.
            matching_work = next(
                (
                    work
                    for work in works
                    if (
                        isinstance(work.get("key"), str)
                        and work["key"].startswith("/works/")
                        and canonical_title(work.get("title", ""))
                        == requested_title
                        and any(
                            normalize_text(candidate_author)
                            == requested_author
                            for candidate_author
                            in work.get("author_name", [])
                        )
                    )
                ),
                None,
            )
            if not matching_work:
                return []

            work_id = matching_work["key"]

        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(
                f"Open Library search falló para {title!r}: {exc}"
            ) from exc
        finally:
            session.close()

    # Buscar cada idioma por separado para consultar el índice de ediciones y recopilar fallos sin perder respuestas válidas de otros idiomas.
    # `seen_editions` evita que la misma ficha se repita si aparece en varias consultas; `errors` permite distinguir cero resultados de fallo del API.
    session = _create_session()
    editions_found = []
    seen_editions = set()
    errors = []

    try:
        for language in language_codes:
            try:
                response = session.get(
                    OPEN_LIBRARY_SEARCH_URL,
                    params={
                        "q": (
                            f'title:"{title}" '
                            f'AND author:"{author}" '
                            f"AND language:{language}"
                        ),
                        "fields": (
                            "key,title,author_name,"
                            "editions,editions.key,editions.title,"
                            "editions.language,editions.isbn_13,"
                            "editions.isbn_10,editions.publisher,"
                            "editions.number_of_pages"
                        ),
                        "limit": 50,
                    },
                    headers=REQUEST_HEADERS,
                    timeout=(5, 15),
                )
                response.raise_for_status()
                works = response.json().get("docs") or []

            except (requests.RequestException, ValueError) as exc:
                errors.append((language, exc))
                print(
                    f"Open Library falló para {title!r} "
                    f"en {language}: {exc}"
                )
                continue

            # No aceptar resultados de otras obras, aunque coincida el título traducido o el autor.
            work = next(
                (
                    item
                    for item in works
                    if item.get("key") == work_id
                ),
                None,
            )
            if not work:
                continue

            # La búsqueda de obras anida las ediciones dentro de `editions.docs`.
            editions = (
                (work.get("editions") or {}).get("docs") or []
            )

            for edition in editions:
                # Comprobar el idioma declarado en la edición, no solo el idioma usado como filtro de la búsqueda de la obra.
                languages = {
                    _language_code(code, alpha_3=True) or code
                    for code in _normalize_languages(edition.get("language"))
                }

                if language not in languages:
                    continue

                edition_id = edition.get("key")
                if not edition_id:
                    continue

                # Elegir ISBN-13 cuando existe; recurrir a ISBN-10 si no está.
                isbn_values = (
                    edition.get("isbn_13")
                    or edition.get("isbn_10")
                    or []
                )
                publishers = edition.get("publisher") or []

                # La misma edición puede aparecer en varias consultas; deduplicar por ID e idioma conserva una copia por cada variante pertinente.
                key = (edition_id, language)
                if key in seen_editions:
                    continue

                seen_editions.add(key)

                editions_found.append({
                    "title": (
                        edition.get("title") or work.get("title") or title
                    ).strip(),
                    "author": author,
                    "language": languages,
                    "isbn": isbn_values[0] if isbn_values else None,
                    "publisher": (
                        publishers[0] if publishers else None
                    ),
                    "pages": edition.get("number_of_pages"),
                    "open_library_id": work_id,
                    "open_library_edition_id": edition_id,
                    "source": "open_library",
                })


    finally:
        session.close()

    # La búsqueda general puede omitir ediciones menos relevantes. Si ya tenemos  el ID de la obra y no hubo coincidencias, revisar su catálogo de ediciones  directamente antes de concluir que el idioma solicitado no está disponible.
    if open_library_id and not editions_found:
        session = _create_session()
        try:
            response = session.get(
                f"https://openlibrary.org{work_id}/editions.json",
                params={"limit": 100, "offset": 0},
                headers=REQUEST_HEADERS,
                timeout=(5, 15),
            )
            response.raise_for_status()
            payload = response.json()
            edition_records = (
                payload.get("entries")
                or payload.get("docs")
                or []
            )

            # El endpoint directo usa nombres de campos distintos; aceptar ambas  variantes y extraer solo ediciones con idioma e ID comprobables.
            for edition in edition_records:
                languages = {
                    _language_code(code, alpha_3=True) or code
                    for code in _normalize_languages(
                        edition.get("languages")
                        or edition.get("language")
                    )
                }
                matching_languages = languages.intersection(language_codes)
                edition_id = edition.get("key")

                if not matching_languages or not edition_id:
                    continue

                isbn_values = (
                    edition.get("isbn_13")
                    or edition.get("isbn_10")
                    or []
                )
                publishers = edition.get("publishers") or edition.get("publisher") or []
                publisher = publishers[0] if publishers else None
                if isinstance(publisher, dict):
                    publisher = publisher.get("name")

                # Una edición puede cubrir varios idiomas solicitados; generar  una entrada por idioma sin repetir el mismo par edición/idioma.
                for language in language_codes:
                    if language not in matching_languages:
                        continue

                    key = (edition_id, language)
                    if key in seen_editions:
                        continue

                    seen_editions.add(key)
                    editions_found.append({
                        "title": (edition.get("title") or title).strip(),
                        "author": author,
                        "language": matching_languages,
                        "isbn": isbn_values[0] if isbn_values else None,
                        "publisher": publisher,
                        "pages": (
                            edition.get("number_of_pages")
                            or edition.get("number_of_pages_median")
                        ),
                        "open_library_id": work_id,
                        "open_library_edition_id": edition_id,
                        "source": "open_library",
                    })

                    if len(editions_found) >= max_results:
                        break

                if len(editions_found) >= max_results:
                    break

        except (requests.RequestException, ValueError) as exc:
            errors.append(("edition_catalog", exc))
            print(
                f"Open Library edition catalog falló para "
                f"{title!r}: {exc}"
            )
        finally:
            session.close()

    # Si hubo fallos y no hay resultados, devolver error en vez de afirmar que la obra no tiene ediciones en esos idiomas: la búsqueda quedó inconclusa.  Solo convertir fallos en error si no hay ningún dato útil: si un idioma falla pero otro respondió, devolver las ediciones que sí se verificaron.
    if errors and not editions_found:
        raise RuntimeError(
            f"No se pudieron verificar las ediciones de {title!r}. "
            "La consulta falló; no se puede confirmar que no existan ediciones."
        )

    return editions_found
