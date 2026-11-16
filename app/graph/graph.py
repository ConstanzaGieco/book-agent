"""Construye y compila el flujo de recomendación de libros con LangGraph."""

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    collect_reader_context,
    build_book_search_filters,
    discover_books,
    resolve_work_identities,
    evaluate_and_rank_book_candidates,
    enrich_ranked_book_languages,
    generate_recommendation,
    research_ranked_books,
)
from app.graph.edition_nodes import find_book_editions
from app.graph.state import AgentState


def build_graph():
    """Crea el grafo que procesa una solicitud de recomendación."""
    # AgentState define la forma del estado compartido entre los nodos.
    builder = StateGraph(AgentState)

    # Extrae del mensaje del usuario el contexto necesario para recomendar.
    builder.add_node("collect_reader_context", collect_reader_context)

    # Convierte el contexto del lector en filtros estructurados de búsqueda.
    builder.add_node("build_book_search_filters", build_book_search_filters)

    # Busca libros candidatos usando esos filtros.
    builder.add_node("discover_books", discover_books)

    # Resuelve qué obra representa cada candidato antes de evaluarlo.
    builder.add_node("resolve_work_identities", resolve_work_identities)

    # Evalúa los candidatos y ordena los que mejor responden a la solicitud.
    builder.add_node(
        "evaluate_and_rank_book_candidates",
        evaluate_and_rank_book_candidates,
    )

    # Verifica las ediciones disponibles y aplica la preferencia de idioma.
    builder.add_node(
        "enrich_ranked_book_languages",
        enrich_ranked_book_languages,
    )

    # Reúne información adicional sobre los libros que quedaron ordenados.
    builder.add_node("research_ranked_books", research_ranked_books)

    # Prepara las recomendaciones que verá el usuario.
    builder.add_node("generate_recommendation", generate_recommendation)

    # Busca ediciones y opciones de compra para las recomendaciones finales.
    builder.add_node("find_book_editions", find_book_editions)

    # START es el punto de entrada: primero se interpreta el mensaje y después se preparan los filtros de búsqueda.
    builder.add_edge(START, "collect_reader_context")
    builder.add_edge("collect_reader_context", "build_book_search_filters")

    # Con los filtros listos, se buscan candidatos y se resuelven sus obras.
    builder.add_edge("build_book_search_filters", "discover_books")
    builder.add_edge("discover_books", "resolve_work_identities")

    # Los candidatos identificados se evalúan, ordenan y enriquecen con información sobre sus idiomas.
    builder.add_edge(
        "resolve_work_identities",
        "evaluate_and_rank_book_candidates",
    )
    builder.add_edge(
        "evaluate_and_rank_book_candidates",
        "enrich_ranked_book_languages",
    )

    # Luego se investiga cada libro y se genera la lista de recomendaciones.
    builder.add_edge(
        "enrich_ranked_book_languages",
        "research_ranked_books",
    )
    builder.add_edge("research_ranked_books", "generate_recommendation")

    # La última etapa busca ediciones y disponibilidad comercial.
    builder.add_edge("generate_recommendation", "find_book_editions")

    # END marca el final del recorrido normal del grafo.
    builder.add_edge("find_book_editions", END)

    # compile() transforma la definición del builder en un grafo ejecutable.
    return builder.compile()