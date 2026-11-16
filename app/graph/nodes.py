"""Nodos que interpretan la solicitud y generan recomendaciones de libros."""

import json
from app.graph.state import AgentState
from app.llm import (
    create_chat_model,
    invoke_with_rate_limit_retry as _invoke_llm_with_rate_limit_retry,
)
from app.models.reader import ReaderContext
from app.models.book import (
    BookCandidate,
    BookCandidates,
    MAX_BOOK_CANDIDATES,
    RankedBook,
    RankedBooks,
    FinalRecommendations,
    BookSearchFilters,
    EvaluatedBook,
    EvaluatedBooks,
    BookResearch,
    BookResearchResults,
    RankedEvaluationResults,
    LiteraryEvaluationResults,
    BookCatalogEdition,
)
from app.models.work_identity import WorkIdentityResults, WorkIdentity, ReferenceTitleTranslations
from app.tools.book_search import (
    search_books,
    get_open_library_work,
    search_open_library_editions,
    classify_content_type,
    classify_requested_content_type,
)
from app.tools.web_research import research_book
from app.tools.book_vector_store import store_book_research, get_cached_book_research
import re
import unicodedata
import pycountry

# Los nodos reciben la misma interfaz LangChain; app.llm selecciona el proveedor.
llm = create_chat_model("general")
work_identity_base_llm = create_chat_model(
    "work_identity",
    max_tokens=400,
)

# Cada runnable solicita al modelo una respuesta validada con el esquema indicado.
structured_llm = llm.with_structured_output(ReaderContext)
reference_titles_llm = work_identity_base_llm.with_structured_output(ReferenceTitleTranslations, method="json_schema")
work_identity_llm = work_identity_base_llm.with_structured_output(WorkIdentityResults, method="json_schema")
recommendation_structured_llm = llm.with_structured_output(FinalRecommendations, method="json_schema")
book_research_llm = llm.with_structured_output(BookResearchResults, method="json_schema")
evaluation_ranking_llm = llm.with_structured_output(LiteraryEvaluationResults, method="json_schema")

def normalize_text(value: str | None) -> str:
    """Normaliza texto para comparar títulos, autores y preferencias."""
    # casefold compara sin distinguir mayúsculas; NFKD separa letras y tildes.
    value = unicodedata.normalize("NFKD", (value or "").casefold())
    # Quita las marcas diacríticas para que, por ejemplo, "ó" y "o" coincidan.
    value = "".join(
        char for char in value
        if not unicodedata.combining(char)
    )
    # Conserva palabras y números, elimina puntuación y compacta espacios.
    return " ".join(re.findall(r"\w+", value))


def normalize_language(value: str | None) -> str:
    """Normaliza idiomas conocidos a códigos ISO de tres letras."""
    # Un idioma ausente o vacío no puede coincidir con una preferencia.
    if not isinstance(value, str) or not value.strip():
        return ""

    raw_value = value.strip()
    normalized_value = raw_value.casefold()

    # Primero intenta reconocer códigos ISO de dos o tres letras.
    if len(normalized_value) == 2:
        language = pycountry.languages.get(alpha_2=normalized_value)
        if language:
            return getattr(language, "alpha_3", normalized_value)

    if len(normalized_value) == 3:
        language = (
            pycountry.languages.get(alpha_3=normalized_value)
            or pycountry.languages.get(bibliographic=normalized_value)
        )
        if language:
            return getattr(language, "alpha_3", normalized_value)

    # Si no parece un código, busca el nombre del idioma en pycountry.
    try:
        language = pycountry.languages.lookup(raw_value)
        return getattr(language, "alpha_3", normalized_value)
    except LookupError:
        pass

    # Algunos nombres frecuentes no están reconocidos por pycountry; este mapa cubre esos alias y deja normalizado cualquier otro texto.
    aliases = {
        "espanol": "spa",
        "castellano": "spa",
        "ingles": "eng",
        "english": "eng",
        "espanol latinoamericano": "spa",
    }

    return aliases.get(normalize_text(raw_value), normalize_text(raw_value))


def _invoke_work_identity_with_retry(prompt: str):
    """Usa la política común de reintentos para el modelo de identidad."""
    return _invoke_llm_with_rate_limit_retry(
        work_identity_llm,
        prompt,
        "resolver identidades",
    )


def calculate_length_score(
    context: ReaderContext,
    pages: int | None,
) -> int:
    """Puntúa cuánto coincide la cantidad de páginas con la preferencia."""
    category = context.preferred_length_category or "any"

    # Sin páginas comprobables o sin preferencia, se asigna un valor neutral.
    if pages is None or pages <= 0 or category == "any":
        return 5

    # Cada categoría tiene un rango ideal y rangos cercanos con puntaje menor.
    if category == "short":
        # Para libros cortos, hasta 200 páginas es el ajuste ideal; hasta 300 se acepta parcialmente y el resto recibe poco puntaje.
        if pages <= 200:
            return 10
        if pages <= 300:
            return 6
        return 2

    if category == "medium":
        # El rango ideal es 150-350 páginas; el rango cercano es 100-400.
        if 150 <= pages <= 350:
            return 10
        if 100 <= pages <= 400:
            return 6
        return 2

    if category == "long":
        # Más de 400 páginas es ideal para una preferencia de libros largos.
        if pages > 400:
            return 10
        if pages >= 250:
            return 6
        return 2

    return 5


def calculate_language_score(context: ReaderContext, languages: list[str] | None) -> int:
    """Puntúa los idiomas registrados para el candidato frente a los preferidos."""
    # En modo exclusivo solo se comparan los idiomas permitidos.
    if context.book_language_constraint == "exclusive":
        languages_to_consider = context.preferred_book_languages or []
    else:
        languages_to_consider = (
            (context.preferred_book_languages or [])
            + [context.conversation_language]
        )

    # En modo flexible también se considera el idioma de la conversación.
    # Normaliza y deduplica para comparar códigos y nombres equivalentes.
    preferred = list(dict.fromkeys(
        normalize_language(language)
        for language in languages_to_consider
        if isinstance(language, str)
        and language.strip()
        and normalize_language(language)
    ))

    # El catálogo puede devolver idiomas como nombres o códigos distintos.
    candidate_languages = {
        normalize_language(language)
        for language in (languages or [])
        if isinstance(language, str) and language.strip()
    }

    # La falta de metadatos no penaliza al candidato.
    if not preferred or not candidate_languages:
        return 7

    # Coincidencias más tempranas siguen el orden de prioridad del lector.
    for index, language in enumerate(preferred):
        if language in candidate_languages:
            # La primera preferencia recibe 15 puntos; el valor baja según su posición para mantener el orden elegido por el lector.
            return max(
                1,
                round(15 * (len(preferred) - index) / len(preferred)),
            )

    # Sin una coincidencia, se conserva el puntaje neutral.
    return 7

def author_is_excluded(
    author: str | None,
    excluded_authors: list[str] | None,
) -> bool:
    """Comprueba si el autor coincide con alguno de los autores excluidos."""
    author_key = normalize_text(author)

    # Si no hay autor identificable, no se puede afirmar que esté excluido.
    if not author_key:
        return False

    # La comparación es exacta tras normalizar, no una búsqueda parcial del nombre de un autor dentro de otro.
    return any(
        author_key == normalize_text(excluded)
        for excluded in (excluded_authors or [])
        if normalize_text(excluded)
    )


def matches_language_constraint(languages: list[str] | None, context: ReaderContext) -> bool:
    """Deja la decisión estricta de idioma para la verificación por edición."""
    # Los idiomas registrados a nivel de obra no permiten confirmar ni descartar la existencia de ediciones en otros idiomas. La verificación debe hacerse con datos de ediciones concretas.
    return True

