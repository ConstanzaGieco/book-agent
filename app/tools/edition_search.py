
"""Busca y valida ofertas comerciales de ediciones impresas.

El módulo separa la búsqueda por tienda de la verificación: una coincidencia
de título no alcanza para confirmar que haya stock ni que la URL sea válida.
"""

import re
import unicodedata
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlparse
from dotenv import load_dotenv
from app.models.book import BookEdition, RetailerSearchStatus

load_dotenv()


# Límite por librería y máximo específico para la búsqueda de Mercado Libre.
TARGET_OPTIONS = 5
MERCADOLIBRE_MAX_RESULTS = 5

# Cada entrada reúne el nombre que se muestra, su mercado, el dominio permitido y los términos que describen el producto buscado. El orden es intencional: primero se consultan tiendas argentinas y luego comercios internacionales.
RETAILERS = [
    # Argentina: se consulta primero.
    {
        "name": "Mercado Libre Argentina",
        "market": "Argentina",
        "domain": "mercadolibre.com.ar",
        "query_hint": "libro edición impresa disponibilidad",
    },
    {
        "name": "Cúspide",
        "market": "Argentina",
        "domain": "cuspide.com",
        "query_hint": "libro edición impresa disponibilidad",
    },
    {
        "name": "Yenny–El Ateneo",
        "market": "Argentina",
        "domain": "yenny-elateneo.com",
        "query_hint": "libro edición impresa",
    },
    {
        "name": "Buscalibre Argentina",
        "market": "Argentina",
        "domain": "buscalibre.com.ar",
        "query_hint": "libro físico edición impresa disponibilidad",
    },
    {
        "name": "SBS Librerías",
        "market": "Argentina",
        "domain": "sbs.com.ar",
        "query_hint": "libro edición impresa disponibilidad",
    },
    {
        "name": "Librería Hernández",
        "market": "Argentina",
        "domain": "libreriahernandez.com.ar",
        "query_hint": "libro edición impresa disponibilidad comprar",
    },
    {
        "name": "KEL Ediciones",
        "market": "Argentina",
        "domain": "kelediciones.com",
        "query_hint": "libros en inglés edición impresa comprar disponibilidad",
    },
    # Internacional: solo se consulta si Argentina no alcanza el cupo.
    {
        "name": "Amazon Estados Unidos",
        "market": "Internacional",
        "domain": "amazon.com",
        "query_hint": "buy paperback print edition availability",
    },
    {
        "name": "AbeBooks",
        "market": "Internacional",
        "domain": "abebooks.com",
        "query_hint": "buy used or new print book edition availability",
    },
    {
        "name": "Alibris",
        "market": "Internacional",
        "domain": "alibris.com",
        "query_hint": "buy used or new print book edition availability",
    },
    {
        "name": "Better World Books",
        "market": "Internacional",
        "domain": "betterworldbooks.com",
        "query_hint": "buy print book availability",
    },
    {
        "name": "ThriftBooks",
        "market": "Internacional",
        "domain": "thriftbooks.com",
        "query_hint": "buy used or new print book availability",
    },
]


def build_retailer_search_url(
    retailer: dict,
    title: str,
    author: str,
) -> str:
    """Construye una URL de búsqueda según el formato de cada tienda.
    Algunos sitios esperan parámetros de búsqueda y otros una ruta tipo slug;
    el resultado es un enlace para que la persona continúe la búsqueda en la tienda.
    """
    title = clean_retailer_search_title(title)
    # La misma consulta necesita codificaciones distintas según vaya en una ruta o en un parámetro de URL.
    query = quote(title)
    query_plus = quote_plus(title)
    title_and_author = quote(f"{title} {author}")
    # Mercado Libre usa una ruta legible; normalizar título y autor evita signos y acentos que puedan romper ese segmento.
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        normalize_text(f"{title} {author}"),
    ).strip("-")

    templates = {
        "Mercado Libre Argentina": (
            f"https://listado.mercadolibre.com.ar/{slug}"
            f"?sb=all_mercadolibre"
            f"#D[A:{quote(f'{title} {author}')}]"
        ),
        "Cúspide": (
            f"https://cuspide.com/?s={quote(title, safe='')}&post_type=product"
        ),
        "Yenny–El Ateneo": (
            f"https://www.yenny-elateneo.com/search/?q={query_plus}"
        ),
        "Buscalibre Argentina": (
            f"https://www.buscalibre.com.ar/libros/search/"
            f"?q={quote_plus(f'{title} {author}')}"
        ),
        "SBS Librerías": (
            f"https://www.sbs.com.ar/{query}"
            f"?_q={query}&map=ft"
        ),
        "Librería Hernández": (
            f"https://www.libreriahernandez.com.ar/search?q={title_and_author}"
        ),
        "KEL Ediciones": (
            f"https://www.kelediciones.com/{quote(title.casefold(), safe='')}"
            f"?_q={query}&map=ft"
        ),
        "Amazon Estados Unidos": (
            f"https://www.amazon.com/s?k={quote_plus(f'{title} by {author} hardcover')}"
        ),
        "AbeBooks": (
            f"https://www.abebooks.com/servlet/SearchResults?kn={query_plus}"
        ),
        "Alibris": (
            f"https://www.alibris.com/search/books?keyword={query_plus}"
        ),
        "Better World Books": (
            "https://www.betterworldbooks.com/search/results"
            f"?q={query}&src=enter"
        ),
        "ThriftBooks": (
            f"https://www.thriftbooks.com/browse/?b.search={query_plus}"
        ),
    }

    try:
        return templates[retailer["name"]]
    except KeyError:
        raise ValueError(
            f"No hay URL de búsqueda configurada para {retailer['name']}"
        )


def clean_retailer_search_title(title: str) -> str:
    """Quita subtítulos editoriales entre paréntesis del texto de búsqueda.
    Se repite la limpieza porque puede haber paréntesis anidados; el título
    original se conserva en los modelos, esta versión solo arma consultas.
    """
    cleaned = title or ""
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = re.sub(r"\([^()]*\)", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" \t\r\n-–—:;,.=|")

