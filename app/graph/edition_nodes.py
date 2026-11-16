"""Verifica ediciones y disponibilidad comercial de las recomendaciones."""

from app.graph.state import AgentState
from app.graph.nodes import normalize_language, normalize_text
from app.models.book import (
    BookEdition,
    BookCatalogEdition,
    FinalRecommendations,
    RetailerSearchStatus,
)
from app.mcp.client import call_mcp_tool
from app.tools.edition_search import (
    RETAILERS,
    clean_retailer_search_title,
    is_mercadolibre_search_url,
    is_valid_retailer_url,
)
from app.tools.book_search import search_open_library_editions


def _deduplicate_purchase_options(
    offers: list[BookEdition | dict],
) -> list[BookEdition]:
    """Valida las ofertas comerciales y elimina las repetidas."""
    # El índice permite comprobar rápidamente que cada oferta pertenece a un retailer conocido y obtener el dominio autorizado para sus URLs.
    retailers_by_name = {
        retailer["name"]: retailer
        for retailer in RETAILERS
    }

    unique_offers = []
    seen_urls = set()

    for item in offers:
        # El MCP puede devolver modelos ya validados o diccionarios. A partir de aquí se trabaja siempre con BookEdition.
        offer = (
            item
            if isinstance(item, BookEdition)
            else BookEdition.model_validate(item)
        )

        retailer = retailers_by_name.get(offer.retailer)

        # No se muestran retailers desconocidos, ofertas no disponibles ni URLs que no pertenezcan al dominio configurado del retailer.
        if retailer is None:
            continue

        if offer.availability != "available":
            continue

        if not is_valid_retailer_url(offer.url, retailer["domain"]):
            continue

        # El fragmento y la barra final no identifican una ficha distinta. casefold permite comparar URLs sin distinguir mayúsculas.
        normalized_url = (
            offer.url.split("#", 1)[0].rstrip("/").casefold()
            if offer.url
            else ""
        )

        if not normalized_url or normalized_url in seen_urls:
            continue

        seen_urls.add(normalized_url)
        unique_offers.append(offer)

    # Orden estable para presentar primero por mercado y luego por tienda.
    return sorted(
        unique_offers,
        key=lambda offer: (
            offer.market.casefold(),
            offer.retailer.casefold(),
        ),
    )