def collect_reader_context(state: AgentState) -> AgentState:
    """Extrae del mensaje las preferencias y datos relevantes del lector."""
    # El esquema estructurado obliga al modelo a devolver los campos de ReaderContext.
    prompt = f"""
        Analyze the following reader message and extract all relevant information
        for personalized book recommendations.

        Reader message:
        {state["user_input"]}

        Extract:
        - the language the user is communicating in
        - the reader's age, only if explicitly stated
        - their general reader profile
        - what they currently want to read
        - their current mood
        - preferred genres
        - excluded genres
        - preferred book languages
        - preferred approximate book length
        - the normalized preferred length category
        - reference books they mention
        - preferred authors
        - topics or themes they want to avoid
        - any other relevant reading preferences
        - preferred book content type or format, if explicitly stated or strongly implied
        
        Specific book request rules:
        - requested_book_title must contain the title only when the user
          explicitly asks to find, obtain, read, or receive a specific book.
        - Do not treat books mentioned only as references, favorites,
          dislikes, or comparisons as requested books.
        - requested_book_author must contain the author only when explicitly
          stated or unambiguously identifiable.
        - requested_book_language must contain the language of the requested
          edition only when the user explicitly specifies it.
        - If no specific book is requested, return null for all three fields.
        - A requested book language is independent of conversation_language.
        - Include requested_book_language in preferred_book_languages,
          preserving the language priority rules already defined below.

        Language and author constraints:
        - Extract the reader's book-language preferences from the meaning
        of the complete message, regardless of the language used.
        - If no book-language preference is expressed, set
        book_language_constraint to "none" and leave
        preferred_book_languages empty.
        - Do not add English automatically during context extraction.
        - The conversation language and English fallback are handled
        later by the recommendation pipeline, not inferred as user preferences.
        - If a language is preferred but alternatives remain acceptable,
        set book_language_constraint to "preferred".
        - If the reader restricts acceptable books to specified languages,
        set book_language_constraint to "exclusive".
        - In an exclusive constraint, include only the accepted languages
        in preferred_book_languages.
        - Identify authors explicitly excluded by the reader, including
        authors used as style references whose works the reader rejects.
        - Do not classify a style reference as a preferred author when
        the reader explicitly says they do not want to read that author.
        - Preserve the distinction between preferred_authors,
        excluded_authors and requested_book_author.
        - Do not infer language or author constraints that the reader
        has not expressed.

        Length rules:
        - Extract preferred_length in natural language, preserving the meaning
          and strength of the user's preference.
        - Distinguish between a strict length constraint and a softer preference
          for avoiding long books.
        - Do not convert a soft preference into a strict short-length requirement.
        - Normalize preferred_length_category using only:
          "short": explicitly prefers short books or imposes a strict short-length constraint.
          "medium": prefers moderate length or wants to avoid long books without requiring a short book.
          "long": explicitly prefers long books.
          "any": no clear preference about length.
        - Determine the category from meaning, regardless of the language used.

        Other rules:
        - Only extract information explicitly stated or strongly implied.
        - Do not invent preferences or infer the reader's age.
        - If a scalar field is not mentioned, return null, except fields with
          explicit defaults or constraints in the output schema.
        - If a list field is not mentioned, return an empty list.
        - Never return null for list fields.
        - A reference book can be a book the reader liked, disliked, or used as
          a comparison. Preserve that context when possible.
        - Do not recommend books yet. Only build the reader context.
        - Identify preferred book content type from meaning, regardless of language.
        - Return null if no content type preference is stated or strongly implied.
        - Do not restrict the result to a predefined list of formats.
    """

    # Interpreta el pedido, pero todavía no genera ni selecciona recomendaciones.
    reader_context = _invoke_llm_with_rate_limit_retry(
        structured_llm, prompt, "interpretar el pedido"
    )

    # Las preferencias elegidas en la interfaz prevalecen sobre las inferidas en el mensaje; los campos que no se enviaron conservan su valor inferido.
    preference_overrides = state.get("reader_preferences")
    if preference_overrides is not None:
        updates = preference_overrides.model_dump(exclude_none=True)
        # Convierte la categoría seleccionada en una indicación legible que también pueda usar el resto del flujo de recomendaciones.
        if "preferred_length_category" in updates:
            length_descriptions = {
                "short": "corto, idealmente hasta 200 páginas",
                "medium": "mediano, idealmente entre 150 y 350 páginas",
                "long": "largo, idealmente más de 400 páginas",
                "any": None,
            }
            updates["preferred_length"] = length_descriptions[
                updates["preferred_length_category"]
            ]
        if updates:
            reader_context = reader_context.model_copy(update=updates)

    # Devuelve una actualización parcial del estado con el contexto ya validado.
    return {
        **state,
        "reader_context": reader_context,
    }

def enrich_ranked_book_languages(state: AgentState) -> dict:
    """Verifica idiomas por edición y aplica el filtro exclusivo si corresponde."""
    # Copia las estructuras mutables para actualizar el estado sin modificar directamente las instancias recibidas de los nodos anteriores.
    context = state["reader_context"]
    ranked_books = list(state["ranked_books"].books)
    catalog_cache = dict(state.get("candidate_catalog_editions", {}))
    verification = dict(state.get("candidate_language_verification", {}))
    exclusive = context.book_language_constraint == "exclusive"

    # Usa una clave estable para asociar libros con sus ediciones verificadas.
    def book_key(book) -> str:
        return f"{normalize_text(book.title)}|{normalize_text(book.author)}"

    requested_title = normalize_text(context.requested_book_title)
    requested_author = normalize_text(context.requested_book_author)

    # La solicitud explícita se conserva con prioridad aunque su puntuación no alcance los umbrales generales.
    def is_requested(book) -> bool:
        return bool(requested_title) and (
            normalize_text(book.title) == requested_title
            and (
                not requested_author
                or normalize_text(book.author) == requested_author
            )
        )

    # Define los idiomas que se consultarán en el catálogo, en orden: el idioma de conversación solo se agrega cuando la restricción no es exclusiva.
    primary_languages = (
        list(dict.fromkeys(context.preferred_book_languages or []))
        if exclusive
        else list(dict.fromkeys(
            ([context.conversation_language] if context.conversation_language else [])
            + (context.preferred_book_languages or [])
        ))
    )

    requested_codes = {
        normalize_language(language)
        for language in primary_languages
        if normalize_language(language)
    }

    # Limita consultas externas por ejecución para acotar latencia y uso de API.
    updated_books = []
    lookups_used = 0
    max_language_lookups = 12

    for book in ranked_books:
        key = book_key(book)

        # Si ya hay datos guardados, reutiliza la verificación anterior.
        if key in catalog_cache:
            verification.setdefault(key, "verified")

        # Si no hay caché, consulta ediciones solo cuando hay ID, cupo y lenguas.
        elif (
            book.open_library_id
            and lookups_used < max_language_lookups
            and primary_languages
        ):
            lookups_used += 1

            try:
                editions = search_open_library_editions(
                    title=book.title,
                    author=book.author,
                    preferred_languages=primary_languages,
                    max_results=10,
                    open_library_id=book.open_library_id,
                )

                # El fallback a inglés solo está permitido fuera del modo exclusivo.
                if (
                    not editions
                    and not exclusive
                    and "eng" not in requested_codes
                ):
                    editions = search_open_library_editions(
                        title=book.title,
                        author=book.author,
                        preferred_languages=["English"],
                        max_results=10,
                        open_library_id=book.open_library_id,
                    )

                catalog_cache[key] = [
                    BookCatalogEdition.model_validate(item).model_dump()
                    for item in editions
                ]

                # "verified" significa que la consulta se completó; puede haber devuelto cero ediciones, lo cual no equivale a un fallo técnico.
                verification[key] = "verified"

            except Exception as exc:
                print(
                    f"No se pudieron verificar idiomas para "
                    f"{book.title!r}: {exc}"
                )
                catalog_cache.pop(key, None)
                verification[key] = "error"

        else:
            verification[key] = "not_checked"

        # Reúne los idiomas registrados en las ediciones concretas encontradas.
        editions = catalog_cache.get(key, [])

        # BookCatalogEdition.language es una lista, no un string.
        edition_languages = list(dict.fromkeys(
            language
            for edition in editions
            for language in (edition.get("language") or [])
            if isinstance(language, str) and language.strip()
        ))

        matched_language = any(
            normalize_language(language) in requested_codes
            for language in edition_languages
        )

        # En modo exclusivo se exige confirmación positiva de una edición en uno de los idiomas aceptados; sin ella, se omite el libro.
        if exclusive and not (
            verification.get(key) == "verified"
            and requested_codes
            and matched_language
        ):
            continue

        # Sustituye el puntaje de idioma calculado con metadatos de obra por el puntaje basado en los idiomas de las ediciones consultadas.
        if edition_languages:
            language_score = calculate_language_score(
                context,
                edition_languages,
            )

            score = max(
                0,
                min(
                    100,
                    book.match_score
                    - book.language_score
                    + language_score,
                ),
            )

            book = book.model_copy(update={
                "language_score": language_score,
                "match_score": score,
            })

        updated_books.append(book)

    # Prepara el nuevo ranking para sincronizar también las evaluaciones.
    recalculated_by_key = {
        book_key(book): book
        for book in updated_books
    }

    evaluations = state.get("evaluated_books")

    if evaluations:
        updated_evaluations = []

        for evaluation in evaluations.books:
            key = (
                f"{normalize_text(evaluation.title)}|"
                f"{normalize_text(evaluation.author)}"
            )

            recalculated = recalculated_by_key.get(key)

            if recalculated:
                updated_evaluations.append(
                    evaluation.model_copy(update={
                        "is_relevant": (
                            recalculated.match_score >= 65
                            and recalculated.genre_intent_score >= 20
                        ),
                        "match_score": recalculated.match_score,
                        "language_score": recalculated.language_score,
                    })
                )
            else:
                updated_evaluations.append(
                    evaluation.model_copy(update={
                        "is_relevant": False,
                    })
                )

        evaluations = EvaluatedBooks(books=updated_evaluations)

    # Vuelve a aplicar los umbrales con el puntaje de idioma actualizado. Se conservan además pedidos explícitos y candidatos provisionales.
    updated_books = [
        book
        for book in updated_books
        if (
            book.match_score >= 65
            and book.genre_intent_score >= 20
        ) or is_requested(book) or getattr(book, "is_provisional", False)
    ]

    # Ordena primero la obra pedida explícitamente y luego por afinidad.
    updated_books.sort(
        key=lambda book: (
            not is_requested(book),
            -book.match_score,
        )
    )

    updated_books = updated_books[:8]

    # El nodo actualiza el ranking, las evaluaciones y las cachés de idioma.
    return {
        "ranked_books": RankedBooks(books=updated_books),
        "evaluated_books": evaluations,
        "candidate_catalog_editions": catalog_cache,
        "candidate_language_verification": verification,
    }

