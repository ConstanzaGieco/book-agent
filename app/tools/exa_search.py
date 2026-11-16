"""Adaptador fino que expone Exa con la interfaz de búsqueda del proyecto.
Así, `edition_search` puede usar la misma llamada independientemente de que
el proveedor activo sea Exa o Tavily.
"""

from __future__ import annotations

from typing import Any


class ExaSearchClient:
    """Envuelve Exa sin alterar la forma de su respuesta.
    El validador comercial ya conoce la estructura del SDK de Exa, por lo que
    este adaptador se limita a construir el cliente y reenviar la consulta.
    """

    provider_name = "EXA"

    def __init__(self, api_key: str):
        """Crea el cliente de Exa solo cuando se seleccionó ese proveedor.
        El import local evita exigir el SDK de Exa al iniciar el proceso si la
        aplicación está usando únicamente Tavily.
        """
        from exa_py import Exa
        self.client = Exa(api_key=api_key)

    def search_and_contents(self, query: str, **kwargs: Any) -> Any:
        """Reenvía texto y opciones, conservando la respuesta original del SDK.
        `kwargs` permite pasar opciones específicas de contenido sin duplicar
        aquí la firma completa de Exa.
        """
        return self.client.search_and_contents(query, **kwargs)
