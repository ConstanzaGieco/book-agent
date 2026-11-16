"""Adapta resultados de Tavily al contrato que consume `edition_search`.

El SDK devuelve diccionarios; las clases de respuesta de este módulo ofrecen
los atributos uniformes que el validador usa para recorrer cada resultado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tavily import TavilyClient


@dataclass
class SearchResult:
    """Campos del resultado que necesita el validador de ofertas."""
    title: str | None
    url: str | None
    text: str | None


@dataclass
class SearchResponse:
    """Contenedor de resultados compatible con la respuesta de Exa."""
    results: list[SearchResult]


class TavilySearchClient:
    """Traduce opciones y resultados de Tavily a la interfaz común del agente."""

    provider_name = "TAVILY"

    def __init__(self, api_key: str):
        """Crea la conexión usando la clave de API seleccionada por la app."""
        self.client = TavilyClient(api_key=api_key)

    def search_and_contents(
        self,
        query: str,
        *,
        num_results: int = 5,
        include_domains: list[str] | None = None,
        text: dict[str, Any] | None = None,
        **_: Any,
    ) -> SearchResponse:
        """Ejecuta búsqueda básica y adapta sus resultados.

        El número solicitado se restringe al rango aceptado por el servicio;
        `max_characters` limita el texto que pasa a validación y reduce el
        volumen de contenido procesado por cada resultado.
        """
        max_characters = int((text or {}).get("max_characters", 5000))
        # Se conserva `include_domains` como filtro del proveedor; una lista vacía significa que Tavily puede consultar cualquier dominio.
        response = self.client.search(
            query,
            search_depth="basic",
            max_results=min(max(num_results, 1), 20),
            include_domains=include_domains or [],
            include_raw_content="text",
        )
        # El validador espera siempre title/url/text; Tavily llama `content` al fragmento y puede incluir además `raw_content`.
        results = []
        for result in response.get("results", []):
            # Preferir contenido completo y usar el snippet como respaldo.
            content = result.get("raw_content") or result.get("content") or ""
            results.append(SearchResult(
                title=result.get("title"),
                url=result.get("url"),
                text=content[:max_characters],
            ))
        return SearchResponse(results=results)
