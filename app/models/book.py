
"""Modelos de datos para candidatos, evaluaciones y recomendaciones de libros."""

from pydantic import BaseModel, Field
from typing import Literal

# Descubrimiento no conserva más candidatos que los que pueden pasar a evaluación. Este límite también lo valida el contenedor BookCandidates.
MAX_BOOK_CANDIDATES = 30


class BookCandidate(BaseModel):
    """Libro encontrado en el catálogo, antes de calcular su puntuación."""

    # Título, autor y motivo de inclusión identifican y explican el candidato.
    title: str = Field(description="Book title.")
    author: str = Field(description="Book author.")
    reason: str = Field(description="Why this book was discovered.")

    # Metadatos de catálogo opcionales: pueden faltar según la fuente consultada.
    published_date: int | None = Field(
        default=None,
        description="First publication year when known.",
    )
    languages: list[str] = Field(
        default_factory=list,
        description="Languages recorded by the source.",
    )
    subjects: list[str] = Field(
        default_factory=list,
        description="Book subjects or categories.",
    )
    # La cantidad de páginas ayuda a aplicar la preferencia de extensión.
    pages: int | None = Field(
        default=None,
        description="Page count for the returned edition.",
    )

    # Datos de una edición que ayudan a identificar o describir el libro.
    isbn: str | None = Field(
        default=None,
        description="ISBN-13 or ISBN-10 for the edition.",
    )
    publisher: str | None = Field(
        default=None,
        description="Publisher of the returned edition.",
    )
    description: str | None = Field(
        default=None,
        description="Description provided by the catalog.",
    )
    open_library_id: str | None = Field(
        default=None,
        description="Open Library work identifier, if available.",
    )


class BookCandidates(BaseModel):
    """Agrupa los candidatos hallados y valida su cantidad máxima."""

    books: list[BookCandidate] = Field(max_length=MAX_BOOK_CANDIDATES)


class ReferenceTitleTranslations(BaseModel):
    """Títulos originales y traducciones reconocidas de libros de referencia."""

    titles: list[str] = Field(default_factory=list)


class RankedBook(BaseModel):
    """Candidato evaluado, con puntuación y metadatos para ordenar resultados."""

    # Motivo breve y nota final, además de las notas parciales que la componen.
    title: str
    author: str
    reason: str
    match_score: int = Field(ge=0, le=100)
    genre_intent_score: int = Field(ge=0, le=30, default=0)
    themes_mood_score: int = Field(ge=0, le=30, default=0)
    literary_style_score: int = Field(ge=0, le=15, default=0)
    length_score: int = Field(ge=0, le=10, default=0)
    language_score: int = Field(ge=0, le=15, default=0)
    # Indica que se conserva como alternativa tentativa si no hubo coincidencias que alcanzaran el umbral normal de relevancia.
    is_provisional: bool = False

    # Se propagan los metadatos para no perderlos al avanzar al siguiente nodo.
    published_date: int | None = None
    languages: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    pages: int | None = None
    isbn: str | None = None
    publisher: str | None = None
    description: str | None = None
    open_library_id: str | None = None

class RankedBooks(BaseModel):
    """Contenedor del ranking de candidatos."""

    books: list[RankedBook]


class BookEdition(BaseModel):
    """Edición u oferta encontrada en un comercio o mercado concreto."""

    # Comercio y mercado permiten identificar de dónde proviene la oferta.
    retailer: str
    market: Literal["Argentina", "Internacional"]

    # Datos bibliográficos y enlace de la edición listada.
    title: str
    author: str | None = None
    url: str
    language: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    format: str | None = None
    pages: int | None = None

    # "unverified" evita afirmar disponibilidad cuando la evidencia no alcanza.
    availability: Literal[
        "available",
        "unavailable",
        "unverified",
    ] = "unverified"

    availability_evidence: str | None = None
    shipping_to_argentina: Literal[
        "confirmed",
        "not_confirmed",
        "not_offered",
        "not_applicable",
    ] = "not_confirmed"

    # Evidencia breve que respalda la disponibilidad detectada.
    source_excerpt: str | None = None