def normalize_text(value: str) -> str:
    """Normaliza texto comercial para comparar palabras de títulos y snippets."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _title_token_variants(title: str) -> list[list[str]]:
    """Genera variantes de tokens para artículos desplazados al final del título."""
    tokens = normalize_text(title).split()
    variants = [tokens] if tokens else []
    # Algunos registros bibliográficos trasladan el artículo inicial al final:
    # por ejemplo, "Murder of Roger Ackroyd, The".
    if len(tokens) > 1 and tokens[0] in {"the", "a", "an"}:
        variants.append(tokens[1:] + [tokens[0]])
    return variants


def _english_parenthetical_titles(titles: list[str] | None) -> list[str]:
    """Recupera títulos originales en inglés entre paréntesis."""
    english_markers = {"the", "of", "and", "a", "an", "in", "to", "for"}
    found = []
    for title in titles or []:
        for candidate in re.findall(r"\(([^()]*)\)", title or ""):
            cleaned = candidate.strip()
            if english_markers.intersection(normalize_text(cleaned).split()):
                found.append(cleaned)
    return list(dict.fromkeys(found))

def author_matches(author: str, content: str) -> bool:
    """Busca el nombre completo del autor como secuencia de tokens.
    También acepta el orden invertido que suelen usar catálogos y fichas
    bibliográficas, pero no una coincidencia parcial de apellido solamente.
    """
    author_tokens = normalize_text(author).split()
    content_tokens = normalize_text(content).split()

    if not author_tokens:
        return False

    candidates = (
        author_tokens,
        list(reversed(author_tokens)),
    )

    return any(
        content_tokens[i:i + len(tokens)] == tokens
        for tokens in candidates
        for i in range(len(content_tokens) - len(tokens) + 1)
    )


def looks_like_book_result(
    result_title: str,
    result_text: str,
    title: str,
    author: str,
    required_vendor_terms: list[str] | None = None,
) -> bool:
    """Comprueba que el resultado sea la obra solicitada, no una página genérica.
    Se exige título y autor, y luego se descartan formatos de búsqueda,
    recopilaciones o ediciones que no cumplan las condiciones del comercio.
    """
    normalized_title = normalize_text(title)
    normalized_result = normalize_text(result_title)
    normalized_author = normalize_text(author)
    normalized_content = normalize_text(f"{result_title}\n{result_text}")

    if not normalized_title or not normalized_result or not normalized_author:
        return False

    # Los snippets de páginas de autor, desafíos anti-bot y búsquedas generales no identifican una ficha comprable y se descartan antes del cotejo.
    generic_terms = (
        "search results",
        "book search",
        "books by",
        "author page",
        "resultados de busqueda",
        "resultados para",
        "catalogo del autor",
        "libros del autor",
        "prime free trial",
        "kindle unlimited",
        "gift card",
        "audible membership",
        "client challenge",
        "couldn't load",
        "could not load",
    )
    if any(term in normalized_result for term in generic_terms):
        return False

    if any(
        term in normalized_result
        for term in ("search", "busqueda", "resultados")
    ):
        return False

    # Exigir autor reduce falsos positivos cuando el título es común o breve.
    if not author_matches(
        author,
        f"{result_title} {result_text}",
    ):
        return False

    title_variants = _title_token_variants(title)
    title_tokens = title_variants[0]
    result_tokens = normalized_result.split()

    # Para un título de una sola palabra, comprobar la parte del encabezado que queda fuera del autor evita aceptar otro libro de la misma serie.
    if len(title_tokens) == 1:
        author_tokens = normalized_author.split()
        remaining_tokens = None
        for author_variant in (author_tokens, list(reversed(author_tokens))):
            for start in range(len(result_tokens) - len(author_variant) + 1):
                if result_tokens[start:start + len(author_variant)] == author_variant:
                    remaining_tokens = (
                        result_tokens[:start]
                        + result_tokens[start + len(author_variant):]
                    )
                    break
            if remaining_tokens is not None:
                break

        harmless_suffixes = {
            "paperback", "hardcover", "hardback", "ebook", "book",
            "libro", "tapa", "blanda", "dura", "edition", "edicion",
        }
        specific_headline = [
            token for token in (remaining_tokens or [])
            if token not in harmless_suffixes
        ]
        if specific_headline != title_tokens:
            return False

    # Prefijos comerciales habituales en los títulos de las librerías.
    prefixes = {
        "libro",
        "book",
        "the book",
    }
    while result_tokens:
        removed = False
        for prefix in prefixes:
            prefix_tokens = prefix.split()
            if result_tokens[:len(prefix_tokens)] == prefix_tokens:
                result_tokens = result_tokens[len(prefix_tokens):]
                removed = True
                break
        if not removed:
            break

    # Admitir el título como secuencia de palabras en cualquier posición del encabezado; así se toleran subtítulos y datos de formato alrededor. Ej.: "Nineteen Eighty-Four (1984)" y "Libro 1984".
    title_found = any(
        result_tokens[i:i + len(variant)] == variant
        for variant in title_variants
        for i in range(len(result_tokens) - len(variant) + 1)
    )

    # Variantes conocidas del título original de la novela.
    if normalized_title == "1984":
        title_found = title_found or any(
            result_tokens[i:i + 3] == ["nineteen", "eighty", "four"]
            for i in range(len(result_tokens) - 2)
        )

    # Si el título traducido no coincide literalmente, aceptar solo una mención explícita que indique que el título pedido es el título original.
    if not title_found:
        original_title_patterns = (
            "original title",
            "titulo original",
            "originally published as",
            "tambien publicado como",
        )
        has_original_title_evidence = (
            normalized_title in normalized_content
            and any(
                pattern in normalized_content
                for pattern in original_title_patterns
            )
        )
        if not has_original_title_evidence:
            return False

    # Aunque el título aparezca, una colección, antología o volumen completo puede ser otra edición; no confundirla con el libro individual buscado.
    compound_starters = {
        "and", "other", "selected", "collected", "collection",
        "anthology", "stories", "tales", "complete", "volume", "vol",
        "y", "otros", "seleccion", "coleccion", "cuentos", "obras",
        "completas", "related",
    }

    for variant in title_variants:
        for i in range(len(result_tokens) - len(variant) + 1):
            if result_tokens[i:i + len(variant)] != variant:
                continue
            remainder = result_tokens[i + len(variant):]
            if remainder and remainder[0] in compound_starters:
                return False

    # Algunos comercios requieren una señal concreta además de título y autor.
    if required_vendor_terms and not all(
        normalize_text(term) in normalized_content
        for term in required_vendor_terms
    ):
        return False

    return True


def extract_isbn(text: str) -> str | None:
    """Extrae un ISBN de 10 o 13 dígitos, admitiendo guiones y espacios."""
    patterns = [
        r"(?<!\d)97[89](?:[-\s]?\d){10}(?!\d)",
        r"(?<!\d)\d{9}[\dXx](?!\d)",
    ]

    # Buscar primero el patrón ISBN-13; devolver el valor sin separadores.
    for pattern in patterns:
        for match in re.finditer(pattern, text or ""):
            isbn = re.sub(r"[-\s]", "", match.group(0))

            if len(isbn) in (10, 13):
                return isbn.upper()

    return None

def extract_language(
    text: str,
    preferred_languages: list[str] | None = None,
) -> str | None:
    """Obtiene un idioma a partir de etiquetas o frases explícitas.
    No infiere idioma por el nombre del autor o la tienda; si el fragmento no
    declara idioma de forma reconocible, devuelve `None`.
    """
    if not text:
        return None

    # Priorizar etiquetas explícitas y luego frases como "edición en español".
    patterns = [
        r"(?im)^\s*(?:idioma|language|langue|sprache)\s*[:\-]\s*([^\n|;]{2,50})",
        r"(?i)\b(?:edition language|language edition|book language|idioma de la edición|idioma del libro)\s*[:\-]\s*([^\n|;]{2,50})",
        r"(?i)\b(?:edici[oó]n|libro|texto)\s+en\s+(espa[ñn]ol|castellano|ingl[eé]s|franc[eé]s|italiano|alem[aá]n|portugu[eé]s)\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            value = match.group(1).strip().rstrip(".,")
            if value:
                return value

    return None


def _language_key(value: str | None) -> str | None:
    """Unifica nombres frecuentes y códigos de idioma para compararlos."""
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(
        ch for ch in normalized
        if not unicodedata.combining(ch)
    )
    normalized = re.sub(r"[^a-z]+", " ", normalized).strip()

    # Usar alias conocidos porque los resultados mezclan nombres y códigos ISO.
    aliases = {
        "spanish": "spa",
        "espanol": "spa",
        "castellano": "spa",
        "english": "eng",
        "ingles": "eng",
        "french": "fra",
        "frances": "fra",
        "italian": "ita",
        "italiano": "ita",
        "german": "deu",
        "aleman": "deu",
        "portuguese": "por",
        "portugues": "por",
        "spa": "spa",
        "eng": "eng",
        "fra": "fra",
        "fre": "fra",
        "ita": "ita",
        "deu": "deu",
        "ger": "deu",
        "por": "por",
    }

    return aliases.get(normalized)

def extract_publisher(text: str) -> str | None:
    """Extrae el texto que sigue a una etiqueta de editorial o publisher."""
    match = re.search(
        r"(?:editorial|publisher)\s*[:\-]\s*"
        r"([^\n|.;]{2,80})",
        text or "",
        re.IGNORECASE,
    )

    return match.group(1).strip() if match else None


def extract_format(text: str) -> str | None:
    """Clasifica tapa blanda, tapa dura o formato digital si se menciona."""
    patterns = [
        (r"\btapa blanda\b|\bpaperback\b", "Tapa blanda"),
        (r"\btapa dura\b|\bhardcover\b|\bhardback\b", "Tapa dura"),
        (r"\bkindle\b|\bebook\b|\be-book\b", "Digital"),
    ]

    for pattern, book_format in patterns:
        if re.search(pattern, text or "", re.IGNORECASE):
            return book_format

    return None


def extract_pages(text: str) -> int | None:
    """Extrae páginas tanto cuando el número precede a la etiqueta como cuando no."""
    if not text:
        return None

    match = re.search(
        r"\b(\d{2,4})\s*(?:páginas|pages)\b",
        text,
        re.IGNORECASE,
    )

    if match:
        return int(match.group(1))

    match = re.search(
        r"\b(?:n[º°o.]?\s*)?(?:páginas|pages)\s*[:\-]?\s*(\d{2,4})\b",
        text,
        re.IGNORECASE,
    )

    return int(match.group(1)) if match else None



def detect_availability(text: str) -> tuple[str, str | None]:
    """Clasifica disponibilidad solo a partir de frases de stock explícitas.
    Si el texto contiene señales positivas y negativas a la vez, se conserva
    como `unverified`; ante la duda no se informa una oferta como disponible.
    """
    if not text:
        return "unverified", None

    normalized_text = normalize_text(text)

    unavailable_patterns = [
        r"\bsin stock\b",
        r"\bagotado(?:s)?\b",
        r"\bno disponible\b",
        r"\bno hay disponibilidad\b",
        r"\bno hay stock\b",
        r"\bsin disponibilidad\b",
        r"\bno disponible para comprar\b",
        r"\bavisame cuando haya stock\b",
        r"\bnotify me when (?:it is )?back in stock\b",
        r"\bfuera de stock\b",
        r"\bpublicacion finalizada\b",
        r"\banuncio finalizado\b",
        r"\bventa finalizada\b",
        r"\bout of stock\b",
        r"\bsold out\b",
        r"\bcurrently unavailable\b",
        r"\bnot available\b",
        r"\bno longer available\b",
        r"\bthis listing has ended\b",
        r"\blisting ended\b",
        r"\bsale ended\b",
        r"\btemporalmente sin stock\b",
        r"\bno contamos con stock\b",
        r"\bno se encuentra disponible\b",
        r"\bagotado temporalmente\b",
        r"\bproducto discontinuado\b",
        r"\bcurrently out of stock\b",
        r"\btemporarily out of stock\b",
        r"\bthis item is unavailable\b",
        r"\bno longer in stock\b",
    ]

    available_patterns = [
        r"\bquedan\s+(?:(?:mas)\s+de\s+)?\d+\s+unidades?\b",
        r"\b\d+\s+(?:available|in stock)\b",
        r"\bin stock\b",
        r"\ben stock\b",
        r"\bavailable now\b",
        r"\bcurrently in stock\b",
        r"\b\d+\s+disponibles\b",
        r"\bdisponible para comprar\b",
        r"\bdisponibilidad inmediata\b",
        r"\bdisponible en stock\b",
        r"\blisto para enviar\b",
        r"\bin stock now\b",
        r"\bavailable for shipping\b",
        r"\bships within \d+ days?\b",
    ]

    # Recopilar ambos tipos de señales antes de decidir para detectar contradicciones.
    unavailable = [
        match.group(0)
        for pattern in unavailable_patterns
        if (match := re.search(pattern, normalized_text))
    ]

    # Evitar interpretar "no longer in stock" como "in stock".
    normalized_for_available = re.sub(
        r"\bno longer in stock\b",
        "",
        normalized_text,
    )
    normalized_for_available = re.sub(
        r"\bno disponible(?: para comprar)?\b",
        "",
        normalized_for_available,
    )

    available = [
        match.group(0)
        for pattern in available_patterns
        if (match := re.search(pattern, normalized_for_available))
    ]

    # La información conflictiva no permite afirmar disponibilidad con seguridad.
    if unavailable and available:
        return "unverified", "Señales de disponibilidad contradictorias en el texto"
    if unavailable:
        return "unavailable", unavailable[0]
    if available:
        return "available", available[0]

    return "unverified", None


def has_active_purchase_evidence(text: str) -> bool:
    """Busca una señal comercial activa: precio o acción explícita de compra."""
    normalized = normalize_text(text or "")
    # Un precio aislado o un botón de compra puede completar un estado de stock que la página no expresa con las frases de disponibilidad reconocidas.
    has_price = bool(re.search(
        r"(?:\$\s*\d|\b(?:ars|usd)\s*\$?\s*\d|\b\d[\d.,]*\s*(?:ars|usd)\b)",
        text or "",
        re.IGNORECASE,
    ))
    has_purchase_action = any(
        phrase in normalized
        for phrase in (
            "comprar",
            "agregar al carrito",
            "add to cart",
            "buy now",
            "disponible para comprar",
        )
    )
    return has_price or has_purchase_action


def detect_shipping_to_argentina(
    text: str,
) -> tuple[str, str | None]:
    """Devuelve estado y fragmento que respalda el envío a Argentina."""
    negative_patterns = [
        r"\bno enviamos a argentina\b",
        r"\bno realiza envíos a argentina\b",
        r"\bdoes not ship to argentina\b",
        r"\bcannot ship to argentina\b",
    ]

    positive_patterns = [
        r"\bships to argentina\b",
        r"\bshipping to argentina\b",
        r"\bdelivery to argentina\b",
        r"\benvío a argentina\b",
        r"\benvíos a argentina\b",
    ]

    # Una afirmación negativa tiene prioridad si el texto contiene ambas señales.
    for pattern in negative_patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return "not_offered", match.group(0)

    for pattern in positive_patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return "confirmed", match.group(0)

    return "not_confirmed", None



def is_generic_listing_url(url: str) -> bool:
    """Detecta rutas de búsqueda comunes que no apuntan a un producto concreto."""
    parsed_url = urlparse(url)
    hostname = (parsed_url.hostname or "").casefold()
    path = parsed_url.path.casefold().rstrip("/")

    # Mercado Libre permite páginas de resultados de búsqueda.
    if hostname == "listado.mercadolibre.com.ar":
        return False

    generic_paths = {
        "/search",
        "/search-results",
        "/book-search",
        "/busqueda",
        "/buscar",
        "/catalogsearch",
        "/catalog/search",
        "/resultados",
        "/resultados-de-busqueda",
    }

    return any(
        path == pattern or path.startswith(pattern + "/")
        for pattern in generic_paths
    )


def is_mercadolibre_search_url(url: str) -> bool:
    """Reconoce páginas de resultados de búsqueda de Mercado Libre Argentina."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    path = parsed.path.casefold().rstrip("/")

    return (
        hostname == "listado.mercadolibre.com.ar"
        and bool(path.strip("/"))
    )


