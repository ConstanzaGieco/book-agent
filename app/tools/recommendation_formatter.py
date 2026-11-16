
"""Presenta recomendaciones, ediciones y disponibilidad en texto legible.
El formateador no realiza búsquedas ni determina el stock: representa los
modelos finales y conserva la diferencia entre datos de catálogo y ofertas.
"""

from app.models.book import FinalRecommendations


def format_recommendations(
    final_recommendations: FinalRecommendations,
) -> str:
    """Arma el informe final en secciones consistentes para cada recomendación.
    Se muestran las ediciones de catálogo por separado de las ofertas cuya
    disponibilidad ya fue marcada como confirmada.
    """

    # Acumular líneas facilita conservar una presentación consistente.
    lines = ["RECOMENDACIONES", "=" * 60, ""]

    if not final_recommendations.recommendations:
        return "No se encontraron recomendaciones."

    # Numerar desde uno hace que cada recomendación sea fácil de referenciar.
    for index, book in enumerate(
        final_recommendations.recommendations,
        start=1,
    ):
        lines.extend([
            f"{index}. {book.title} — {book.author}",
            f"Coincidencia: {book.match_score}/100",
            "",
            book.introduction,
            "",
            f"Por qué puede gustarte: {book.why_it_matches}",
            f"Posibles desventajas: {book.potential_drawbacks}",
            f"Extensión aproximada: {book.approximate_length}",
            f"Idioma: {book.language}",
            "",
            "EDICIONES DE CATÁLOGO",
            "-" * 40,
        ])

        # Las ediciones del catálogo identifican formatos/ISBN, pero no prueban venta.
        if book.catalog_editions:
            for edition in book.catalog_editions:
                lines.append(f"- Título: {edition.title}")

                if edition.author:
                    lines.append(f"  Autor: {edition.author}")

                if edition.language:
                    lines.append(
                        "  Idiomas registrados: "
                        + ", ".join(edition.language)
                    )

                if edition.isbn:
                    lines.append(f"  ISBN: {edition.isbn}")

                if edition.publisher:
                    lines.append(f"  Editorial: {edition.publisher}")

                if edition.open_library_edition_id:
                    lines.append(
                        "  Ficha de catálogo: "
                        "https://openlibrary.org"
                        f"{edition.open_library_edition_id}"
                    )

                lines.append("  Disponibilidad comercial: no verificada")
                lines.append("")
        else:
            lines.extend([
                "No se encontraron ediciones catalogadas "
                "en los idiomas solicitados.",
                "",
            ])

        lines.extend([
            "OPCIONES COMERCIALES",
            "-" * 40,
        ])

        # No presentar estados unverified ni unavailable como opciones de compra.
        verified_offers = [
            offer for offer in book.purchase_options
            if offer.availability == "available"
        ]

        if verified_offers:
            # Agrupar por mercado para que las opciones locales e internacionales se lean como bloques separados.
            options_by_market = {}

            for offer in verified_offers:
                options_by_market.setdefault(
                    offer.market,
                    [],
                ).append(offer)

            for market, offers in sorted(options_by_market.items()):
                lines.extend([f"{market}:", ""])

                for offer in offers:
                    lines.append(f"- Librería: {offer.retailer}")
                    lines.append(f"  Título: {offer.title}")
                    lines.append(f"  Enlace: {offer.url}")
                    lines.append("  Disponibilidad: verified")

                    if offer.availability_evidence:
                        lines.append(
                            f"  Evidencia: {offer.availability_evidence}"
                        )

                    # Mercado Libre puede no devolver idioma; no inferirlo si
                    # el resultado no lo especifica y avisar que debe verificarse.
                    if offer.language:
                        lines.append(f"  Idioma: {offer.language}")
                    elif offer.retailer == "Mercado Libre Argentina":
                        lines.append(
                            "  Idioma: no informado; verificá la edición "
                            "en los resultados de Mercado Libre."
                        )

                    if offer.isbn:
                        lines.append(f"  ISBN: {offer.isbn}")

                    if offer.publisher:
                        lines.append(f"  Editorial: {offer.publisher}")

                    lines.append("")
        else:
            lines.extend([
                "No se encontraron opciones comerciales válidas.",
                "",
            ])

        # Mostrar el estado de cada comercio incluso cuando no produjo una oferta: ayuda a distinguir ausencia de resultados de error de proveedor.
        if book.retailer_search_status:
            # Explicar límites de los datos evita que el informe prometa precio,
            # envío o stock que las fuentes consultadas no han confirmado.
            lines.extend([
                "ESTADO DE LAS BÚSQUEDAS COMERCIALES",
                "-" * 40,
            ])

            for status in book.retailer_search_status:
                lines.append(
                    f"- {status.retailer} ({status.market}): "
                    f"{status.status} "
                    f"(resultados: {status.results_count})"
                )

                if status.message:
                    lines.append(f"  Detalle: {status.message}")

            lines.append("")

        lines.extend([
            "Nota: una edición de catálogo no confirma que esté a la venta. "
            "La disponibilidad se considera confirmada únicamente cuando "
            "la evidencia lo respalda. Los precios no están disponibles "
            "en los datos actuales; verificá el importe en la librería. "
            "No se calculan gastos de envío ni impuestos.",
            "",
            "=" * 60,
            "",
        ])

    return "\n".join(lines)
