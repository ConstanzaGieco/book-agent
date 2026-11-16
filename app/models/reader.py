"""Modelos para preferencias explícitas y contexto de lectura."""

from pydantic import BaseModel, Field
from typing import Literal


# Valores normalizados que puede recibir el formulario de preferencias.
GenrePreference = Literal[
    "mystery",
    "detective fiction",
    "thriller",
    "science fiction",
    "fantasy",
    "horror",
    "romance",
    "historical fiction",
    "adventure",
    "literary fiction",
    "humor",
]

# Códigos ISO de idioma permitidos en preferencias explícitas.
BookLanguageCode = Literal[
    "spa",
    "eng",
    "por",
    "fra",
    "ita",
    "deu",
    "jpn",
]

# Tipos de libro aceptados como selección estructurada.
BookContentType = Literal[
    "novel",
    "short story collection",
    "essay",
    "poetry",
    "graphic novel",
]

# Categorías usadas para traducir una preferencia de extensión en una puntuación.
LengthCategory = Literal["short", "medium", "long", "any"]

# Determina si el idioma de los libros es libre, preferido o obligatorio.
BookLanguageConstraint = Literal["none", "preferred", "exclusive"]


class ReaderPreferenceOverrides(BaseModel):
    """Preferencias opcionales enviadas explícitamente desde la interfaz."""

    # None significa que el usuario no modificó esa preferencia; una lista vacía expresa una selección explícitamente vacía.
    preferred_genres: list[GenrePreference] | None = None
    excluded_genres: list[GenrePreference] | None = None
    preferred_book_languages: list[BookLanguageCode] | None = None
    book_language_constraint: BookLanguageConstraint | None = None
    preferred_length_category: LengthCategory | None = None
    preferred_content_type: BookContentType | None = None


class ReaderContext(BaseModel):
    """Contexto unificado que los nodos usan durante una solicitud."""

    # Información general del lector frente a lo que busca en esta ocasión.
    reader_profile: str | None = Field(
        default=None,
        description="The reader's general reading profile, habits, tastes, and preferences."
    )

    reading_intent: str | None = Field(
        default=None,
        description="What the reader is currently looking for in their next reading."
    )

    current_mood: str | None = Field(
        default=None,
        description="The reader's current mood or emotional state relevant to this reading session."
    )

    # Géneros inferidos o indicados y los que deben evitarse.
    preferred_genres: list[str] = Field(
        default_factory=list,
        description="Genres or literary categories the reader prefers."
    )

    excluded_genres: list[str] = Field(
        default_factory=list,
        description="Genres or literary categories the reader wants to avoid."
    )

    # Idioma de la conversación, distinto del idioma que se desea para el libro.
    conversation_language: str = Field(
        description="The language the user is currently communicating in."
    )
    
    # Datos de una obra solicitada directamente, si el pedido tiene un título concreto.
    requested_book_title: str | None = Field(
        default=None,
        description="The exact title of a specific book the user explicitly requests."
    )

    requested_book_author: str | None = Field(
        default=None,
        description="The author of the specifically requested book, if known."
    )

    requested_book_language: str | None = Field(
        default=None,
        description="The explicitly requested language of the specific book edition, if any."
    )

    # Idiomas aceptables para las recomendaciones y regla para aplicarlos.
    preferred_book_languages: list[str] = Field(
        default_factory=list,
        description=(
            "Languages in which the reader wants or accepts books. "
            "Always include the conversation language and every additional "
            "language explicitly requested or accepted by the reader. "
            "English may be used as a fallback only when no suitable "
            "recommendations are available in the conversation language "
            "or in the explicitly requested languages."
        )
    )

    # La descripción libre se conserva junto con la categoría normalizada usada al puntuar.
    preferred_length: str | None = Field(
        default=None,
        description="Preferred approximate book length, such as short, medium, or long."
    )

    preferred_length_category: LengthCategory = "any"

    age: int | None = Field(
        default=None,
        description="The reader's age, if explicitly stated."
    )

    # Referencias positivas o negativas que ayudan a orientar la búsqueda y evaluación.
    reference_books: list[str] = Field(
        default_factory=list,
        description="Books mentioned by the reader as references, especially books they liked or disliked."
    )

    preferred_authors: list[str] = Field(
        default_factory=list,
        description="Authors the reader explicitly prefers."
    )

    # Temas que deben excluirse y otras preferencias que no encajan en campos específicos.
    excluded_topics: list[str] = Field(
        default_factory=list,
        description="Topics, themes, or content the reader explicitly wants to avoid."
    )

    other_preferences: list[str] = Field(
        default_factory=list,
        description="Other relevant preferences that do not fit the previous fields."
    )
    
    # Tipo de libro en texto abierto para conservar categorías inferidas no enumeradas.
    preferred_content_type: str | None = Field(
        default=None,
        description=(
            "The type or format of book the reader prefers, "
            "expressed independently of the language used. "
            "Examples include novel, short story collection, "
            "essay, poetry, or graphic novel."
        ),
    )

    # Distingue una preferencia flexible de un requisito estricto de idioma.
    book_language_constraint: BookLanguageConstraint = Field(
        default="none",
        description=(
            "Whether book languages are unrestricted, a soft preference, "
            "or a strict constraint. Infer this semantically from the "
            "reader's message, regardless of its language."
        ),
    )

    # Se aplican como exclusiones durante el filtrado de candidatos.
    excluded_authors: list[str] = Field(
        default_factory=list,
        description=(
            "Authors the reader explicitly does not want to read, "
            "including authors mentioned only as style references "
            "when the reader rejects reading their works."
        ),
    )