def research_ranked_books(state: AgentState) -> dict:
    """Investiga los libros ordenados y reutiliza resultados guardados."""
    context = state["reader_context"]
    all_ranked_books = state["ranked_books"].books
    requested_title = normalize_text(context.requested_book_title)
    requested_author = normalize_text(context.requested_book_author)

    # La obra solicitada explícitamente se investiga primero para que no quede fuera si se alcanza el límite de libros por ejecución.
    requested_books = [
        book for book in all_ranked_books
        if requested_title
        and normalize_text(book.title) == requested_title
        and (
            not requested_author
            or normalize_text(book.author) == requested_author
        )
    ]

    other_books = [
        book for book in all_ranked_books
        if book not in requested_books
    ]

    ranked_books = (requested_books + other_books)[:8]

    # Si no hay candidatos, no se consulta ni el buscador ni el modelo.
    if not ranked_books:
        return {
            "book_research": [],
            "retrieved_book_research": [],
        }

    # raw_records conserva la evidencia original; structured_by_key guarda la versión resumida y validada que usará el nodo de recomendaciones.
    raw_records = []
    structured_by_key = {}
    research_to_store = []

    MAX_RESEARCH_CHARS_PER_BOOK = 3500
    MAX_SOURCES_PER_BOOK = 5

    for book in ranked_books:
        key = (
            normalize_text(book.title),
            normalize_text(book.author),
        )

        # 1. Buscar primero la investigación ya guardada para evitar una nueva consulta web y una llamada de estructuración innecesaria.
        try:
            cached_research = get_cached_book_research(
                book.title,
                book.author,
            )
        except Exception as exc:
            print(
                f"Cache lookup failed for {book.title}: "
                f"{type(exc).__name__}"
            )
            cached_research = None

        if cached_research is not None:
            # La caché contiene datos estructurados, así que no hay que volver a enviarlos al modelo. Se registra también que fue un acierto.
            structured_by_key[key] = cached_research

            raw_records.append({
                "title": book.title,
                "author": book.author,
                "raw_research": "",
                "sources": list(cached_research.sources),
                "cache_hit": True,
            })
            continue

        # 2. Si no hay caché válida, consulta una vez el servicio de investigación web configurado para este libro.
        try:
            raw_text = research_book(book.title, book.author) or ""
        except Exception as exc:
            print(
                f"Research failed for {book.title}: "
                f"{type(exc).__name__}"
            )
            raw_text = ""

        # Conserva solo URLs únicas y limita cuántas fuentes se procesan.
        urls = list(dict.fromkeys(
            re.findall(r"URL:\s*(https?://\S+)", raw_text)
        ))[:MAX_SOURCES_PER_BOOK]

        raw_records.append({
            "title": book.title,
            "author": book.author,
            "raw_research": raw_text,
            "sources": urls,
            "cache_hit": False,
        })

        # Sin evidencia textual no se llama al modelo para resumirla.
        if not raw_text.strip():
            continue

        item = {
            "title": book.title,
            "author": book.author,
            "evidence": raw_text[:MAX_RESEARCH_CHARS_PER_BOOK],
            "allowed_sources": urls,
        }

        prompt = f"""Structure the supplied research for exactly one book.
            Evidence:
            {json.dumps(item, ensure_ascii=False)}

            Rules:
            - Return exactly one book.
            - Preserve supplied title and author exactly.
            - Use only evidence supplied for this book.
            - Do not invent plot details, themes, style, or critical reception.
            - Use an empty synopsis if unsupported.
            - Use empty lists for unsupported themes, tone, or style.
            - Use null for unsupported critical reception.
            - Sources must be exact URLs from allowed_sources.
            - Do not include unsupported information.
        """

        # 3. Pide un registro estructurado, usando únicamente la evidencia web disponible y limitando el tamaño de entrada por libro.
        try:
            parsed = _invoke_llm_with_rate_limit_retry(
                book_research_llm, prompt, "investigar libros"
            )

            if isinstance(parsed, dict):
                parsed = BookResearchResults.model_validate(parsed)

            if not isinstance(parsed, BookResearchResults):
                raise ValueError(
                    "The research LLM returned an invalid structured result."
                )

            if len(parsed.books) != 1:
                raise ValueError(
                    "The research LLM must return exactly one book."
                )

            research = parsed.books[0]

            # Confirma que la respuesta del modelo sigue refiriéndose al libro que se le pidió investigar.
            expected_key = (
                normalize_text(book.title),
                normalize_text(book.author),
            )
            actual_key = (
                normalize_text(research.title),
                normalize_text(research.author),
            )

            if actual_key != expected_key:
                raise ValueError(
                    "The research LLM returned a different book."
                )

            # Conserva el título y autor originales y descarta fuentes que el modelo agregue sin que aparezcan en la búsqueda.
            research.title = book.title
            research.author = book.author
            allowed_sources = set(urls)
            research.sources = [
                source
                for source in research.sources
                if source in allowed_sources
            ]

            # Solo guarda un resultado que incluya algún dato aprovechable.
            if (
                research.synopsis.strip()
                or research.themes
                or research.tone
                or research.style
                or (research.critical_reception or "").strip()
            ):
                structured_by_key[key] = research
                research_to_store.append(research)

        except Exception as exc:
            print(
                f"Could not structure research for {book.title}: "
                f"{type(exc).__name__}"
            )

    # 4. Persiste únicamente investigaciones nuevas con contenido útil.
    if research_to_store:
        try:
            store_book_research(research_to_store)
        except Exception as exc:
            # Un fallo de persistencia no debe descartar la investigación obtenida durante esta ejecución.
            print(
                f"Could not store book research: {type(exc).__name__}"
            )

    # 5. Mantiene una posición por libro para que los datos de investigación sigan alineados con el ranking, aunque no se haya encontrado evidencia.
    structured_research = []

    for book in ranked_books:
        key = (
            normalize_text(book.title),
            normalize_text(book.author),
        )

        research = structured_by_key.get(key)

        if research is None:
            research = BookResearch(
                title=book.title,
                author=book.author,
                synopsis="",
                themes=[],
                tone=[],
                style=[],
                critical_reception=None,
                sources=[],
            )

        structured_research.append(research)

    return {
        "book_research": structured_research,
        "retrieved_book_research": raw_records,
    }

