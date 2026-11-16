"""Investiga contexto editorial y recepción crítica de una obra con Tavily.
Esta búsqueda complementa el catálogo y las ofertas: consulta dominios
preseleccionados y devuelve evidencia textual para la investigación del libro.
"""

import os

from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

# Restringir la investigación a editoriales, crítica y obras de referencia; no es la lista de tiendas usada para comprobar disponibilidad comercial.
TRUSTED_DOMAINS = [
    "britannica.com",
    "nytimes.com",
    "theguardian.com",
    "lrb.co.uk",
    "newyorker.com",
    "theatlantic.com",
    "publishersweekly.com",
    "kirkusreviews.com",
    "penguinrandomhouse.com",
    "penguin.co.uk",
    "macmillan.com",
    "harpercollins.com",
]


def research_book(title: str, author: str) -> str:
    """Busca sinopsis, temas, estilo y recepción en fuentes seleccionadas.
    La salida se aplana a texto con título y URL para que el agente pueda citar
    y distinguir cada fragmento que recibió del proveedor.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY no est? configurada.")

    client = TavilyClient(api_key=api_key)
    query = (
        # Combinar la identidad del libro con los temas de investigación deseados.
        f'"{title}" "{author}" '
        "synopsis themes writing style literary analysis "
        "critical reception"
    )
    # La consulta combina identidad bibliográfica con los campos que debe cubrir la investigación; limitar resultados acota texto y latencia.
    response = client.search(
        query,
        search_depth="basic",
        max_results=3,
        include_domains=TRUSTED_DOMAINS,
        include_raw_content="text",
    )

    # Aplanar los resultados en un bloque legible para el contexto del agente. El contenido completo se prefiere al snippet, que queda como respaldo.
    return "\n\n".join(
        f"Title: {result.get('title') or 'Unknown'}\n"
        f"URL: {result.get('url')}\n"
        f"Content: {result.get('raw_content') or result.get('content') or ''}"
        for result in response.get("results", [])
    )

