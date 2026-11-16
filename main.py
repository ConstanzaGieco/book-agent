"""Punto de entrada de consola para generar recomendaciones de libros.
El servicio web se ejecuta desde `app.api`; este módulo ofrece una alternativa
interactiva para probar el grafo localmente sin mantener un pedido de ejemplo
fijo ni escribir archivos si no se solicitan.
"""

import argparse
from pathlib import Path


def is_valid_input(user_input: str) -> bool:
    """Comprueba que el texto tenga longitud y contenido alfabético mínimo."""
    cleaned = user_input.strip()

    if len(cleaned) < 3:
        return False

    if not any(char.isalpha() for char in cleaned):
        return False

    return True


def main(argv: list[str] | None = None) -> int:
    """Lee la solicitud, ejecuta el grafo e imprime las recomendaciones.
    El texto puede pasarse como argumento posicional o escribirse cuando el
    programa lo solicite. `--output` es opcional y guarda una copia del informe.
    """
    parser = argparse.ArgumentParser(
        description="Genera recomendaciones de libros a partir de tus preferencias."
    )
    parser.add_argument(
        "user_input",
        nargs="*",
        help="descripción de lo que te gustaría leer; si se omite, se solicita",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="ARCHIVO",
        help="guarda el informe formateado en la ruta indicada",
    )
    args = parser.parse_args(argv)

    # Unir las palabras permite aceptar el pedido entrecomillado o sin comillas.
    user_input = " ".join(args.user_input).strip()
    if not user_input:
        try:
            user_input = input("¿Qué te gustaría leer? ")
        except EOFError:
            parser.error("Ingresá un pedido como argumento o por entrada interactiva.")

    if not is_valid_input(user_input):
        parser.error("El pedido debe tener al menos tres caracteres y contener letras.")

    # Importar la aplicación solo después de procesar argumentos evita cargar dependencias del grafo al mostrar --help o rechazar una entrada inválida.
    from app.graph.graph import build_graph
    from app.graph.state import create_initial_state
    from app.models.book import FinalRecommendations
    from app.tools.recommendation_formatter import format_recommendations

    # Inicializar el grafo después de validar la entrada evita trabajo innecesario.
    graph = build_graph()
    result = graph.invoke(create_initial_state(user_input))

    recommendations_data = result.get("final_recommendations")
    if recommendations_data is None:
        raise RuntimeError(
            "El grafo terminó sin devolver el campo 'final_recommendations'."
        )

    final_recommendations = FinalRecommendations.model_validate(
        recommendations_data
    )

    # El formateador incluye tanto las recomendaciones como los estados de búsqueda comercial, evitando mantener dos presentaciones del mismo dato.
    formatted_output = format_recommendations(final_recommendations)
    print(formatted_output)

    if args.output is not None:
        args.output.write_text(formatted_output, encoding="utf-8")
        print(f"\nResultado guardado en: {args.output.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