def generate_recommendation(state: AgentState) -> AgentState:
    """Redacta las recomendaciones a partir del ranking y la investigación."""
    context = state["reader_context"]

    # Los autores excluidos y el ranking general se procesaron antes.
    # El idioma de la edición concreta se verifica en el siguiente nodo.
    # Estos mensajes ayudan a inspeccionar en consola qué candidatos llegaron al paso de redacción.
    all_ranked_books = state["ranked_books"].books
    print("\n--- DEBUG GENERATE RECOMMENDATION ---")
    print("Libros recibidos:", len(all_ranked_books))

    for book in all_ranked_books:
        print(
            book.title,
            "|", book.author,
            "| score:", book.match_score,
            "| genre:", book.genre_intent_score,
        )

    requested_title = normalize_text(context.requested_book_title)
    requested_author = normalize_text(context.requested_book_author)

    def is_requested(book):
        if not requested_title:
            return False

        return (
            normalize_text(book.title) == requested_title
            and (
                not requested_author
                or normalize_text(book.author) == requested_author
            )
        )

    # Separa el pedido explícito para conservarlo al principio de la lista.
    requested_books = [
        book for book in all_ranked_books
        if is_requested(book)
    ]

    other_recommendations = [
        book for book in all_ranked_books
        if not is_requested(book)
        and (
            book.is_provisional
            or (
                book.match_score >= 65
                and book.genre_intent_score >= 20
            )
        )
    ]

    # Limita el contexto enviado al modelo a los primeros ocho libros.
    ranked_books = (requested_books + other_recommendations)[:8]

    # No se llama al modelo cuando no quedó ningún candidato elegible.
    if not ranked_books:
        return {
            **state,
            "final_recommendations": FinalRecommendations(
                recommendations=[]
            ),
        }

    # Asocia cada investigación con su libro mediante título y autor normalizados.
    research_by_key = {
        (
            normalize_text(research.title),
            normalize_text(research.author),
        ): research.model_dump()
        for research in state.get("book_research", [])
    }

    # Un idioma exclusivo no puede ampliarse con el idioma de la conversación; en modo flexible, este último se agrega como una preferencia más.
    if context.book_language_constraint == "exclusive":
        preferred_languages = list(
            context.preferred_book_languages or []
        )
    else:
        preferred_languages = list(dict.fromkeys(
            [context.conversation_language]
            + (context.preferred_book_languages or [])
        ))

    preferred_normalized = {
        normalize_language(language)
        for language in preferred_languages
        if normalize_language(language)
    }

    # Prepara entradas pequeñas y consistentes para el prompt del modelo.
    book_records = []

    for book in ranked_books:
        key = (
            normalize_text(book.title),
            normalize_text(book.author),
        )

        available_languages = [
            language.strip()
            for language in (book.languages or [])
            if isinstance(language, str) and language.strip()
        ]

        matching_languages = [
            language
            for language in available_languages
            if normalize_language(language) in preferred_normalized
        ]

        book_records.append({
            "title": book.title,
            "author": book.author,
            "open_library_id": book.open_library_id,
            "match_score": book.match_score,
            "genre_intent_score": book.genre_intent_score,
            "themes_mood_score": book.themes_mood_score,
            "literary_style_score": book.literary_style_score,
            "length_score": book.length_score,
            "language_score": book.language_score,
            "provisional_candidate": book.is_provisional,
            "pages": book.pages,
            "preferred_languages": preferred_languages,
            "matching_preferred_language_metadata": matching_languages,
            "description": (book.description or "")[:500],
            "subjects": (book.subjects or [])[:5],
            "research": research_by_key.get(
                key,
                {
                    "synopsis": "",
                    "themes": [],
                    "tone": [],
                    "style": [],
                    "critical_reception": None,
                    "sources": [],
                },
            ),
        })

    # El prompt restringe las respuestas a estos libros y a la evidencia proporcionada para cada uno; no autoriza inventar títulos ni datos.
    prompt = f"""
        Create personalized recommendations for these books only.

        Reader context:
        {context.model_dump_json()}

        Books and research:
        {json.dumps(book_records, ensure_ascii=False)}

        Requirements:
        - Return every non-provisional book exactly once.
        - A provisional candidate may be omitted if the supplied metadata and
          research do not support a sufficiently good recommendation.
        - Include a provisional candidate only when the supplied evidence
          supports a plausible match; communicate remaining uncertainty.
        - Do not add other books.
        - Preserve the supplied titles, authors, and match scores.
        - Write in the reader's conversation language.
        - Base factual claims only on supplied metadata and research.
        - Do not invent plot details, critical reception, or availability.
        - Explain specifically how each book matches the reader's intent,
          mood, genres, and other preferences when evidence supports it.
        - Do not present generic literary themes as personalized reasons.
        - If evidence is insufficient, state the limitation explicitly.
        - Use the supplied page count when available.
        - If page count is missing, use
          "Cantidad de páginas no disponible".
        - Never infer page count.
        - Language metadata may aggregate multiple editions.
        - The reader's conversation language and explicitly requested
          languages have priority. English is only a fallback when
          suitable books are unavailable in those languages.
        - Never use English as a fallback when book_language_constraint
          is "exclusive".
        - Do not claim that a specific edition is available in a language
          unless the supplied evidence confirms that edition's language.
        - Do not use aggregated work-level language metadata to claim that
          a book is available in English or any other language.
        - Do not discuss language availability in why_it_matches; edition-level
          language information is added after recommendations are generated.
        - Do not let language metadata alone strengthen the literary match.
        - Mark only the first ranked book as the best match.
        - Describe potential drawbacks supported by the evidence.
        - If no significant drawbacks are supported, use
          "No se identificaron desventajas significativas".
        - Write why_it_matches as natural, human and personalized prose.
        - Explain why the book could appeal to this reader using concrete
        evidence from the supplied metadata and research.
        - Never mention scores, points, percentages, ranking calculations
        or internal evaluation criteria.
        - Do not use generic praise or formulaic explanations.
        - Do not invent similarities, plot details or literary characteristics.
        - If evidence is limited, communicate uncertainty naturally.
        - Do not mention language or length scoring in the explanation.

        Return exactly one JSON object matching the FinalRecommendations schema.
        The root object must contain a "recommendations" key whose value is
        an array of actual book recommendation objects.
        Each item must contain the required fields and data for its book.
        Do not return the schema definition itself.
        Do not return schema metadata such as "$defs", "properties",
        "required", "title", or "description".
    """

    # Obtiene recomendaciones en el idioma de la conversación y con el formato Pydantic FinalRecommendations.
    result = _invoke_llm_with_rate_limit_retry(
        recommendation_structured_llm, prompt, "generar recomendaciones"
    )

    # Valida el tipo de respuesta antes de usar sus campos.
    if isinstance(result, dict):
        result = FinalRecommendations.model_validate(result)

    if not isinstance(result, FinalRecommendations):
        raise ValueError(
            "The recommendation LLM returned an invalid structured result."
        )

    def key_of(book):
        return (
            normalize_text(book.title),
            normalize_text(book.author),
        )

    # Mantiene una copia autoritativa de cada candidato para validar la salida y recuperar sus metadatos originales después de la generación.
    original_by_key = {
        key_of(book): book
        for book in ranked_books
    }

    generated_by_key = {}

    # Rechaza libros inventados o duplicados por el modelo.
    for recommendation in result.recommendations:
        key = key_of(recommendation)

        if key not in original_by_key:
            raise ValueError(
                f"Unexpected recommendation: "
                f"{recommendation.title} — {recommendation.author}"
            )

        if key in generated_by_key:
            raise ValueError(
                f"Duplicate recommendation: "
                f"{recommendation.title} — {recommendation.author}"
            )

        generated_by_key[key] = recommendation

    # Los candidatos normales deben aparecer todos; los provisionales pueden omitirse si la evidencia no alcanza para recomendarlos.
    required_keys = {
        key_of(book)
        for book in ranked_books
        if not book.is_provisional
    }
    if not required_keys.issubset(generated_by_key):
        raise ValueError(
            "The recommendation LLM omitted one or more fully ranked books."
        )

    # La respuesta del modelo puede omitir candidatos provisionales, pero conserva el orden original de los que sí recomendó.
    validated = []

    selected_books = [
        book for book in ranked_books if key_of(book) in generated_by_key
    ]

    for index, original in enumerate(selected_books):
        recommendation = generated_by_key[key_of(original)]

        languages = [
            language.strip()
            for language in (original.languages or [])
            if isinstance(language, str) and language.strip()
        ]

        preferred_matches = [
            language
            for language in languages
            if normalize_language(language) in preferred_normalized
        ]

        # Los datos de identidad y puntaje provienen del candidato original, no de valores que el modelo pudiera haber cambiado.
        recommendation.title = original.title
        recommendation.author = original.author
        recommendation.match_score = original.match_score
        recommendation.open_library_id = original.open_library_id

        # La extensión se basa solo en páginas conocidas; no se inventa un número cuando el catálogo no lo proporciona.
        pages = original.pages
        if (
            isinstance(pages, int)
            and not isinstance(pages, bool)
            and pages > 0
        ):
            recommendation.approximate_length = (
                f"Aproximadamente {pages} páginas"
            )
        else:
            recommendation.approximate_length = "No disponible"

        # Los idiomas de la obra pueden reunir varias ediciones, por eso el texto aclara que el idioma de la edición concreta aún debe verificarse.
        if preferred_matches:
            recommendation.language = (
                "Idioma preferido presente en los metadatos "
                "agregados de la obra: "
                + ", ".join(preferred_matches)
                + ". Idioma de la edición concreta por verificar."
            )
        else:
            recommendation.language = (
                "Idioma de la edición concreta no confirmado."
            )

        recommendation.is_best_match = index == 0

        # No se agrega una advertencia para justificar libros débiles.
        validated.append(recommendation)

    # Mantiene el resto del estado y actualiza la lista de recomendaciones.
    return {
        **state,
        "final_recommendations": FinalRecommendations(
            recommendations=validated
        ),
    }


