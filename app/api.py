"""Expone el flujo de recomendaciones de libros mediante una API FastAPI.
Valida la solicitud HTTP, la convierte al estado inicial del grafo y devuelve
la salida tipada. También puede servir el frontend compilado si está disponible.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from app.graph.state import create_initial_state
from app.models.book import FinalRecommendations
from app.models.reader import ReaderPreferenceOverrides


logger = logging.getLogger(__name__)


class RecommendationRequest(BaseModel):
    """Esquema de entrada y validaciones propias del endpoint de recomendaciones."""

    # Los límites rechazan solicitudes vacías o excesivamente extensas antes de iniciar el grafo y consumir llamadas a los proveedores.
    user_input: str = Field(min_length=3, max_length=4000)
    preferences: ReaderPreferenceOverrides | None = None

    @field_validator("user_input")
    @classmethod
    def validate_user_input(cls, value: str) -> str:
        """Quita espacios exteriores y evita aceptar una descripción vacía."""
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Describí qué te gustaría leer.")
        return value

    @model_validator(mode="after")
    def validate_language_preferences(self):
        """Mantiene coherentes los idiomas indicados y su nivel de restricción.
        Una preferencia `preferred` o `exclusive` necesita al menos un idioma;
        una lista sin restricción explícita se interpreta como preferida.
        """
        preferences = self.preferences
        if preferences is None:
            return self

        languages = preferences.preferred_book_languages or []
        constraint = preferences.book_language_constraint
        if constraint in {"preferred", "exclusive"} and not languages:
            raise ValueError(
                "Elegí al menos un idioma para priorizarlo o limitar la búsqueda."
            )
        if languages and constraint == "none":
            raise ValueError(
                "No se pueden elegir idiomas si no hay una preferencia de idioma."
            )
        if languages and constraint is None:
            preferences.book_language_constraint = "preferred"
        return self


@lru_cache(maxsize=1)
def get_graph():
    """Construye el grafo una sola vez y reutiliza su instancia en las peticiones."""
    from app.graph.graph import build_graph

    # Importar dentro de la función posterga la inicialización de dependencias hasta que se solicite una recomendación, no al importar este módulo.
    return build_graph()


# La metadata describe el servicio en OpenAPI y en la documentación de FastAPI.
app = FastAPI(
    title="Book Agent",
    description="API local para generar recomendaciones de libros.",
)
# El frontend local puede llamar a la API desde el servidor de desarrollo de
# Vite; no se habilitan credenciales ni orígenes ajenos a esos hosts.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    """Comprueba que el proceso HTTP está respondiendo, sin ejecutar el grafo."""
    return {"status": "ok"}


@app.post("/api/recommendations", response_model=FinalRecommendations)
def create_recommendations(
    request: RecommendationRequest,
) -> FinalRecommendations:
    """Ejecuta el grafo con el texto y las preferencias de la solicitud.
    La salida del grafo se valida contra el modelo público de recomendaciones.
    Los errores internos quedan registrados en el servidor y se exponen como
    una respuesta 502 sin filtrar detalles del proveedor al cliente.
    """
    try:
        # Convertir la entrada HTTP al estado que espera LangGraph y ejecutar el grafo sin exponer sus nodos ni su estructura interna en la API.
        result = get_graph().invoke(
            create_initial_state(
                request.user_input,
                reader_preferences=request.preferences,
            )
        )
        # Validar la salida evita responder con un objeto parcial o mal formado.
        return FinalRecommendations.model_validate(
            result.get("final_recommendations")
        )
    except Exception as exc:
        # El traceback completo queda en los logs locales; la respuesta HTTP mantiene un mensaje estable y evita filtrar credenciales o datos internos.
        logger.exception("No se pudieron generar las recomendaciones.")
        raise HTTPException(
            status_code=502,
            detail=(
                "No se pudieron generar las recomendaciones. "
                "Revisá los logs del servidor para ver el error del proveedor."
            ),
        ) from exc


# El montaje es condicional para permitir ejecutar solo la API durante el desarrollo o cuando todavía no se generó la carpeta del frontend.
frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=frontend_dist, html=True),
        name="frontend",
    )