def find_book_editions(state: AgentState) -> dict:
    """Agrega ediciones y opciones de compra a las recomendaciones finales."""
    # Este nodo enriquece recomendaciones existentes; no crea libros nuevos.
    final_recommendations = state["final_recommendations"]

    # Sin recomendaciones no hay ediciones que consultar ni ofertas que validar.
    if not final_recommendations.recommendations:
        return {}

    #Busca la clave "reader_context" en el estado del grafo. Si no está, get devuelve None, no lanza un error.
    reader_context = state.get("reader_context")

    #Consulta si reader_context es un diccionario. Si lo es, entra en la primer rama, si no en else leyendo los atributos del objeto ReaderContext.
    if isinstance(reader_context, dict):
        #Lee la lista de idiomas preferidos, si la clave no existe o está vacía, devuelve una lista vacía.
        preferred_book_languages = (
            reader_context.get("preferred_book_languages") or []
        )
        #Lee el idioma en el que está escrita la conversación, si falta queda como None.
        conversation_language = reader_context.get("conversation_language")
        #Lee si la preferencia del idioma es exclusiva o flexible, "preferred" es el valor por defecto solo si falta la clave, si la clave existe pero está vacía devuelve None y si está con otro valor devuelve ese valor.
        language_constraint = reader_context.get(
            "book_language_constraint", "preferred"
        )
    else:
        #Siempre va a buscar el atributo, que si no lo tiene devuelve None, y si es None o vacío devuelve una lista vacía.
        preferred_book_languages = (
            getattr(reader_context, "preferred_book_languages", None) or []
        )
        conversation_language = getattr(
            reader_context, "conversation_language", None
        )
        language_constraint = getattr(
            reader_context, "book_language_constraint", "preferred"
        )

    #Convierte en booleano la preferencia de idioma, True si es exclusiva, False si es flexible.
    exclusive_language = language_constraint == "exclusive"

    #Si la búsqueda no es exclusiva y hay un idioma de conversación, lo agrega al principio de la lista. dict.fromkeys elimina duplicados sin cambiar el orden; así, el idioma de conversación queda con prioridad.
    if not exclusive_language and conversation_language:
        preferred_book_languages = list(dict.fromkeys(
            [conversation_language] + preferred_book_languages
        ))

    #Normaliza los idiomas para que las comparaciones posteriores sean consistentes —por ejemplo, códigos y nombres equivalentes— e ignora valores que no sean texto o estén vacíos.
    preferred_normalized = {
        normalize_language(language)
        for language in preferred_book_languages
        if isinstance(language, str) and language.strip()
    }

    def offer_language_is_allowed(offer) -> bool:
        # Las respuestas pueden llegar como BookEdition o como diccionarios. Se extraen los mismos tres datos para aplicar una sola política.
        if isinstance(offer, BookEdition):
            retailer_name = offer.retailer
            market = offer.market
            language = offer.language
        else:
            retailer_name = offer.get("retailer")
            market = offer.get("market")
            language = offer.get("language")

        # En modo flexible el idioma no descarta una oferta comercial.
        if not exclusive_language:
            return True

        # Mercado Libre puede devolver un enlace general de búsqueda, no una ficha con idioma. Se conserva solo si hay evidencia de publicación en carrito; el idioma desconocido se aclara en el texto final.
        if (
            retailer_name == "Mercado Libre Argentina"
            and market == "Argentina"
            and not language
            and isinstance(offer, BookEdition)
            and is_mercadolibre_search_url(offer.url)
            and "carrito" in normalize_text(
                offer.availability_evidence or ""
            )
        ):
            return True

        # Para KEL y el mercado internacional, esta función solo considera permitidas las ofertas identificadas como inglesas.
        if retailer_name == "KEL Ediciones" or market == "Internacional":
            return normalize_language(language) == "eng"

        # El resto de los retailers debe coincidir con uno de los idiomas preferidos por el lector.
        return matches_preferred_language(language)

    updated_recommendations = []
    # Estas dos cachés las completa el paso previo de verificación de idiomas.
    cached_catalog_editions = state.get("candidate_catalog_editions", {})
    language_verification = state.get("candidate_language_verification", {})

    for recommendation in final_recommendations.recommendations:
        # La clave se basa en título y autor normalizados para localizar la verificación de esta obra aunque cambien mayúsculas o acentos.
        cache_key = (
            f"{normalize_text(recommendation.title)}|"
            f"{normalize_text(recommendation.author)}"
        )

        verification_status = language_verification.get(cache_key)

        # Si las ediciones ya fueron verificadas, se reutilizan. En modo exclusivo no se inicia una verificación nueva desde este nodo: sin un resultado previo confirmado, la recomendación no se publica.
        if cache_key in cached_catalog_editions:
            catalog_editions = [
                BookCatalogEdition.model_validate(edition)
                for edition in cached_catalog_editions[cache_key]
            ]
        elif exclusive_language:
            continue
        else:
            # En modo flexible se consultan ediciones del catálogo para preparar títulos alternativos en los idiomas preferidos. A diferencia del fallback en inglés de abajo, esta consulta principal no captura excepciones; un fallo puede detener el nodo.
            catalog_editions = [
                BookCatalogEdition.model_validate(edition)
                for edition in search_open_library_editions(
                    title=recommendation.title,
                    author=recommendation.author,
                    preferred_languages=preferred_book_languages,
                    open_library_id=recommendation.open_library_id,
                )
            ]

        def matches_preferred_language(language):
            # Normaliza cada idioma del catálogo antes de compararlo con el conjunto de preferencias preparado arriba.
            normalized = normalize_language(language) if language else None
            return bool(normalized and normalized in preferred_normalized)

        # Toma el primer idioma preferido encontrado entre las ediciones del catálogo; queda en None si ninguna coincide.
        catalog_language = next(
            (
                language
                for edition in catalog_editions
                for language in (edition.language or [])
                if matches_preferred_language(language)
            ),
            None,
        )

        # Para una restricción exclusiva, la obra debe tener verificación positiva y al menos una edición en un idioma aceptado.
        if exclusive_language and (
            verification_status != "verified" or not catalog_language
        ):
            continue

        # Guarda el orden original de preferencia: menor índice significa mayor prioridad para elegir títulos alternativos.
        language_priority = {
            normalize_language(language): index
            for index, language in enumerate(preferred_book_languages)
            if isinstance(language, str) and language.strip()
        }

        # Conserva ediciones con título y al menos un idioma preferido.
        preferred_editions = [
            edition for edition in catalog_editions
            if edition.title and any(
                matches_preferred_language(language)
                for language in (edition.language or [])
            )
        ]

        # Si una edición tiene varios idiomas, se ordena según el más prioritario que coincida con las preferencias del lector.
        preferred_editions.sort(key=lambda edition: min(
            (
                language_priority.get(normalize_language(language), len(language_priority))
                for language in (edition.language or [])
                if matches_preferred_language(language)
            ),
            default=len(language_priority),
        ))

        # Los retailers pueden reconocer mejor el título localizado. Se quitan duplicados conservando el orden de prioridad anterior.
        search_titles = list(dict.fromkeys(
            edition.title.strip() for edition in preferred_editions
            if edition.title and edition.title.strip()
        ))

        # Prepara una lista independiente de títulos en inglés para los retailers que solo ofrecen o reconocen ediciones inglesas.
        english_catalog_editions = [
            edition for edition in catalog_editions
            if any(
                normalize_language(language) == "eng"
                for language in (edition.language or [])
            )
        ]
        if not english_catalog_editions:
            # Si el catálogo inicial no contiene inglés, se intenta una consulta adicional; el fallo no impide buscar en retailers.
            try:
                english_catalog_editions = [
                    BookCatalogEdition.model_validate(edition)
                    for edition in search_open_library_editions(
                        title=recommendation.title,
                        author=recommendation.author,
                        preferred_languages=["English"],
                        max_results=10,
                        open_library_id=recommendation.open_library_id,
                    )
                ]
            except Exception as exc:
                print(
                    f"No se pudieron consultar títulos en inglés para "
                    f"{recommendation.title!r}: {exc}"
                )
        english_search_titles = list(dict.fromkeys(
            edition.title.strip() for edition in english_catalog_editions
            if edition.title and edition.title.strip() and any(
                normalize_language(language) == "eng"
                for language in (edition.language or [])
            )
        ))

        # El MCP busca disponibilidad comercial con el título original y los títulos alternativos obtenidos del catálogo.
        try:
            mcp_result = call_mcp_tool(
                "search_book_availability",
                {
                    "title": recommendation.title,
                    "author": recommendation.author,
                    "preferred_book_languages": preferred_book_languages,
                    "alternate_titles": search_titles,
                    "english_titles": english_search_titles,
                    "exclusive_language": exclusive_language,
                },
            )
            offers = [
                BookEdition.model_validate(offer)
                for offer in (mcp_result.get("offers") or [])
            ]
            statuses = [
                RetailerSearchStatus.model_validate(status)
                for status in (mcp_result.get("statuses") or [])
            ]
        except Exception as exc:
            # Si falla el MCP, se conserva la recomendación sin ofertas y se devuelve un estado visible de error para este libro.
            print(
                f"Availability MCP falló para {recommendation.title!r}: "
                f"{type(exc).__name__}: {exc}"
            )
            offers = []
            statuses = [
                RetailerSearchStatus(
                    retailer="Availability MCP",
                    market="Argentina",
                    status="error",
                    message=f"No se pudo consultar el servidor MCP: {exc}",
                )
            ]

        valid_offers = []

        # El MCP puede informar resultados sin stock o con idioma no válido. Solo se pasan a la deduplicación las ofertas aceptables.
        for offer in offers:
            if isinstance(offer, BookEdition):
                availability = offer.availability
                language = offer.language
            else:
                availability = offer.get("availability")
                language = offer.get("language")

            if availability != "available":
                continue

            if exclusive_language and not offer_language_is_allowed(offer):
                continue

            valid_offers.append(offer)

        # La lista final solo conserva ofertas disponibles, de retailers conocidos y con URLs válidas y únicas.
        purchase_options = _deduplicate_purchase_options(valid_offers)

        # Si existe un título preferido del catálogo, se usa para mostrar la recomendación; de lo contrario se mantiene el título original.
        display_title = (
            clean_retailer_search_title(search_titles[0])
            if search_titles
            else recommendation.title
        )

        # Toma el idioma de la primera oferta comercial que pasa la política.
        commercial_language = next(
            (
                offer.language if isinstance(offer, BookEdition)
                else offer.get("language")
                for offer in purchase_options
                if offer_language_is_allowed(offer)
            ),
            None,
        )

        # Explica qué se pudo verificar: solo catálogo, catálogo y oferta, o únicamente una oferta comercial.
        if catalog_language and commercial_language:
            if matches_preferred_language(commercial_language):
                language_description = (
                    f"Idioma preferido confirmado en catálogo y oferta "
                    f"comercial: {catalog_language} / "
                    f"{commercial_language}."
                )
            else:
                language_description = (
                    f"Edición de catálogo en {catalog_language}; oferta "
                    f"comercial encontrada en {commercial_language}."
                )
        elif catalog_language:
            language_description = (
                f"Edición de catálogo encontrada en idioma preferido: "
                f"{catalog_language}. Disponibilidad comercial en ese idioma "
                f"no confirmada."
            )
        elif commercial_language:
            language_description = (
                f"Oferta comercial encontrada en idioma preferido: "
                f"{commercial_language}."
            )
        elif exclusive_language:
            # Defensa adicional: nunca se publica una recomendación exclusiva si el idioma no quedó confirmado en el catálogo ni en la oferta.
            continue
        else:
            language_description = (
                "No se pudo confirmar el idioma de las ediciones concretas."
            )

        # Reconstruye el mismo tipo de recomendación, conserva sus demás campos y reemplaza los datos enriquecidos por este nodo.
        updated_recommendation = type(recommendation).model_validate(
            {
                **recommendation.model_dump(),
                "title": display_title,
                "language": language_description,
                "purchase_options": purchase_options,
                "retailer_search_status": statuses,
                "catalog_editions": catalog_editions,
            }
        )

        updated_recommendations.append(updated_recommendation)

    # Detecta si el usuario pidió un libro concreto para darle prioridad frente a las demás recomendaciones.
    if isinstance(reader_context, dict):
        requested_title = normalize_text(
            reader_context.get("requested_book_title")
        )
        requested_author = normalize_text(
            reader_context.get("requested_book_author")
        )
    else:
        requested_title = normalize_text(
            getattr(reader_context, "requested_book_title", None)
        )
        requested_author = normalize_text(
            getattr(reader_context, "requested_book_author", None)
        )

    def is_requested(recommendation):
        # Título obligatorio; autor solo se compara si el usuario lo indicó.
        return (
            bool(requested_title)
            and normalize_text(recommendation.title) == requested_title
            and (
                not requested_author
                or normalize_text(recommendation.author) == requested_author
            )
        )

    # Separa primero los pedidos explícitos del resto de recomendaciones.
    requested_recommendations = [
        recommendation
        for recommendation in updated_recommendations
        if is_requested(recommendation)
    ]

    other_recommendations = [
        recommendation
        for recommendation in updated_recommendations
        if not is_requested(recommendation)
    ]

    # Entre las demás, prioriza las que tienen al menos una opción de compra.
    with_purchase_options = [
        recommendation
        for recommendation in other_recommendations
        if recommendation.purchase_options
    ]

    without_purchase_options = [
        recommendation
        for recommendation in other_recommendations
        if not recommendation.purchase_options
    ]

    # El pedido explícito va primero; después, las recomendaciones comprables y finalmente las que no tienen ofertas confirmadas. Se muestran hasta 3.
    selected_recommendations = (
        requested_recommendations
        + with_purchase_options
        + without_purchase_options
    )[:3]

    # Marca como mejor coincidencia el primer elemento de la lista final.
    selected_recommendations = [
        type(recommendation).model_validate({
            **recommendation.model_dump(),
            "is_best_match": index == 0,
        })
        for index, recommendation in enumerate(selected_recommendations)
    ]

    # LangGraph combina esta actualización parcial con el estado existente.
    return {
        "final_recommendations": FinalRecommendations(
            recommendations=selected_recommendations
        )
    }