def build_book_search_filters(state: AgentState) -> dict:
    """Convierte el contexto del lector en filtros para buscar libros."""
    reader_context = state["reader_context"]

    def unique(values: list[str] | None) -> list[str]:
        # Limpia valores vacíos y elimina duplicados sin cambiar su prioridad.
        result = []
        seen = set()

        for value in values or []:
            if not isinstance(value, str):
                continue

            value = value.strip()
            key = normalize_text(value)

            if value and key and key not in seen:
                seen.add(key)
                result.append(value)

        return result

    # Separa los datos de una obra solicitada de las preferencias generales.
    requested_title = reader_context.requested_book_title
    requested_author = reader_context.requested_book_author
    requested_language = reader_context.requested_book_language

    # Los libros de referencia sirven para orientar la búsqueda, pero no deben volver como recomendaciones; por eso empiezan en excluded_titles.
    reference_books = unique(reader_context.reference_books)
    excluded_titles = list(reference_books)

    # Busca posibles títulos alternativos de las referencias para excluir también traducciones reconocibles de la misma obra.
    if reference_books:
        prompt = f"""
        Identify the Spanish and English titles of these reference books.

        Reference books provided by the reader:
        {reference_books}

        Rules:
        - Include each supplied title exactly as written.
        - Include alternative Spanish or English titles only when
          confidently identified as the same literary work.
        - Do not include adaptations, sequels, similar works,
          or different works by the same author.
        - Do not invent titles.
        - Remove duplicates.
        - Return only the list of titles.
        """

        translations = _invoke_llm_with_rate_limit_retry(
            reference_titles_llm, prompt, "traducir t?tulos de referencia"
        )
        excluded_titles = unique(
            reference_books + (translations.titles or [])
        )

    # La obra solicitada explícitamente nunca debe excluirse como referencia.
    if requested_title:
        requested_key = normalize_text(requested_title)
        excluded_titles = [
            title
            for title in excluded_titles
            if normalize_text(title) != requested_key
        ]

    # Estos términos describen intención, ánimo y otras preferencias libres.
    preference_terms = unique([
        reader_context.reading_intent,
        reader_context.current_mood,
        *(reader_context.other_preferences or []),
    ])

    # El título solo se convierte en término de búsqueda si se pidió un libro concreto; las referencias se mantienen en la lista de exclusiones.
    search_terms = unique(
        [requested_title] if requested_title else []
    )

    # Combina idiomas solicitados y preferidos, manteniendo su orden.
    languages = unique(reader_context.preferred_book_languages)
    if requested_language:
        languages = unique([requested_language] + languages)

    # El idioma de la conversación se suma como preferencia flexible, pero no amplía una restricción exclusiva.
    if reader_context.book_language_constraint != "exclusive":
        languages = unique(
            [reader_context.conversation_language] + languages
        )

    # El autor pedido explícitamente queda antes que las preferencias generales.
    authors = unique(reader_context.preferred_authors)
    if requested_author:
        authors = unique([requested_author] + authors)

    # Agrupa las preferencias limpias en un modelo que entiende book_search.py.
    filters = BookSearchFilters(
        subjects=unique(reader_context.preferred_genres),
        languages=languages,
        authors=authors,
        content_type=reader_context.preferred_content_type,
        search_terms=search_terms,
        preference_terms=preference_terms,
        excluded_titles=excluded_titles,
    )

    return {"book_search_filters": filters}


def discover_books(state: AgentState) -> AgentState:
    """Busca candidatos de catálogo y los combina con los ya acumulados."""
    filters = state["book_search_filters"]

    # Si todavía no hay filtros, devuelve una lista vacía y avanza el contador.
    if filters is None:
        return {
            "book_candidates": BookCandidates(books=[]),
            "discovery_round": state.get("discovery_round", 0) + 1,
        }

    # Recupera resultados de catálogo y prepara reglas específicas del pedido.
    results = search_books(filters, max_results=20)
    context = state["reader_context"]
    requested_category = classify_requested_content_type(
        context.preferred_content_type
    )

    def normalize(value: str | None) -> str:
        return normalize_text(value)

    requested_title = context.requested_book_title
    requested_key = normalize(requested_title) if requested_title else None

    # Las referencias no deben aparecer como recomendaciones, excepto cuando el lector pidió explícitamente ese mismo título.
    reference_titles = {
        normalize(title)
        for title in (filters.excluded_titles or [])
        if isinstance(title, str) and title.strip()
    }

    # El pedido explícito tiene prioridad sobre la exclusión por referencias.
    if requested_key:
        reference_titles.discard(requested_key)

    # La búsqueda puede repetirse: se conservan candidatos anteriores y se evita volver a incorporar la misma combinación de título y autor.
    existing_books = state["book_candidates"].books
    candidates = []
    seen = {
        (normalize(book.title), normalize(book.author))
        for book in existing_books
    }

    # Convierte cada resultado externo en BookCandidate después de filtrar tipo de obra, título, autor, referencias y duplicados.
    for result in results:
        result_category = classify_content_type(
            result.get("subjects")
        )

        if (
            requested_category != "unknown"
            and result_category not in {"unknown", requested_category}
        ):
            continue
        title = (result.get("title") or "").strip()
        authors = result.get("authors") or result.get("author_name") or []

        if isinstance(authors, str):
            authors = [authors]

        authors = [
            author.strip()
            for author in authors
            if isinstance(author, str) and author.strip()
        ]

        if not title or not authors:
            continue

        author = authors[0]
        title_key = normalize(title)

        if title_key in reference_titles:
            continue

        key = (title_key, normalize(author))
        if key in seen:
            continue

        # Las respuestas externas no siempre usan el mismo nombre o tipo para los campos; se normalizan antes de crear el modelo.
        languages = result.get("language") or result.get("languages") or []
        if isinstance(languages, str):
            languages = [languages]

        subjects = result.get("subjects") or []
        if isinstance(subjects, str):
            subjects = [subjects]

        candidates.append(
            BookCandidate(
                title=title,
                author=author,
                reason=(
                    "Encontrado mediante búsqueda de catálogo: "
                    f"{result.get('matched_search_term', '')}"
                ),
                published_date=result.get("published_date"),
                languages=languages,
                subjects=subjects,
                pages=result.get("pages"),
                isbn=result.get("isbn"),
                publisher=result.get("publisher"),
                description=result.get("description"),
                open_library_id=result.get("open_library_id"),
            )
        )
        seen.add(key)

    # Vuelve a comprobar tipo de obra y autores excluidos para todo el conjunto, incluidos candidatos que ya estaban en el estado.
    all_candidates_first = existing_books + candidates
    all_candidates = [
        book
        for book in all_candidates_first
        if (
            requested_category == "unknown"
            or classify_content_type(book.subjects)
            in {"unknown", requested_category}
        )
        and not author_is_excluded(
            book.author,
            context.excluded_authors,
        )
    ]
    # Si existe un título concreto pedido, sus coincidencias quedan primero.
    if requested_key:
        requested_author_key = (
            normalize(context.requested_book_author)
            if context.requested_book_author
            else None
        )

        requested_candidates = [
            book
            for book in all_candidates
            if normalize(book.title) == requested_key
            and (
                not requested_author_key
                or normalize(book.author) == requested_author_key
            )
        ]

        other_candidates = [
            book
            for book in all_candidates
            if book not in requested_candidates
        ]

        all_candidates = requested_candidates + other_candidates

    # Limita los candidatos para reducir las llamadas de identidad y evaluación.
    return {
        "book_candidates": BookCandidates(
            books=all_candidates[:MAX_BOOK_CANDIDATES]
        ),
        "discovery_round": state.get("discovery_round", 0) + 1,
    }