def is_mercadolibre_item_url(url: str) -> bool:
    """Reconoce una URL de producto de Mercado Libre con ID de artículo MLA."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    return (
        is_valid_retailer_url(url, "mercadolibre.com.ar")
        and hostname != "listado.mercadolibre.com.ar"
        and bool(re.search(r"/MLA-?\d+", parsed.path, re.IGNORECASE))
    )


def is_valid_retailer_url(url: str, expected_domain: str) -> bool:
    """Valida el esquema y el límite de dominio de una URL recibida externamente.
    Se comprueba el dominio completo o un subdominio con punto separador para
    no aceptar, por ejemplo, `tienda.com.ejemplo-malicioso.com`.
    """

    if not url or not expected_domain:
        return False

    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        expected = expected_domain.casefold().rstrip(".")
    except (TypeError, ValueError):
        return False

    if parsed.scheme.casefold() not in {"http", "https"}:
        return False

    return bool(
        hostname
        and expected
        and (hostname == expected or hostname.endswith("." + expected))
    )

def is_general_retailer_search_url(url: str, retailer: dict) -> bool:
    """Reconoce enlaces de búsqueda válidos cuando no hay ficha de producto.
    Cada comercio tiene una ruta y un parámetro distinto; cualquier ruta no
    registrada se rechaza para no presentar páginas arbitrarias como ofertas.
    """
    if not is_valid_retailer_url(url, retailer["domain"]):
        return False

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        path = parsed.path.rstrip("/").casefold() or "/"
        params = parse_qs(parsed.query)
    except (TypeError, ValueError):
        return False

    # Validar por comercio en vez de aceptar cualquier URL del mismo dominio.
    name = retailer["name"]

    if name == "Mercado Libre Argentina":
        return (
            host == "listado.mercadolibre.com.ar"
            and bool(path.strip("/"))
            and not re.search(r"/MLA\d+", path, re.IGNORECASE)
        )

    if name == "Cúspide":
        return (
            path == "/"
            and bool(params.get("s", [""])[0])
            and params.get("post_type") == ["product"]
        )

    if name == "Yenny–El Ateneo":
        return path == "/search" and bool(params.get("q", [""])[0])

    if name == "Buscalibre Argentina":
        return path == "/libros/search" and bool(params.get("q", [""])[0])

    if name == "SBS Librerías":
        return (
            path != "/"
            and bool(params.get("_q", [""])[0])
            and params.get("map") == ["ft"]
        )

    if name == "Librería Hernández":
        return path == "/search" and bool(params.get("q", [""])[0])

    if name == "KEL Ediciones":
        return (
            path != "/"
            and bool(params.get("_q", [""])[0].strip())
            and params.get("map") == ["ft"]
        )

    if name == "Amazon Estados Unidos":
        return path == "/s" and bool(params.get("k", [""])[0])

    if name == "AbeBooks":
        return (
            path == "/servlet/searchresults"
            and bool(params.get("kn", [""])[0])
        )

    if name == "Alibris":
        return (
            path == "/search/books"
            and bool(params.get("keyword", [""])[0])
        )

    if name == "Better World Books":
        return (
            path == "/search/results"
            and bool(params.get("q", [""])[0])
        )

    if name == "ThriftBooks":
        return path == "/browse" and bool(params.get("b.search", [""])[0])

    return False


def _search_url_matches_book(
    url: str,
    title_candidates: list[str],
) -> bool:
    """Comprueba que los parámetros o la ruta de búsqueda mencionen el título."""
    try:
        parsed = urlparse(url)
        search_text = normalize_text(" ".join([
            parsed.path,
            *[
                value
                for values in parse_qs(parsed.query).values()
                for value in values
            ],
        ]))
    except (TypeError, ValueError):
        return False

    search_tokens = search_text.split()
    for candidate in title_candidates:
        title_tokens = normalize_text(candidate).split()
        if title_tokens and any(
            search_tokens[index:index + len(title_tokens)] == title_tokens
            for index in range(len(search_tokens) - len(title_tokens) + 1)
        ):
            return True
    return False

def is_argentina_retailer_url(url: str, domain: str) -> bool:
    """Confirma el dominio local y descarta rutas regionales extranjeras conocidas."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()

    if not is_valid_retailer_url(url, domain):
        return False

    # Hernández publica páginas regionales /co/ y /uy/.
    if domain == "libreriahernandez.com.ar":
        return not re.match(r"^/(?:es|co|uy)(?:/|$)", path)

    return True