class RetailerSearchStatus(BaseModel):
    """Resultado de la búsqueda en un comercio, incluso si no hubo oferta."""

    retailer: str
    market: Literal["Argentina", "Internacional"]

    # Distingue resultados, ausencia de coincidencias, errores y comercios omitidos.
    status: Literal[
        "results_found",
        "no_results",
        "error",
         "not_searched",
    ]
    results_count: int = 0
    message: str | None = None


class BookCatalogEdition(BaseModel):
    """Edición bibliográfica del catálogo, separada de las ofertas comerciales."""

    title: str
    author: str | None = None
    language: list[str] = Field(default_factory=list)
    isbn: str | None = None
    publisher: str | None = None
    pages: int | None = None
    open_library_id: str
    open_library_edition_id: str | None = None
    source: Literal["open_library"] = "open_library"


class BookRecommendation(BaseModel):
    """Recomendación redactada para el usuario y enriquecida con opciones de edición."""

    title: str
    author: str
    open_library_id: str | None = None
    match_score: int = Field(ge=0, le=100)

    # Texto explicativo de la recomendación y advertencias posibles.
    introduction: str
    why_it_matches: str
    potential_drawbacks: str
    approximate_length: str
    language: str
    is_best_match: bool

    # Las ofertas comerciales y las ediciones de catálogo son fuentes distintas.
    purchase_options: list[BookEdition] = Field(
        default_factory=list
    )

    retailer_search_status: list[RetailerSearchStatus] = Field(
        default_factory=list
    )
    catalog_editions: list[BookCatalogEdition] = Field(
        default_factory=list
    )


class FinalRecommendations(BaseModel):
    """Objeto raíz de la respuesta final del agente."""

    recommendations: list[BookRecommendation]


class BookSearchFilters(BaseModel):
    """Criterios estructurados que recibe la búsqueda de catálogo."""

    # Preferencias que amplían o enfocan la búsqueda.
    subjects: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    content_type: str | None = None
    search_terms: list[str] = Field(default_factory=list)
    preference_terms: list[str] = Field(default_factory=list)
    # Títulos de referencia que no deben recomendarse como resultados nuevos.
    excluded_titles: list[str] = Field(default_factory=list)


class EvaluatedBook(BaseModel):
    """Resultado de evaluación de un candidato, con relevancia y notas parciales."""

    title: str
    author: str
    is_relevant: bool
    reason: str
    match_score: int = Field(ge=0, le=100)
    # Componentes que contribuyen a la puntuación final sobre 100.
    genre_intent_score: int = Field(ge=0, le=30)
    themes_mood_score: int = Field(ge=0, le=30)
    literary_style_score: int = Field(ge=0, le=15)
    length_score: int = Field(ge=0, le=10)
    language_score: int = Field(ge=0, le=15)


class EvaluatedBooks(BaseModel):
    """Contenedor de las evaluaciones calculadas para los candidatos."""

    books: list[EvaluatedBook]


class BookResearch(BaseModel):
    """Información investigada sobre un libro y las fuentes consultadas."""

    title: str
    author: str
    synopsis: str
    # Estos rasgos se usan para explicar y contrastar la afinidad literaria.
    themes: list[str] = Field(default_factory=list)
    tone: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)
    critical_reception: str | None = None
    sources: list[str] = Field(default_factory=list)


class BookResearchResults(BaseModel):
    """Contenedor de resultados de investigación de varios libros."""

    books: list[BookResearch]


class RankedEvaluationItem(BaseModel):
    """Evaluación resumida con la decisión de relevancia y su nota final."""

    title: str
    author: str
    is_relevant: bool
    reason: str
    match_score: int = Field(ge=0, le=100)


class RankedEvaluationResults(BaseModel):
    """Contenedor de evaluaciones resumidas."""

    books: list[RankedEvaluationItem]


class LiteraryEvaluationItem(BaseModel):
    """Salida literaria estructurada antes de sumar idioma y extensión."""

    # candidate_id permite vincular la respuesta del modelo con el candidato exacto.
    candidate_id: str
    title: str
    author: str
    # El modelo puntúa afinidad literaria; otros componentes se calculan aparte.
    genre_intent_score: int = Field(ge=0, le=30)
    themes_mood_score: int = Field(ge=0, le=30)
    literary_style_score: int = Field(ge=0, le=15)
    reason: str


class LiteraryEvaluationResults(BaseModel):
    """Objeto raíz exigido para la respuesta estructurada de evaluación."""

    books: list[LiteraryEvaluationItem]