def matches_excluded_content(candidate: BookCandidate, reader_context: ReaderContext) -> bool:
    """Detecta temas o géneros excluidos en los datos conocidos del candidato."""
    excluded_terms = [
        *reader_context.excluded_genres,
        *reader_context.excluded_topics,
    ]

    # Sin términos de exclusión no hay motivo para comparar el candidato.
    if not excluded_terms:
        return False

    # Busca en título, razón de hallazgo, descripción y materias del catálogo.
    searchable_text = normalize_text(
        " ".join(
            [
                candidate.title or "",
                candidate.reason or "",
                candidate.description or "",
                " ".join(candidate.subjects or []),
            ]
        )
    )

    # Los límites de palabra evitan coincidencias parciales dentro de otros términos.
    for term in excluded_terms:
        normalized_term = normalize_text(term.strip())

        if not normalized_term:
            continue

        pattern = rf"(?<!\w){re.escape(normalized_term)}(?!\w)"

        if re.search(pattern, searchable_text):
            return True

    return False


def evaluate_and_rank_book_candidates(state: AgentState) -> AgentState:
    """Evalúa la compatibilidad literaria y construye el ranking de candidatos."""
    context = state["reader_context"]
    candidates = state["book_candidates"].books

    requested_title = context.requested_book_title
    requested_author = context.requested_book_author
    requested_key = normalize_text(requested_title) if requested_title else None
    requested_author_key = (
        normalize_text(requested_author) if requested_author else None
    )

    # Una petición concreta se evalúa aparte y puede conservarse aunque no cumpla los umbrales de una recomendación general.
    def is_requested_book(book: BookCandidate) -> bool:
        if not requested_key:
            return False

        if normalize_text(book.title) != requested_key:
            return False

        return (
            not requested_author_key
            or normalize_text(book.author) == requested_author_key
        )

    # Retira autores y contenidos excluidos antes de enviar títulos al modelo.
    eligible_candidates = [
        book
        for book in candidates
        if not author_is_excluded(
            book.author,
            context.excluded_authors,
        )
        and matches_language_constraint(
            book.languages,
            context,
        )
        and (
            is_requested_book(book)
            or not matches_excluded_content(book, context)
        )
    ]

    # Reutiliza evaluaciones e identidades existentes entre rondas del grafo.
    evaluations = state.get("evaluated_books")
    identities_result = state.get("work_identities")

    previous_evaluations = evaluations.books if evaluations else []
    identities = identities_result.works if identities_result else []

    def normalize_key(title: str, author: str) -> tuple[str, str]:
        return normalize_text(title), normalize_text(author)

    def identity_key(book: BookCandidate) -> str:
        if book.open_library_id:
            return f"openlibrary:{book.open_library_id}"
        return f"{normalize_text(book.title)}|{normalize_text(book.author)}"

    # Asocia cada candidato con su resultado de resolución de identidad.
    identity_by_id = {
        identity.candidate_id: identity
        for identity in identities
    }

    # Prepara referencias por título, ID y grupo de obra para excluirlas aunque aparezcan con una traducción o edición distinta.
    reference_titles = {
        normalize_text(title)
        for title in (context.reference_books or [])
        if not requested_key or normalize_text(title) != requested_key
    }
    reference_ids = set(state.get("reference_candidate_ids", []))
    reference_groups = set(state.get("reference_work_groups", []))

    excluded_keys = {
        normalize_key(book.title, book.author)
        for book in candidates
        if not is_requested_book(book) and matches_excluded_content(book, context)
    }

    # Mantiene evaluaciones previas válidas y elimina las que chocan con exclusiones nuevas; una solicitud explícita conserva prioridad.
    previous_by_key = {
        normalize_key(item.title, item.author): item
        for item in previous_evaluations
        if (
            normalize_key(item.title, item.author) not in excluded_keys
            or (
                requested_key
                and normalize_text(item.title) == requested_key
                and (
                    not requested_author_key
                    or normalize_text(item.author) == requested_author_key
                )
            )
        )
    }
    previous_keys = set(previous_by_key)

    # Evita llamar al modelo cuando los filtros dejaron cero candidatos.
    if not eligible_candidates:
        return {
            **state,
            "evaluated_books": EvaluatedBooks(
                books=list(previous_by_key.values())
            ),
            "ranked_books": RankedBooks(books=[]),
        }

    # Descarta elementos ya evaluados y referencias identificadas como la misma obra, sin descartar el libro solicitado explícitamente.
    eligible = []

    for book in eligible_candidates:
        key = normalize_key(book.title, book.author)
        is_requested = is_requested_book(book)

        if key in previous_keys and not is_requested:
            continue

        if key[0] in reference_titles and not is_requested:
            continue

        identity = identity_by_id.get(identity_key(book))

        if identity and identity.confidence >= 0.8 and not is_requested:
            if identity.group_id and identity.group_id in reference_groups:
                continue

            if set(identity.included_works) & reference_ids:
                continue

        eligible.append(book)

    # Puede quedar vacío después de quitar duplicados o referencias.
    if not eligible:
        return {
            **state,
            "evaluated_books": EvaluatedBooks(
                books=list(previous_by_key.values())
            ),
            "ranked_books": RankedBooks(books=[]),
        }

    # Se evalúan lotes pequeños para limitar el tamaño del prompt.
    returned = {}
    batch_size = 5

    books_without_evidence = []
    books_with_evidence = []

    # Separa candidatos con evidencia literaria de los que solo tienen título y autor; estos últimos no deben obtener una nota alta sin evidencia.
    for book in eligible:
        if (book.description or "").strip() or book.subjects:
            books_with_evidence.append(book)
        else:
            books_without_evidence.append(book)

    for start in range(0, len(books_with_evidence), batch_size):
        # Cada lote mantiene un mapa entre IDs temporales y libros originales, para validar que el modelo evalúe exactamente lo que se le envió.
        batch = books_with_evidence[start:start + batch_size]

        batch_records = []
        expected_batch = {}

        for index, book in enumerate(batch):
            candidate_id = f"batch_{start}_candidate_{index}"
            identity = identity_by_id.get(identity_key(book))

            batch_records.append({
                "candidate_id": candidate_id,
                "title": book.title,
                "author": book.author,
                "description": (book.description or "")[:250],
                "subjects": (book.subjects or [])[:5],
                "work_type": identity.work_type if identity else "uncertain",
                "identity_confidence": identity.confidence if identity else 0,
            })

            expected_batch[candidate_id] = book

        # Pide puntuaciones separadas y exige que cada una se apoye únicamente en el contexto y los metadatos incluidos.
        batch_prompt = f"""
            You are evaluating literary compatibility for book recommendations.

            READER CONTEXT:
            {context.model_dump_json()}

            REFERENCE BOOKS:
            {json.dumps(context.reference_books or [], ensure_ascii=False)}

            CANDIDATES TO EVALUATE:
            {json.dumps(batch_records, ensure_ascii=False)}

            TASK:
            Evaluate every candidate using only the supplied information.

            For each candidate, provide:
            - candidate_id
            - title
            - author
            - genre_intent_score: integer from 0 to 30
            - themes_mood_score: integer from 0 to 30
            - literary_style_score: integer from 0 to 15
            - reason: concise explanation of the evaluation and evidence limitations

            SCORING:
            1. genre_intent_score (0-30):
            Assess the supported match between the book's genre and the reader's
            requested reading intent and preferred genres.

            2. themes_mood_score (0-30):
            Assess the supported match between the book's documented themes or
            subject matter and the reader's interests and mood.

            3. literary_style_score (0-15):
            Assess stylistic compatibility only when the supplied metadata provides
            evidence about literary style.

            EVIDENCE RULES:
            - Use only the supplied reader context and candidate metadata.
            - Do not invent plots, themes, genres, writing styles, or other book details.
            - Do not infer literary characteristics from an author's fame or reputation.
            - Do not reward a book merely because it is a classic, famous, popular,
              frequently cited, or published by a prestigious publisher.
            - A missing description or subject is not evidence of a positive match.
            - When evidence is insufficient, score conservatively and explain the limitation.
            - Evaluate explicit content-type preferences and requested purpose.
            - Do not reject a candidate solely because it is a collection, adaptation,
              bibliography, catalog, reference work, or study guide.
            - Do not invent a different title or substitute another book.
            - If metadata is insufficient, score conservatively and explain uncertainty.

            IDENTITY AND COMPLETENESS:
            - Evaluate exactly the supplied candidates.
            - Return exactly one evaluation per candidate_id.
            - Copy candidate_id, title, and author exactly as supplied.
            - Do not omit, duplicate, or add candidates.
            - Every score must be an integer within its specified range.

            OUTPUT REQUIREMENTS:
            - Return data conforming to LiteraryEvaluationResults.
            - The root value must contain a "books" array.
            - Return evaluation data, not the schema definition.
            - Do not include Markdown or text outside the structured result.
        """

        result = _invoke_llm_with_rate_limit_retry(
            evaluation_ranking_llm,
            batch_prompt,
            "evaluar candidatos",
        )
        batch_returned = set()

        # Verifica IDs, evita duplicados y restaura título/autor desde el candidato original en vez de confiar en los valores del modelo.
        for item in result.books:
            candidate_id = item.candidate_id

            if candidate_id not in expected_batch:
                raise ValueError(
                    "El modelo devolvió un candidate_id fuera del lote: "
                    f"{candidate_id}"
                )

            if candidate_id in batch_returned:
                raise ValueError(
                    f"El modelo devolvió un candidato duplicado: {candidate_id}"
                )

            batch_returned.add(candidate_id)

            book = expected_batch[candidate_id]
            key = normalize_key(book.title, book.author)

            item.title = book.title
            item.author = book.author

            if key in returned:
                raise ValueError(
                    f"Candidato duplicado en la evaluación: "
                    f"{book.title} — {book.author}"
                )

            returned[key] = item

        # Un lote incompleto no se considera una evaluación válida.
        missing = set(expected_batch) - batch_returned

        if missing:
            missing_titles = [
                (
                    f"{expected_batch[candidate_id].title} — "
                    f"{expected_batch[candidate_id].author}"
                )
                for candidate_id in missing
            ]

            raise ValueError(
                "La evaluación del lote está incompleta. Candidatos omitidos: "
                + "; ".join(missing_titles)
            )

    # Calcula la puntuación final combinando evidencia literaria, extensión e idioma.
    new_evaluations = []
    ranked = []

    for book in eligible:
        key = normalize_key(book.title, book.author)
        item = returned.get(key)

        # Un libro sin descripción ni materias recibe valores neutrales y un techo bajo: no puede parecer una coincidencia confirmada.
        if item is None:
            genre_score = 15
            themes_score = 15
            style_score = 7
            reason = (
                "No hay descripción ni materias suficientes para verificar "
                "la compatibilidad literaria; se mantiene como no confirmado."
            )
            has_literary_evidence = False
        else:
            genre_score = item.genre_intent_score
            themes_score = item.themes_mood_score
            style_score = item.literary_style_score
            reason = item.reason
            has_literary_evidence = True

        # Los componentes de idioma y extensión completan los puntajes literarios devueltos por el modelo.
        length_score = calculate_length_score(context, book.pages)
        language_score = calculate_language_score(context, book.languages or [])

        score = (
            genre_score
            + themes_score
            + style_score
            + length_score
            + language_score
        )

        # Sin evidencia literaria el máximo queda por debajo del umbral normal.
        if not has_literary_evidence:
            score = min(score, 59)

        score = max(0, min(100, score))
        # Para ser relevante debe alcanzar tanto el total mínimo como el mínimo de afinidad con el género o intención solicitados.
        is_relevant = score >= 65 and genre_score >= 20

        new_evaluations.append(
            EvaluatedBook(
                title=book.title,
                author=book.author,
                is_relevant=is_relevant,
                reason=reason,
                match_score=score,
                genre_intent_score=genre_score,
                themes_mood_score=themes_score,
                literary_style_score=style_score,
                length_score=length_score,
                language_score=language_score,
            )
        )

        # El libro solicitado explícitamente se incluye aunque no alcance el umbral que se aplica a recomendaciones generales.
        if (score >= 65 and genre_score >= 20) or is_requested_book(book):
            ranked.append(
                RankedBook(
                    **book.model_dump(),
                    match_score=score,
                    genre_intent_score=genre_score,
                    themes_mood_score=themes_score,
                    literary_style_score=style_score,
                    length_score=length_score,
                    language_score=language_score,
                )
            )

    # Si nadie alcanza el corte normal, retiene un grupo pequeño de candidatos prometedores para que la investigación posterior aporte más evidencia.
    if not ranked:
        # Si el catálogo no alcanza el corte estricto, conservar una lista pequeña para investigar sin marcarlos como relevantes todavía.
        candidate_by_key = {
            normalize_key(book.title, book.author): book
            for book in eligible
        }
        provisional_evaluations = [
            item for item in new_evaluations
            if (
                item.match_score >= 55
                and item.genre_intent_score >= 20
                and item.themes_mood_score >= 14
                and normalize_key(item.title, item.author) in candidate_by_key
            )
        ]
        # Ordena provisionales por compatibilidad y conserva como máximo ocho.
        provisional_evaluations.sort(
            key=lambda item: (
                -item.match_score,
                -item.genre_intent_score,
                -item.themes_mood_score,
            )
        )

        for item in provisional_evaluations[:8]:
            book = candidate_by_key[normalize_key(item.title, item.author)]
            ranked.append(
                RankedBook(
                    **book.model_dump(),
                    match_score=item.match_score,
                    genre_intent_score=item.genre_intent_score,
                    themes_mood_score=item.themes_mood_score,
                    literary_style_score=item.literary_style_score,
                    length_score=item.length_score,
                    language_score=item.language_score,
                    is_provisional=True,
                )
            )

    # La obra solicitada aparece primero; el resto se ordena por puntaje.
    ranked.sort(
        key=lambda book: (
            not (
                requested_key
                and normalize_text(book.title) == requested_key
                and (
                    not requested_author_key
                    or normalize_text(book.author) == requested_author_key
                )
            ),
            -book.match_score,
        )
    )

    # Combina evaluaciones anteriores con las nuevas sin perder resultados que siguen siendo válidos para candidatos no reevaluados.
    all_evaluations = dict(previous_by_key)

    for item in new_evaluations:
        key = normalize_key(item.title, item.author)
        if key not in excluded_keys or (
            requested_key
            and normalize_text(item.title) == requested_key
            and (
                not requested_author_key
                or normalize_text(item.author) == requested_author_key
            )
        ):
            all_evaluations[key] = item

    # Devuelve el ranking y el conjunto acumulado de evaluaciones.
    return {
        **state,
        "evaluated_books": EvaluatedBooks(
            books=list(all_evaluations.values())
        ),
        "ranked_books": RankedBooks(books=ranked),
    }