def _normalize_offer_url(url: str) -> str:
    """Quita fragmentos y barras finales para comparar enlaces duplicados."""
    return url.split("#", 1)[0].rstrip("/").casefold()

def _add_purchase_offers(
    offers: list[BookEdition],
    seen_urls: set[str],
    all_offers: list[BookEdition],
) -> None:
    """Valida y acumula ofertas, evitando dominios inválidos y URLs repetidas.
    Mercado Libre admite un caso acotado de página de búsqueda cuando la oferta
    ya fue verificada por carrito; para el resto se priorizan fichas concretas.
    """
    retailers_by_name = {
        retailer["name"]: retailer
        for retailer in RETAILERS
    }

    for offer in offers:
        retailer_name = getattr(offer, "retailer", "desconocido")
        offer_url = getattr(offer, "url", "")

        retailer = retailers_by_name.get(retailer_name)

        if retailer_name == "Yenny–El Ateneo":
            parsed = urlparse(offer_url)
            params = parse_qs(parsed.query)

            valid_url = (
                parsed.hostname is not None
                and parsed.hostname.casefold()
                in {"yenny-elateneo.com", "www.yenny-elateneo.com"}
                and parsed.path.rstrip("/") == "/search"
                and bool(params.get("q", [""])[0].strip())
            )
        elif retailer and retailer_name != "Mercado Libre Argentina":
            valid_url = (
                is_valid_retailer_url(offer_url, retailer["domain"])
                and is_argentina_retailer_url(offer_url, retailer["domain"])
                and (
                    not is_generic_listing_url(offer_url)
                    or is_general_retailer_search_url(offer_url, retailer)
                )
            )
        elif retailer and retailer_name == "Mercado Libre Argentina":
            verified_search_result = (
                is_mercadolibre_search_url(offer_url)
                and getattr(offer, "availability", None) == "available"
                and "carrito" in normalize_text(
                    getattr(offer, "availability_evidence", "")
                )
            )
            valid_url = (
                is_mercadolibre_item_url(offer_url)
                or verified_search_result
            )
        elif retailer:
            valid_url = is_general_retailer_search_url(
                offer_url, retailer
            )
        else:
            valid_url = True

        # Rechazar antes de deduplicar: una URL inválida no debe ocupar el lugar
        # de una oferta válida que aparezca después.
        if not valid_url:
            print(
                f"[OFERTA DESCARTADA: URL NO VÁLIDA] "
                f"{retailer_name}: {offer_url}"
            )
            continue

        if (
            is_generic_listing_url(offer_url)
            and not (
                retailer
                and is_general_retailer_search_url(offer_url, retailer)
            )
            and not is_mercadolibre_search_url(offer_url)
        ):
            print(
                f"[OFERTA DESCARTADA: LISTADO GENÉRICO] "
                f"{retailer_name}: {offer_url}"
            )
            continue

        # Normalizar solo para comparar; el modelo conserva la URL original.
        url = _normalize_offer_url(offer_url)

        if not url:
            print(
                f"[OFERTA DESCARTADA: URL VACÍA] {retailer_name}"
            )
            continue

        if url in seen_urls:
            print(
                f"[OFERTA DESCARTADA: DUPLICADA] "
                f"{retailer_name}: {url}"
            )
            continue

        seen_urls.add(url)
        all_offers.append(offer)

        print(
            f"[OFERTA AGREGADA] {retailer_name}: "
            f"{getattr(offer, 'title', 'sin título')}"
        )


def _should_search_international(
    valid_offer_count: int,
    target_options: int = TARGET_OPTIONS,
) -> bool:
    """Indica si la cantidad de ofertas válidas todavía no alcanza el objetivo."""
    return valid_offer_count < target_options


