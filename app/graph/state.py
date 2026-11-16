"""Define el estado compartido y su contenido inicial para el grafo."""

from typing import NotRequired, TypedDict

from app.models.reader import ReaderContext, ReaderPreferenceOverrides
from app.models.book import (
    BookCandidates,
    RankedBooks,
    FinalRecommendations,
    BookSearchFilters,
    EvaluatedBooks,
    BookResearch,
)
from app.models.work_identity import WorkIdentityResults


class AgentState(TypedDict):
    """Esquema de los datos compartidos por todos los nodos del grafo.

    Las claves declaradas normalmente están presentes en cada estado.
    Las marcadas con NotRequired se agregan cuando un nodo las produce.
    """

    user_input: str  # Mensaje original escrito por el lector.
    reader_context: ReaderContext | None  # Preferencias extraídas; None antes del primer nodo.

    book_candidates: BookCandidates  # Libros encontrados en las fuentes de catálogo.
    ranked_books: RankedBooks  # Candidatos evaluados y ordenados por afinidad.
    final_recommendations: FinalRecommendations  # Resultado redactado para el usuario.
    book_search_filters: BookSearchFilters | None  # Filtros; None antes de prepararlos.
    evaluated_books: EvaluatedBooks  # Puntajes y razones asignados a los candidatos.

    searched_titles: list[str]  # Títulos que el flujo puede registrar como ya buscados.
    discovery_round: int  # Número de ronda de descubrimiento de candidatos.
    book_research: list[BookResearch]  # Investigación resumida y estructurada por libro.
    retrieved_book_research: list[dict]  # Evidencia original recuperada o consultada.

    # Identidades de obras y referencias para evitar repetir títulos o recomendar como novedad un libro que el lector ya mencionó como referencia.
    work_identities: WorkIdentityResults  # Relación entre candidatos y obras.
    reference_candidate_ids: list[str]  # IDs de candidatos usados como referencias.
    reference_work_groups: list[str]  # Grupos de obra que corresponden a referencias.

    # Estos campos son opcionales: se agregan cuando un nodo puede verificarlos o cuando la API recibe preferencias explícitas desde la interfaz.
    candidate_catalog_editions: NotRequired[dict[str, list[dict]]]  # Ediciones por libro.
    candidate_language_verification: NotRequired[dict[str, str]]  # Resultado de verificación.
    reader_preferences: NotRequired[ReaderPreferenceOverrides | None]  # Preferencias de la interfaz.


def create_initial_state(
    user_input: str,
    reader_preferences: ReaderPreferenceOverrides | None = None,
) -> AgentState:
    """Crea el estado inicial antes de ejecutar el primer nodo."""
    # Las colecciones comienzan vacías y los datos que aún no se calcularon se inicializan como None o con modelos vacíos, no con valores inventados.
    return {
        "user_input": user_input,
        "reader_context": None,
        "book_candidates": BookCandidates(books=[]),
        "ranked_books": RankedBooks(books=[]),
        "final_recommendations": FinalRecommendations(recommendations=[]),
        "book_search_filters": None,
        "evaluated_books": EvaluatedBooks(books=[]),
        "searched_titles": [],
        "discovery_round": 0,
        "book_research": [],
        "retrieved_book_research": [],
        "work_identities": WorkIdentityResults(works=[]),
        "reference_candidate_ids": [],
        "reference_work_groups": [],
        "reader_preferences": reader_preferences,
    }