def resolve_work_identities(state: AgentState) -> AgentState:
    """Agrupa ediciones y títulos alternativos que representan la misma obra."""
    candidates = state["book_candidates"].books
    reader_context = state["reader_context"]
    requested_title = reader_context.requested_book_title
    requested_author = reader_context.requested_book_author

    # Esta normalización local permite comparar título y autor sin depender de diferencias de mayúsculas, tildes o puntuación.
    def normalize(value: str | None) -> str:
        value = unicodedata.normalize("NFKD", (value or "").casefold())
        value = "".join(
            char for char in value
            if not unicodedata.combining(char)
        )
        return " ".join(re.findall(r"\w+", value))

    requested_key = normalize(requested_title) if requested_title else None
    requested_author_key = (
        normalize(requested_author) if requested_author else None
    )

    # Un libro pedido explícitamente no debe tratarse como referencia a excluir.
    reference_titles = [
        title
        for title in (reader_context.reference_books or [])
        if not requested_key or normalize(title) != requested_key
    ]
    reference_title_keys = {normalize(title) for title in reference_titles}

    # El ID (ISBN) de Open Library identifica la obra mejor que título y autor; si falta, se usa una clave textual normalizada.
    def identity_key(book: BookCandidate) -> str:
        if book.open_library_id:
            return f"openlibrary:{book.open_library_id}"
        return f"{normalize_text(book.title)}|{normalize_text(book.author)}"

    # Convierte un resultado de catálogo en candidato para poder consultar también las obras que el lector mencionó como referencia.
    def make_candidate(result: dict) -> BookCandidate | None:
        title = (result.get("title") or "").strip()
        authors = result.get("authors") or result.get("author_name") or []

        if isinstance(authors, str):
            authors = [authors]

        authors = [
            author.strip()
            for author in authors
            if isinstance(author, str) and author.strip()
        ]

        if not title or not authors:
            return None

        languages = result.get("language") or result.get("languages") or []
        if isinstance(languages, str):
            languages = [languages]

        subjects = result.get("subjects") or []
        if isinstance(subjects, str):
            subjects = [subjects]

        return BookCandidate(
            title=title,
            author=authors[0],
            reason="Reference work lookup.",
            published_date=result.get("published_date"),
            languages=languages,
            subjects=subjects,
            pages=result.get("pages"),
            isbn=result.get("isbn"),
            publisher=result.get("publisher"),
            description=result.get("description"),
            open_library_id=result.get("open_library_id"),
        )

    # Reutiliza identidades de rondas anteriores cuando ya fueron calculadas.
    previous_result = state.get("work_identities")
    previous_works = previous_result.works if previous_result else []
    identity_by_id = {
        item.candidate_id: item
        for item in previous_works
    }

    reference_candidates = []

    # En la primera pasada, busca referencias en el catálogo para identificar sus ediciones, traducciones y títulos alternativos.
    if not previous_works:
        for reference in reference_titles:
            try:
                results = search_books(
                    BookSearchFilters(search_terms=[reference]),
                    max_results=3,
                )

                for result in results:
                    book = make_candidate(result)
                    if book and normalize(book.title) == normalize(reference):
                        reference_candidates.append(book)

            except Exception as exc:
                print(f"Reference lookup failed for {reference!r}: {exc}")

    reference_ids = set(state.get("reference_candidate_ids", []))

    if not previous_works:
        reference_ids = {
            identity_key(book)
            for book in reference_candidates
        }

    # Agrega referencias y candidatos que todavía no tienen identidad resuelta.
    books_to_identify = {}

    for book in reference_candidates + candidates:
        candidate_id = identity_key(book)
        if candidate_id not in identity_by_id:
            books_to_identify.setdefault(candidate_id, book)

    # El modelo recibe solo los campos necesarios para identificar la obra, con descripciones y materias acotadas para mantener breve el prompt.
    records = [
        {
            "candidate_id": candidate_id,
            "title": book.title,
            "author": book.author,
            "description": (book.description or "")[:150],
            "subjects": (book.subjects or [])[:3],
        }
        for candidate_id, book in books_to_identify.items()
    ]

    # Evita una llamada al modelo cuando todas las identidades ya están guardadas.
    if records:
        previous_context = [
            {
                "candidate_id": item.candidate_id,
                "group_id": item.group_id,
                "work_type": item.work_type,
                "confidence": item.confidence,
                "included_works": item.included_works,
            }
            for item in previous_works
        ]

        # Los lotes limitan el tamaño de cada solicitud de identificación.
        returned = {}
        batch_size = 3

        for start in range(0, len(records), batch_size):
            batch = records[start:start + batch_size]

            prompt = f"""
                Identify the literary work represented by each candidate.

                Reference titles to exclude from recommendations:
                {reference_titles}

                Previously identified works:
                {previous_context}

                Candidates:
                {batch}

                Return exactly one result for each candidate_id.

                Rules:
                - Copy candidate_id exactly.
                - Use the same group_id for confirmed editions, translations,
                  reprints, or alternate titles of the same work.
                - Every group_id must equal a candidate_id from this batch,
                  or a previous group_id when identity is clear.
                - Reuse a previous group_id only when the identity is clear.
                - included_works must contain only candidate_ids from this batch.
                - Classify each record as standalone, collection, adaptation,
                  or uncertain.
                - Do not classify study guides or literary analyses as standalone works.
                - Never group books based only on author, genre, theme, or language.
                - Identify reference works across languages and alternate titles.
                - If uncertain, use the candidate's own ID as group_id and confidence below 0.8.
                - Never invent facts.
                - Keep evidence under 6 words.
            """

            expected_ids = {
                record["candidate_id"]
                for record in batch
            }

            # Solicita una identidad por candidato y revisa que los IDs devueltos pertenezcan al lote actual.
            batch_result = _invoke_work_identity_with_retry(prompt)
            batch_returned = {}

            for item in batch_result.works:
                if item.candidate_id not in expected_ids:
                    print(
                        "Identidad ignorada: candidato desconocido "
                        f"{item.candidate_id}"
                    )
                    continue

                if item.candidate_id in batch_returned:
                    print(
                        "Identidad duplicada ignorada: "
                        f"{item.candidate_id}"
                    )
                    continue

                batch_returned[item.candidate_id] = item

            # Si el modelo omite un candidato, se conserva como incierto en lugar de asignarle una identidad inventada.
            missing_ids = expected_ids - set(batch_returned)

            for candidate_id in missing_ids:
                print(
                    "Identidad no resuelta por el modelo: "
                    f"{candidate_id}. Se conserva como incierta."
                )
                batch_returned[candidate_id] = WorkIdentity(
                    candidate_id=candidate_id,
                    group_id=candidate_id,
                    work_type="uncertain",
                    included_works=[],
                    confidence=0.0,
                    evidence="No valid identity returned by model",
                )

            returned.update(batch_returned)

        # Asegura que cada registro enviado terminó con una identidad válida o con una marca explícita de incertidumbre.
        expected_ids = {
            record["candidate_id"]
            for record in records
        }

        if set(returned) != expected_ids:
            raise ValueError(
                "Identity validation failed: some candidates are missing."
            )

        identity_by_id.update(returned)

    # Mapea referencias a los IDs que usan WorkIdentity.included_works.
    reference_candidate_ids = set()

    for book in reference_candidates:
        reference_candidate_ids.add(identity_key(book))

    reference_candidate_ids.update(
        candidate_id
        for candidate_id in reference_ids
        if candidate_id in identity_by_id
    )

    # Solo se excluyen grupos de referencia cuando la identificación tiene suficiente confianza para considerarse confirmada.
    reference_groups = {
        identity_by_id[reference_id].group_id
        for reference_id in reference_candidate_ids
        if (
            reference_id in identity_by_id
            and identity_by_id[reference_id].confidence >= 0.8
            and identity_by_id[reference_id].group_id
        )
    }

    # Pone primero la obra solicitada para que sobreviva antes a los límites y a los filtros de deduplicación.
    ordered_candidates = list(candidates)

    if requested_key:
        ordered_candidates.sort(
            key=lambda book: (
                normalize(book.title) != requested_key,
                bool(
                    requested_author_key
                    and normalize(book.author) != requested_author_key
                ),
                normalize(book.title),
                normalize(book.author),
            )
        )

    # Construye la lista sin repetir títulos ni varias ediciones de una misma obra independiente con identidad confirmada.
    selected = []
    seen_groups = set()
    seen_titles = set()

    for book in ordered_candidates:
        candidate_id = identity_key(book)
        identity = identity_by_id.get(candidate_id)

        is_requested = bool(
            requested_key
            and normalize(book.title) == requested_key
            and (
                not requested_author_key
                or normalize(book.author) == requested_author_key
            )
        )

        # Un pedido concreto se conserva aunque el modelo no haya podido resolver su identidad; otros candidatos sin identidad se omiten.
        if identity is None:
            if is_requested:
                identity = WorkIdentity(
                    candidate_id=candidate_id,
                    group_id=candidate_id,
                    work_type="uncertain",
                    included_works=[],
                    confidence=0.0,
                    evidence="Requested work; identity unverified",
                )
                identity_by_id[candidate_id] = identity

                selected.append(book)
                seen_titles.add(
                    (normalize(book.title), normalize(book.author))
                )
            continue

        title_key = (normalize(book.title), normalize(book.author))
        is_requested = bool(
            requested_key
            and normalize(book.title) == requested_key
            and (
                not requested_author_key
                or normalize(book.author) == requested_author_key
            )
        )

        # Excluye las referencias conocidas antes de agregar candidatos.
        if not is_requested and normalize(book.title) in reference_title_keys:
            continue

        # Las decisiones de agrupación o exclusión solo se aplican si la confianza supera el mínimo definido para una identidad confirmada.
        if identity.confidence >= 0.8:
            if not is_requested and identity.group_id in reference_groups:
                continue

            if (
                not is_requested
                and set(identity.included_works) & reference_candidate_ids
            ):
                continue

            if identity.work_type == "standalone" and identity.group_id:
                if identity.group_id in seen_groups:
                    continue
                seen_groups.add(identity.group_id)

        if title_key in seen_titles:
            continue

        seen_titles.add(title_key)
        selected.append(book)

    # Devuelve candidatos depurados y actualiza las identidades y referencias para que la evaluación pueda aplicar los mismos grupos de obras.
    return {
        **state,
        "work_identities": WorkIdentityResults(
            works=list(identity_by_id.values())
        ),
        "book_candidates": BookCandidates(books=selected),
        "reference_candidate_ids": list(reference_candidate_ids),
        "reference_work_groups": list(reference_groups),
    }