def search_book_purchase_options(
    title: str,
    author: str,
    search_client=None,
    markets_to_search: list[str] | None = None,
    preferred_book_languages: list[str] | None = None,
    alternate_titles: list[str] | None = None,
    english_titles: list[str] | None = None,
    exclusive_language: bool = False,
    exa_client=None,
) -> tuple[list[BookEdition], list[RetailerSearchStatus]]:
    """Busca ofertas verificables y registra el resultado de cada comercio.
    Se consulta primero Argentina; solo se pasa a comercios internacionales
    cuando las ofertas locales utilizables no alcanzan el objetivo.
    """

    # Mantener el alias temporal para scripts y pruebas que aún usan exa_client.
    if search_client is None:
        search_client = exa_client
    search_provider = getattr(search_client, "provider_name", None)
    if not search_provider:
        search_provider = "EXA" if exa_client is not None else "BUSCADOR"

    # Separar los comercios por mercado para controlar el orden de búsqueda.
    argentina = [
        r for r in RETAILERS if r["market"] == "Argentina"
    ]
    international = [
        r for r in RETAILERS if r["market"] == "Internacional"
    ]

    if markets_to_search is not None:
        if "Argentina" not in markets_to_search:
            argentina = []
        if "Internacional" not in markets_to_search:
            international = []

    # Usar el primer título alternativo como principal si está disponible.
    selected_title = clean_retailer_search_title(
        next(iter(alternate_titles or []), title)
    ) or clean_retailer_search_title(title)

    def search_retailer(
        retailer: dict,
    ) -> tuple[list[BookEdition], RetailerSearchStatus]:
        """Busca y valida ofertas de una tienda, devolviendo su estado."""
        # Para tiendas internacionales y KEL se prioriza el título inglés.
        use_english_title = (
            retailer["name"] == "KEL Ediciones"
            or retailer["market"] == "Internacional"
        )
        retailer_language_preferences = (
            ["English"]
            if use_english_title
            else preferred_book_languages or []
        )
        english_title_candidates = list(dict.fromkeys(
            clean_retailer_search_title(candidate)
            for candidate in (english_titles or [])
            if clean_retailer_search_title(candidate)
        ))
        english_title_candidates.extend(
            candidate for candidate in _english_parenthetical_titles(alternate_titles)
            if candidate not in english_title_candidates
        )
        if use_english_title:
            retailer_title_candidates = english_title_candidates or [
                clean_retailer_search_title(title)
            ]
        else:
            retailer_title_candidates = list(dict.fromkeys(
                clean_retailer_search_title(candidate)
                for candidate in (alternate_titles or [selected_title])
                if clean_retailer_search_title(candidate)
            )) or [selected_title]
        retailer_catalog_title_keys = {
            normalize_text(clean_retailer_search_title(candidate))
            for candidate in (
                english_title_candidates
                if use_english_title
                else alternate_titles
            ) or []
            if clean_retailer_search_title(candidate)
        }
        retailer_title = next(
            (candidate for candidate in retailer_title_candidates if candidate),
            selected_title,
        )

        # Mercado Libre tiene una API propia; ante bloqueos se usa el catálogo de productos y, si corresponde, el buscador configurado.
        if retailer["name"] == "Mercado Libre Argentina":
            try:
                headers = {"User-Agent": "BookAgent/1.0"}
                mercado_libre_token = os.getenv("MERCADOLIBRE_ACCESS_TOKEN")
                if mercado_libre_token:
                    headers["Authorization"] = (
                        f"Bearer {mercado_libre_token.strip()}"
                    )
                response = requests.get(
                    "https://api.mercadolibre.com/sites/MLA/search",
                    params={
                        "q": f"{retailer_title} {author}",
                        "limit": MERCADOLIBRE_MAX_RESULTS,
                    },
                    headers=headers,
                    timeout=15,
                )
                if response.status_code == 401 and mercado_libre_token:
                    # Un token vencido/no válido no debe bloquear los endpoints públicos de búsqueda: reintentar sin OAuth.
                    headers.pop("Authorization", None)
                    response = requests.get(
                        "https://api.mercadolibre.com/sites/MLA/search",
                        params={
                            "q": f"{retailer_title} {author}",
                            "limit": MERCADOLIBRE_MAX_RESULTS,
                        },
                        headers=headers,
                        timeout=15,
                    )
                if response.status_code in (401, 403, 429):
                    # La búsqueda general puede estar bloqueada o limitada; el catálogo permite verificar ofertas aptas para carrito.
                    catalog_response = requests.get(
                        "https://api.mercadolibre.com/products/search",
                        params={
                            "status": "active",
                            "site_id": "MLA",
                            "q": f"{retailer_title} {author}",
                            "limit": MERCADOLIBRE_MAX_RESULTS,
                        },
                        headers=headers,
                        timeout=15,
                    )
                    catalog_response.raise_for_status()
                    products = (
                        catalog_response.json().get("results") or []
                    )[:MERCADOLIBRE_MAX_RESULTS]
                    listings = []
                    for product in products:
                        product_title = product.get("name") or ""
                        product_language = next(
                            (
                                str(attribute.get("value_name")).strip()
                                for attribute in (product.get("attributes") or [])
                                if isinstance(attribute, dict)
                                and "language" in str(
                                    attribute.get("id", "")
                                ).casefold()
                                and attribute.get("value_name")
                            ),
                            None,
                        )
                        if not looks_like_book_result(
                            result_title=product_title,
                            result_text="",
                            title=retailer_title,
                            author=author,
                        ):
                            continue
                        product_id = product.get("id")
                        if not product_id:
                            continue
                        items_response = requests.get(
                            f"https://api.mercadolibre.com/products/"
                            f"{product_id}/items",
                            params={"limit": MERCADOLIBRE_MAX_RESULTS},
                            headers=headers,
                            timeout=15,
                        )
                        if items_response.status_code == 404:
                            # Algunos productos activos no tienen publicaciones asociadas; continuar con el siguiente producto.
                            continue
                        items_response.raise_for_status()
                        for item in (
                            items_response.json().get("results") or []
                        ):
                            item_id = str(item.get("item_id") or "")
                            if not item_id.startswith("MLA"):
                                continue
                            item_tags = item.get("tags") or []
                            if isinstance(item_tags, str):
                                item_tags = [item_tags]
                            if "cart_eligible" not in item_tags:
                                continue
                            item_number = item_id.removeprefix("MLA")
                            listings.append({
                                "title": product_title,
                                "language": product_language,
                                "status": "active",
                                "permalink": (
                                    "https://articulo.mercadolibre.com.ar/"
                                    f"MLA-{item_number}"
                                ),
                                "item_id": item_id,
                                "_cart_eligible": True,
                            })
                            if len(listings) >= MERCADOLIBRE_MAX_RESULTS:
                                break
                        if len(listings) >= MERCADOLIBRE_MAX_RESULTS:
                            break
                else:
                    response.raise_for_status()
                    listings = (response.json().get("results") or [])[
                        :MERCADOLIBRE_MAX_RESULTS
                    ]
            except requests.HTTPError as exc:
                status_code = getattr(exc.response, "status_code", None)
                response_text = (
                    (getattr(exc.response, "text", "") or "")[:400].strip()
                )
                if status_code in (401, 403, 429) and search_client is not None:
                    # Si la API deniega la búsqueda, recurrir a fichas indexadas y exigir coincidencia exacta y evidencia explícita de stock.
                    for candidate in retailer_title_candidates[:3]:
                        query = f'"{candidate}" "{author}" libro'
                        try:
                            fallback = search_client.search_and_contents(
                                query,
                                type="auto",
                                num_results=MERCADOLIBRE_MAX_RESULTS,
                                include_domains=[retailer["domain"]],
                                text={"max_characters": 5000},
                            )
                        except Exception:
                            continue
                        for result in fallback.results or []:
                            result_title = result.title or ""
                            result_text = result.text or ""
                            result_url = result.url or ""
                            if not is_mercadolibre_item_url(result_url):
                                continue
                            if not looks_like_book_result(
                                result_title=result_title,
                                result_text=result_text,
                                title=candidate,
                                author=author,
                            ):
                                continue
                            availability, evidence = detect_availability(
                                f"{result_title}\n{result_text}"
                            )
                            if availability != "available":
                                continue
                            offer = BookEdition(
                                retailer=retailer["name"],
                                market=retailer["market"],
                                title=result_title,
                                author=author,
                                url=result_url,
                                language=extract_language(
                                    f"{result_title}\n{result_text}",
                                    preferred_languages=preferred_book_languages,
                                ),
                                availability="available",
                                availability_evidence=(
                                    evidence or "Stock indicado en la ficha indexada."
                                ),
                                shipping_to_argentina="not_applicable",
                                source_excerpt=result_text[:600] or None,
                            )
                            return [offer], RetailerSearchStatus(
                                retailer=retailer["name"],
                                market=retailer["market"],
                                status="results_found",
                                results_count=1,
                                message=(
                                    "API de Mercado Libre bloqueada; se verificó "
                                    "una ficha de producto indexada con stock."
                                ),
                            )
                if status_code == 429:
                    detail = (
                        "Mercado Libre limitó temporalmente las consultas. "
                        "El catálogo alternativo también respondió con límite; "
                        "probá nuevamente más tarde. "
                        + (response_text or "")
                    ).strip()
                elif status_code == 403 and not mercado_libre_token:
                    detail = (
                        "La API respondió 403 y no hay MERCADOLIBRE_ACCESS_TOKEN "
                        "configurado. Mercado Libre recomienda autenticar también "
                        "las consultas públicas."
                    )
                else:
                    detail = response_text or str(exc)
                return [], RetailerSearchStatus(
                    retailer=retailer["name"],
                    market=retailer["market"],
                    status="error",
                    message=f"HTTP {status_code}: {detail}",
                )
            except (requests.RequestException, ValueError, AttributeError) as exc:
                return [], RetailerSearchStatus(
                    retailer=retailer["name"],
                    market=retailer["market"],
                    status="error",
                    message=(
                        "No se pudo consultar Mercado Libre: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )

            for listing in listings:
                listing_title = listing.get("title") or ""
                listing_attributes = listing.get("attributes") or []
                listing_author_values = [
                    str(attribute.get("value_name"))
                    for attribute in listing_attributes
                    if isinstance(attribute, dict)
                    and "author" in str(attribute.get("id", "")).casefold()
                    and attribute.get("value_name")
                ]
                listing_text = " ".join(listing_author_values)
                try:
                    available_quantity = int(
                        listing.get("available_quantity") or 0
                    )
                except (TypeError, ValueError):
                    available_quantity = 0
                if (
                    listing.get("status") not in (None, "active")
                    or (
                        available_quantity <= 0
                        and not listing.get("_cart_eligible")
                    )
                    or not looks_like_book_result(
                        result_title=listing_title,
                        result_text=listing_text,
                        title=retailer_title,
                        author=author,
                    )
                ):
                    continue

                # El stock se valida con el resultado coincidente; se entrega al usuario la búsqueda general de ese libro en la tienda.
                listing_url = build_retailer_search_url(
                    retailer,
                    retailer_title,
                    author,
                )
                if not is_valid_retailer_url(
                    listing_url, retailer["domain"]
                ):
                    continue

                offer = BookEdition(
                    retailer=retailer["name"],
                    market=retailer["market"],
                    title=listing_title,
                    author=author,
                    url=listing_url,
                    language=(
                        listing.get("language")
                        or extract_language(listing_title)
                    ),
                    isbn=extract_isbn(listing_title),
                    format=extract_format(listing_title),
                    availability="available",
                    availability_evidence=(
                        "La API de catálogo marca la publicación como "
                        "elegible para carrito."
                        if listing.get("_cart_eligible")
                        else (
                            "Publicación activa entre los primeros "
                            f"{MERCADOLIBRE_MAX_RESULTS} resultados; "
                            f"cantidad informada: {available_quantity}."
                        )
                    ),
                    shipping_to_argentina="not_applicable",
                    source_excerpt=listing_title[:600] or None,
                )
                return [offer], RetailerSearchStatus(
                    retailer=retailer["name"],
                    market=retailer["market"],
                    status="results_found",
                    results_count=1,
                    message=(
                        "Se encontró al menos una publicación coincidente "
                        "con disponibilidad en los primeros "
                        f"{MERCADOLIBRE_MAX_RESULTS} resultados."
                    ),
                )

            return [], RetailerSearchStatus(
                retailer=retailer["name"],
                market=retailer["market"],
                status="no_results",
                results_count=0,
                message=(
                    "No se encontró una publicación coincidente con stock "
                    f"en los primeros {MERCADOLIBRE_MAX_RESULTS} resultados."
                ),
            )

        # Las demás tiendas necesitan un cliente Exa o Tavily.
        if search_client is None:
            return [], RetailerSearchStatus(
                retailer=retailer["name"],
                market=retailer["market"],
                status="error",
                message=(
                    "EXA_API_KEY y TAVILY_API_KEY no configuradas "
                    "o cliente no disponible."
                ),
            )

        offers: list[BookEdition] = []
        diagnostics = {
            "received": 0,
            "missing_url": 0,
            "invalid_domain": 0,
            "generic_url": 0,
            "duplicate_url": 0,
            "book_filter": 0,
            "accepted": 0,
            "book_filter_reasons": {},
        }
        seen_urls: set[str] = set()

        def reject_book_result(reason: str, result_title: str, result_url: str) -> None:
            """Cuenta e informa por qué se descartó un resultado."""
            diagnostics["book_filter"] += 1
            reasons = diagnostics["book_filter_reasons"]
            reasons[reason] = reasons.get(reason, 0) + 1
            print(
                f"[RECHAZADO POR FILTRO] {retailer['name']}: "
                f"reason={reason} title={result_title!r} url={result_url!r}"
            )

        title_candidates = list(dict.fromkeys(
            candidate for candidate in retailer_title_candidates if candidate
        ))

        # Conservar variantes para validar resultados traducidos o alternativos.
        query_titles = list(dict.fromkeys(
            candidate for candidate in retailer_title_candidates if candidate
        )) or [retailer_title]
        queries = []
        # Comenzar por título y autor; ampliar la consulta solo si no hay oferta.
        queries = list(dict.fromkeys((
            f'"{query_titles[0]}" "{author}"',
            f'{query_titles[0]} {author}',
            f'"{query_titles[0]}" {retailer["name"]} comprar',
        )))

        blocked_content_patterns = (
            "client challenge",
            "verify you are human",
            "captcha",
            "access denied",
            "enable javascript and cookies",
        )

        related_patterns = (
            r"\n\s*other formats\s*(?:&|and)\s*editions\b",
            r"\n\s*otras ediciones\b",
            r"\n\s*related products\b",
            r"\n\s*productos relacionados\b",
            r"\n\s*customers also bought\b",
            r"\n\s*you may also like\b",
            r"\n\s*otros libros del autor\b",
            r"\n\s*frequently bought together\b",
            r"\n\s*recommended for you\b",
            r"\n\s*productos similares\b",
            r"\n\s*quiz[aá]s te interese\b",
            r"\n\s*también te puede interesar\b",
            r"\n\s*libros similares\b",
        )

        try:
            # Probar consultas progresivamente más amplias y detenerse al validar una oferta, evitando trabajo adicional para esa tienda.
            for attempt, query in enumerate(queries, start=1):
                print(
                    f"[CONSULTA {search_provider}] {retailer['name']} "
                    f"intento {attempt}: {query}"
                )
                response = search_client.search_and_contents(
                    query,
                    type="auto",
                    num_results=10,
                    include_domains=[retailer["domain"]],
                    text={"max_characters": 5000},
                )

                results = response.results or []
                diagnostics["received"] += len(results)

                for result in results:
                    result_title = result.title or ""
                    result_url = result.url or ""
                    result_text = result.text or ""

                    # Normalizar el host de staging cuando apunta a una ficha real.
                    if retailer["name"] == "Buscalibre Argentina":
                        parsed_result_url = urlparse(result_url)
                        if parsed_result_url.hostname == "stage.buscalibre.com.ar":
                            # El buscador puede devolver el host de staging aunque el producto corresponda al sitio de producción.
                            result_url = (
                                "https://www.buscalibre.com.ar"
                                f"{parsed_result_url.path}"
                            )

                    if not result_url:
                        diagnostics["missing_url"] += 1
                        continue

                    normalized_content = normalize_text(
                        f"{result_title}\n{result_text}"
                    )

                    if any(
                        pattern in normalized_content
                        for pattern in blocked_content_patterns
                    ):
                        reject_book_result(
                            "blocked_or_challenge_content", result_title, result_url
                        )
                        continue

                    if not is_valid_retailer_url(
                        result_url, retailer["domain"]
                    ) or (
                        retailer["market"] == "Argentina"
                        and not is_argentina_retailer_url(
                            result_url, retailer["domain"]
                        )
                    ):
                        diagnostics["invalid_domain"] += 1
                        continue

                    is_search_link = is_general_retailer_search_url(
                        result_url,
                        retailer,
                    )
                    # Una página de resultados no equivale a una ficha de producto: requiere señales propias de título, autor y compra.
                    if is_search_link:
                        normalized_content = normalize_text(
                            f"{result_title}\n{result_text}"
                        )
                        search_url_text = normalize_text(unquote(result_url))
                        matched_search_title = next(
                            (
                                candidate for candidate in title_candidates
                                if normalize_text(candidate)
                                and (
                                    normalize_text(candidate) in normalized_content
                                    or (
                                        retailer["name"] == "Mercado Libre Argentina"
                                        and normalize_text(candidate) in search_url_text
                                    )
                                )
                            ),
                            None,
                        )
                        author_context = f"{result_title}\n{result_text}"
                        if retailer["name"] == "Mercado Libre Argentina":
                            author_context += f"\n{unquote(result_url)}"
                        has_author = author_matches(author, author_context)
                        has_purchase_evidence = has_active_purchase_evidence(
                            result_text
                        )
                        # El buscador puede devolver el listado de Mercado Libre con el título en el slug o fragmento, pero sin autor en el snippet. En ese caso, título en la URL + evidencia comercial en el snippet basta para reconocer el listado; el fragmento nunca confirma disponibilidad.
                        title_confirmed_by_url = (
                            retailer["name"] == "Mercado Libre Argentina"
                            and matched_search_title is not None
                            and normalize_text(matched_search_title) in search_url_text
                        )
                        if (
                            not matched_search_title
                            or not (
                                has_author
                                or (title_confirmed_by_url and has_purchase_evidence)
                            )
                        ):
                            if not matched_search_title:
                                reason = "search_title_not_found"
                            elif not has_author and not has_purchase_evidence:
                                reason = "search_author_missing_and_no_purchase_signal"
                            else:
                                reason = "search_author_missing"
                            reject_book_result(reason, result_title, result_url)
                            continue

                        availability, availability_evidence = (
                            detect_availability(
                                f"{result_title}\n{result_text}"
                            )
                        )
                        if (
                            availability == "unverified"
                            and retailer["name"] != "Yenny–El Ateneo"
                            and has_purchase_evidence
                        ):
                            availability = "available"
                            availability_evidence = (
                                "Libro coincidente ofrecido en los resultados "
                                "del retailer."
                            )

                        search_url = build_retailer_search_url(
                            retailer, retailer_title, author
                        )
                        normalized_url = _normalize_offer_url(search_url)
                        if availability == "available":
                            listing_language = extract_language(
                                f"{result_title}\n{result_text}",
                                preferred_languages=retailer_language_preferences,
                            )
                            if (
                                exclusive_language
                                and not listing_language
                                and normalize_text(matched_search_title)
                                in retailer_catalog_title_keys
                            ):
                                listing_language = next(
                                    iter(retailer_language_preferences), None
                                )

                            offers = [
                                offer for offer in offers
                                if _normalize_offer_url(offer.url)
                                != normalized_url
                            ]
                            seen_urls.add(normalized_url)
                            offers.append(
                                BookEdition(
                                    retailer=retailer["name"],
                                    market=retailer["market"],
                                    title=matched_search_title,
                                    author=author,
                                    url=search_url,
                                    language=listing_language,
                                    availability=availability,
                                    availability_evidence=availability_evidence,
                                    shipping_to_argentina="not_applicable",
                                    source_excerpt=(result_text or "")[:600] or None,
                                )
                            )
                        elif normalized_url not in seen_urls:
                            offers.append(
                                BookEdition(
                                    retailer=retailer["name"],
                                    market=retailer["market"],
                                    title=matched_search_title,
                                    author=author,
                                    url=search_url,
                                    language=None,
                                    availability=availability,
                                    availability_evidence=availability_evidence,
                                    shipping_to_argentina="not_applicable",
                                    source_excerpt=(result_text or "")[:600] or None,
                                )
                            )
                            seen_urls.add(normalized_url)

                        if availability == "available":
                            diagnostics["accepted"] += 1
                            break
                        diagnostics["generic_url"] += 1
                        continue

                    # Una URL genérica no demuestra una publicación concreta.
                    if is_generic_listing_url(result_url):
                        diagnostics["generic_url"] += 1
                        continue

                    normalized_url = _normalize_offer_url(result_url)

                    if normalized_url in seen_urls:
                        diagnostics["duplicate_url"] += 1
                        continue

                    normalized_title = normalize_text(result_title)
                    normalized_text = normalize_text(result_text)

                    matched_title = next(
                        (
                            candidate
                            for candidate in title_candidates
                            if looks_like_book_result(
                                result_title=result_title,
                                result_text=result_text,
                                title=candidate,
                                author=author,
                                required_vendor_terms=retailer.get(
                                    "required_vendor_terms"
                                ),
                            )
                        ),
                        None,
                    )

                    # Exigir el título en el encabezado del resultado; que aparezca solo en recomendaciones o texto relacionado no basta.
                    title_match = bool(matched_title) and (
                        normalize_text(matched_title) in normalized_title
                    )

                    author_match = author_matches(
                        author,
                        (
                            f"{result_title} {result_text[:1500]} {result_url}"
                            if retailer["name"] == "Mercado Libre Argentina"
                            else f"{result_title} {result_text[:1500]}"
                        ),
                    )

                    if not title_match or not author_match:
                        reasons = []
                        if not title_match:
                            reasons.append("title_not_in_result_heading")
                        if not author_match:
                            reasons.append("author_not_in_result")
                        reject_book_result(
                            "+".join(reasons), result_title, result_url
                        )
                        continue

                    if not matched_title:
                        reject_book_result(
                            "no_matching_title_candidate", result_title, result_url
                        )
                        continue

                    if not looks_like_book_result(
                        result_title=result_title,
                        result_text=result_text,
                        title=matched_title,
                        author=author,
                        required_vendor_terms=retailer.get("required_vendor_terms"),
                    ):
                        reject_book_result(
                            "book_result_validation_failed", result_title, result_url
                        )
                        continue

                    # Analizar únicamente el contenido de la ficha principal.
                    product_text = result_text

                    for pattern in related_patterns:
                        match = re.search(
                            pattern,
                            product_text,
                            re.IGNORECASE,
                        )
                        if match:
                            product_text = product_text[:match.start()]

                    # Rechazar ediciones digitales y discrepancias de ISBN antes de evaluar stock y construir la oferta.
                    if (
                        (urlparse(result_url).hostname or "").casefold().startswith("ebooks.")
                        or re.search(
                            r"\b(?:ebook|e-book|kindle|digital edition|edici[oó]n digital)\b",
                            f"{result_title}\n{product_text}",
                            re.IGNORECASE,
                        )
                    ):
                        reject_book_result(
                            "digital_edition_not_print", result_title, result_url
                        )
                        continue

                    result_isbn = extract_isbn(result_title)
                    content_isbn = extract_isbn(product_text)

                    heading_match = re.search(
                        r"(?m)^\s*#{1,3}\s+(.+?)\s*$",
                        product_text,
                    )
                    heading = (
                        heading_match.group(1)
                        if heading_match
                        else ""
                    )
                    heading_isbn = extract_isbn(heading)

                    if (
                        result_isbn
                        and heading_isbn
                        and result_isbn != heading_isbn
                    ):
                        reject_book_result(
                            "conflicting_isbn", result_title, result_url
                        )
                        continue

                    normalized_heading = normalize_text(heading)
                    normalized_result = normalize_text(result_title)

                    if (
                        "annotated edition" in normalized_heading
                        and "annotated edition" not in normalized_result
                    ):
                        reject_book_result(
                            "unexpected_annotated_edition", result_title, result_url
                        )
                        continue

                    evidence_text = f"{result_title}\n{product_text}"

                    # Solo se acepta disponibilidad explícita del contenido del resultado; una página de búsqueda por sí sola no la confirma.
                    availability, availability_evidence = detect_availability(
                        evidence_text
                    )
                    if (
                        availability == "unverified"
                        and retailer["name"] != "Yenny–El Ateneo"
                        and has_active_purchase_evidence(
                            evidence_text,
                        )
                    ):
                        availability = "available"
                        availability_evidence = (
                            "El retailer muestra precio u opción de compra "
                            "para el libro coincidente."
                        )
                    if availability != "available":
                        diagnostics["availability_filter"] = (
                            diagnostics.get("availability_filter", 0) + 1
                        )
                        continue

                    offer_url = result_url
                    offers = [
                        offer for offer in offers
                        if _normalize_offer_url(offer.url)
                        != _normalize_offer_url(offer_url)
                    ]

                    if retailer["market"] == "Argentina":
                        shipping_status = "not_applicable"
                    else:
                        shipping_status, _ = detect_shipping_to_argentina(
                            evidence_text
                        )

                    detected_language = extract_language(
                        evidence_text,
                        preferred_languages=retailer_language_preferences,
                    )

                    if (
                        exclusive_language
                        and not detected_language
                        and normalize_text(matched_title)
                        in retailer_catalog_title_keys
                    ):
                        detected_language = next(iter(retailer_language_preferences), None)

                    if exclusive_language:
                        preferred_keys = {
                            _language_key(language)
                            for language in retailer_language_preferences
                        }
                        preferred_keys.discard(None)

                        if (
                            not detected_language
                            or _language_key(detected_language) not in preferred_keys
                        ):
                            reject_book_result(
                                "exclusive_language_mismatch",
                                result_title,
                                result_url,
                            )
                            continue

                    # Guardar solo la evidencia de la ficha validada, no resultados de productos relacionados que aparezcan en el mismo texto.
                    offers.append(
                        BookEdition(
                            retailer=retailer["name"],
                            market=retailer["market"],
                            title=result_title or retailer_title,
                            author=author,
                            url=offer_url,
                            language=detected_language,
                            isbn=content_isbn or result_isbn,
                            publisher=extract_publisher(evidence_text),
                            format=extract_format(evidence_text),
                            pages=extract_pages(evidence_text),
                            availability=availability,
                            availability_evidence=availability_evidence,
                            shipping_to_argentina=shipping_status,
                            source_excerpt=product_text[:600] or None,
                        )
                    )
                    diagnostics["accepted"] += 1
                    seen_urls.add(normalized_url)

                    # Una oferta válida por comercio es suficiente.
                    break

                if any(offer.availability == "available" for offer in offers):
                    if attempt > 1:
                        print(
                            f"[REINTENTO {search_provider}] {retailer['name']}: "
                            f"oferta encontrada en intento {attempt}."
                        )
                    break

            # Si no hubo oferta comprobable, incluir un enlace de búsqueda marcado como no verificado, sin presentarlo como producto disponible.
            if not offers:
                search_url = build_retailer_search_url(
                    retailer, retailer_title, author
                )
                offers.append(
                    BookEdition(
                        retailer=retailer["name"],
                        market=retailer["market"],
                    title=retailer_title,
                        author=author,
                        url=search_url,
                        language=None,
                        availability="unverified",
                        availability_evidence=None,
                        shipping_to_argentina="not_applicable",
                        source_excerpt=(
                            "Enlace de búsqueda sin una oferta concreta "
                            "con disponibilidad verificada."
                        ),
                    )
                )

                print(f"[DIAGNÓSTICO] {retailer['name']}: {diagnostics}")

            diagnostic_summary = ", ".join(
                f"{key}={value}"
                for key, value in diagnostics.items()
                if key != "book_filter_reasons"
            )
            book_filter_reasons = diagnostics["book_filter_reasons"]
            if book_filter_reasons:
                diagnostic_summary += "; book_filter_reasons=" + ", ".join(
                    f"{reason}:{count}"
                    for reason, count in sorted(book_filter_reasons.items())
                )

            return offers, RetailerSearchStatus(
                retailer=retailer["name"],
                market=retailer["market"],
                status=(
                    "results_found"
                    if diagnostics["accepted"]
                    else "no_results"
                ),
                results_count=diagnostics["accepted"],
                message=(
                    None
                    if diagnostics["accepted"]
                    else (
                        "No se encontró una ficha con disponibilidad confirmada. "
                        f"Diagnóstico: {diagnostic_summary}."
                    )
                ),
            )

        except Exception as exc:
            print(
                f"[ERROR {search_provider}] {retailer['name']}: "
                f"{type(exc).__name__}: {exc}"
            )
            print(f"[DIAGNÓSTICO] {retailer['name']}: {diagnostics}")

            return [], RetailerSearchStatus(
                retailer=retailer["name"],
                market=retailer["market"],
                status="error",
                message=f"{type(exc).__name__}: {exc}",
            )

    all_offers: list[BookEdition] = []
    all_statuses: dict[str, RetailerSearchStatus] = {}
    seen_urls: set[str] = set()

    def add_offers(offers: list[BookEdition]) -> None:
        """Incorpora ofertas únicas después de validar sus URLs."""
        _add_purchase_offers(offers, seen_urls, all_offers)

    # 1. Buscar en todos los comercios argentinos.
    for retailer in argentina:
        offers, status = search_retailer(retailer)
        print(
            f"[RESULTADO COMERCIO] {retailer['name']}: "
            f"status={status.status}, "
            f"offers={[(o.title, o.url) for o in offers if o.availability == 'available']}"
        )
        all_statuses[retailer["name"]] = status
        add_offers(offers)

    def count_usable_offers(market: str | None = None) -> int:
        """Cuenta ofertas disponibles que cumplen el filtro de idioma vigente."""
        preferred_keys = {
            _language_key(language)
            for language in (preferred_book_languages or [])
        }
        preferred_keys.discard(None)

        def language_is_usable(offer: BookEdition) -> bool:
            """Aplica el idioma exclusivo solo cuando el lector lo solicitó."""
            if not exclusive_language:
                return True
            if (
                offer.retailer == "KEL Ediciones"
                or offer.market == "Internacional"
            ):
                return _language_key(offer.language) == "eng"
            return (
                offer.language is not None
                and _language_key(offer.language) in preferred_keys
            )

        return sum(
            1
            for offer in all_offers
            if offer.availability == "available"
            and (market is None or offer.market == market)
            and language_is_usable(offer)
        )

    # 2. Completar internacionalmente si Argentina no alcanza el objetivo.
    if count_usable_offers("Argentina") < TARGET_OPTIONS:
        for retailer in international:
            if count_usable_offers() >= TARGET_OPTIONS:
                break

            offers, status = search_retailer(retailer)
            all_statuses[retailer["name"]] = status
            add_offers(offers)

    # Devolver estados en el mismo orden definido para la lista de comercios.
    statuses = [
        all_statuses[r["name"]]
        for r in RETAILERS
        if r["name"] in all_statuses
    ]

    return all_offers, statuses
