"""Servidor MCP que expone la búsqueda de disponibilidad comercial de libros."""

from __future__ import annotations

import os
import sys
from contextlib import redirect_stdout

from mcp.server import MCPServer

from app.models.book import BookEdition
from app.tools.edition_search import search_book_purchase_options
from app.tools.exa_search import ExaSearchClient
from app.tools.tavily_search import TavilySearchClient


# El nombre identifica este servidor cuando se comunica con el cliente MCP.
server = MCPServer("book-agent-availability")


def _create_search_client() -> ExaSearchClient | TavilySearchClient | None:
    """Elige Exa primero y usa Tavily si Exa no está configurado."""
    exa_api_key = os.getenv("EXA_API_KEY", "").strip()
    if exa_api_key:
        return ExaSearchClient(api_key=exa_api_key)

    tavily_api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if tavily_api_key:
        return TavilySearchClient(api_key=tavily_api_key)

    return None


# El decorador registra esta función como una herramienta que el cliente MCP puede invocar por su nombre y con los argumentos declarados abajo. title y author identifican el libro; los demás argumentos refinan mercados, idiomas, títulos alternativos y si el idioma debe aplicarse estrictamente.
@server.tool()
def search_book_availability(
    title: str,
    author: str,
    markets_to_search: list[str] | None = None,
    preferred_book_languages: list[str] | None = None,
    alternate_titles: list[str] | None = None,
    english_titles: list[str] | None = None,
    exclusive_language: bool = False,
) -> dict:
    """Busca ofertas comerciales y devuelve también el estado de cada tienda."""
    # El proceso MCP recibe ambas claves y usa Exa cuando está configurada;
    # Tavily queda como alternativa si no hay una clave de Exa.
    search_client = _create_search_client()

    # stdout transporta los mensajes del protocolo MCP por stdio. Los mensajes de diagnóstico del buscador se envían a stderr para no mezclarlos con esas tramas.
    with redirect_stdout(sys.stderr):
        # La búsqueda de tiendas contiene las reglas de consulta y validación; este servidor solo adapta sus argumentos y publica el resultado.
        offers, statuses = search_book_purchase_options(
            title=title,
            author=author,
            search_client=search_client,
            markets_to_search=markets_to_search,
            preferred_book_languages=preferred_book_languages,
            alternate_titles=alternate_titles,
            english_titles=english_titles,
            exclusive_language=exclusive_language,
        )
    # Convierte los modelos Pydantic en diccionarios simples que MCP puede serializar; también valida los elementos que ya lleguen como diccionario.
    return {
        "offers": [
            offer.model_dump()
            if isinstance(offer, BookEdition)
            else BookEdition.model_validate(offer).model_dump()
            for offer in offers
        ],
        "statuses": [status.model_dump() for status in statuses],
    }


if __name__ == "__main__":
    # Al ejecutarse como módulo, mantiene activo el servidor MCP por stdio.
    server.run()
