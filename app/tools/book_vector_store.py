
"""Almacena, reutiliza y busca investigaciones de libros en ChromaDB.
La coincidencia exacta por título y autor sirve como caché; la búsqueda por
embeddings es una operación semántica distinta y se usa solo cuando se pide.
"""

import json
import re
import unicodedata

import chromadb
from langchain_huggingface import HuggingFaceEmbeddings

from app.models.book import BookResearch


# Ruta persistente y nombres de las colecciones. La colección anterior se deja
# disponible para lecturas y migración; las nuevas escrituras van a la local.
CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "book_research"
LOCAL_COLLECTION_NAME = "book_research_local"
MIGRATION_BATCH_SIZE = 50


# Indexar y consultar con el mismo modelo mantiene los vectores en el mismo espacio semántico. CPU evita depender de una GPU para esta función local.
embedding_model = HuggingFaceEmbeddings(
    model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

# La colección histórica se conserva como fuente de datos; la colección local usa distancia coseno para comparar los embeddings normalizados.
legacy_collection = chroma_client.get_or_create_collection(
    name=COLLECTION_NAME
)

collection = chroma_client.get_or_create_collection(
    name=LOCAL_COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"},
)


def normalize_text(value: str) -> str:
    """Prepara título y autor para una comparación exacta tolerante a formato.
    Se eliminan diacríticos y puntuación, pero se mantienen las palabras y
    números que distinguen obras diferentes.
    """
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return " ".join(re.findall(r"\w+", normalized.casefold()))


def _book_id(title: str, author: str) -> str:
    """Forma un ID estable que permite actualizar la misma obra al reindexarla."""
    normalized_title = normalize_text(title).replace(" ", "_")
    normalized_author = normalize_text(author).replace(" ", "_")
    return f"{normalized_title}_{normalized_author}"


def _research_has_content(book: BookResearch) -> bool:
    """Comprueba si la ficha tiene información que pueda recuperarse después.
    Fuentes por sí solas no bastan: se requiere al menos un campo descriptivo
    con contenido para evitar llenar la colección con resultados vacíos.
    """
    return bool(
        book.synopsis.strip()
        or book.themes
        or book.tone
        or book.style
        or (book.critical_reception or "").strip()
    )


def _parse_legacy_document(
    document: str,
    metadata: dict,
) -> BookResearch | None:
    """
    Convierte documentos históricos con etiquetas de texto al modelo actual.
    El formato viejo no guardaba listas como JSON, por eso se separan sus
    campos de texto y se validan antes de devolverlos.
    """
    fields = {}

    # El formato antiguo era texto plano, con un campo por línea; extraer solo las etiquetas conocidas y normalizarlas a minúsculas.
    for line in document.splitlines():
        match = re.match(
            r"^(Title|Author|Synopsis|Themes|Tone|Style|"
            r"Critical reception|Sources):\s*(.*)$",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            fields[match.group(1).casefold()] = match.group(2).strip()

    # Los metadatos de Chroma son preferibles; el texto actúa como respaldo cuando el registro histórico no guardó esos valores en metadatos.
    title = metadata.get("title") or fields.get("title")
    author = metadata.get("author") or fields.get("author")

    if not title or not author:
        return None

    try:
        return BookResearch(
            title=title,
            author=author,
            synopsis=fields.get("synopsis", ""),
            # El esquema nuevo usa listas; el formato anterior las guardaba como texto separado por comas.
            themes=[
                value.strip()
                for value in fields.get("themes", "").split(",")
                if value.strip()
            ],
            tone=[
                value.strip()
                for value in fields.get("tone", "").split(",")
                if value.strip()
            ],
            style=[
                value.strip()
                for value in fields.get("style", "").split(",")
                if value.strip()
            ],
            critical_reception=(
                fields.get("critical reception") or None
            ),
            sources=[
                value.strip()
                for value in fields.get("sources", "").split(",")
                if value.strip()
            ],
        )
    except (TypeError, ValueError):
        return None


def _parse_stored_document(
    document: str,
    metadata: dict,
) -> BookResearch | None:
    """Convierte una cadena almacenada en el modelo de investigación actual.
    Primero intenta JSON y validación Pydantic; si el documento no corresponde
    a ese formato, delega en el lector del esquema histórico.
    """
    try:
        data = json.loads(document)

        # La presencia de `synopsis` distingue el documento JSON nuevo de otras estructuras que podrían parsearse como JSON pero no son una investigación.
        if isinstance(data, dict) and "synopsis" in data:
            research = BookResearch.model_validate(data)

            if not research.title:
                research.title = metadata.get("title", "")
            if not research.author:
                research.author = metadata.get("author", "")

            return research
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Un JSON inválido o de otro esquema todavía puede ser texto histórico.
    return _parse_legacy_document(document, metadata)


def migrate_existing_research() -> None:
    """
    Copia registros históricos a la colección local.
    Es una operación explícita: no se ejecuta automáticamente
    al importar este módulo.
    """
    # Procesar en lotes limita la memoria y permite migrar colecciones grandes.
    total = legacy_collection.count()

    # `offset` recorre la colección antigua en bloques; cada bloque se migra de manera independiente para no cargar todos sus documentos a la vez.
    for offset in range(0, total, MIGRATION_BATCH_SIZE):
        batch = legacy_collection.get(
            limit=MIGRATION_BATCH_SIZE,
            offset=offset,
            include=["documents", "metadatas"],
        )

        ids = batch.get("ids") or []
        documents = batch.get("documents") or []
        metadatas = batch.get("metadatas") or []

        if not ids:
            continue

        # No volver a insertar registros ya copiados a la colección local.
        existing_ids = set(
            collection.get(ids=ids, include=[]).get("ids", [])
        )

        # Preparar solo los registros que tienen texto y aún no están copiados.
        missing = [
            (record_id, document, metadata or {})
            for record_id, document, metadata
            in zip(ids, documents, metadatas)
            if document and record_id not in existing_ids
        ]

        if not missing:
            continue

        new_ids = [item[0] for item in missing]
        new_documents = [item[1] for item in missing]
        new_metadatas = []

        # Completar metadatos normalizados para que las búsquedas exactas puedan usar el mismo filtro que aplican los registros nuevos.
        for _, document, metadata in missing:
            updated_metadata = dict(metadata)

            title = updated_metadata.get("title", "")
            author = updated_metadata.get("author", "")

            updated_metadata["title_normalized"] = normalize_text(title)
            updated_metadata["author_normalized"] = normalize_text(author)

            new_metadatas.append(updated_metadata)

        # Calcular vectores y guardarlos junto con IDs, texto y metadatos del lote.
        embeddings = embedding_model.embed_documents(new_documents)

        collection.upsert(
            ids=new_ids,
            documents=new_documents,
            metadatas=new_metadatas,
            embeddings=embeddings,
        )


def store_book_research(research: list[BookResearch]) -> None:
    """Persiste las investigaciones útiles como JSON más embedding semántico.
    El ID depende del título y autor; una nueva investigación de esa misma obra
    actualiza la ficha existente en lugar de crear una copia adicional.
    """
    if not research:
        return

    # Preparar documentos y metadatos juntos para mantener sus IDs alineados.
    records = {}

    for book in research:
        if not _research_has_content(book):
            continue

        # Guardar también formas normalizadas permite filtrar con Chroma antes de volver a comprobar título y autor sobre el documento recuperado.
        normalized_title = normalize_text(book.title)
        normalized_author = normalize_text(book.author)

        record_id = _book_id(book.title, book.author)

        # JSON conserva listas y textos sin perder información
        # por separadores como las comas.
        document = json.dumps(
            book.model_dump(mode="json"),
            ensure_ascii=False,
        )

        records[record_id] = {
            "document": document,
            "metadata": {
                "title": book.title,
                "author": book.author,
                "title_normalized": normalized_title,
                "author_normalized": normalized_author,
                "schema_version": 2,
            },
        }

    if not records:
        return

    # Construir las tres listas desde la misma secuencia de IDs conserva la correspondencia entre cada vector, documento y metadatos.
    ids = list(records)
    documents = [records[key]["document"] for key in ids]
    metadatas = [records[key]["metadata"] for key in ids]

    # Calcular un vector por documento para que Chroma pueda indexar la colección.
    embeddings = embedding_model.embed_documents(documents)

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )

def get_stored_book_research(
    title: str,
    author: str,
) -> list[dict]:
    """Busca coincidencias exactas en las colecciones actual e histórica.
    El filtro de metadatos reduce candidatos, pero luego se vuelve a comparar
    la identidad normalizada para no aceptar coincidencias aproximadas.
    """
    target_title = normalize_text(title)
    target_author = normalize_text(author)

    matches = []
    seen_documents = set()

    # Consultar primero la colección actual y después la histórica.
    for source_collection in (collection, legacy_collection):
        try:
            # La colección vieja puede no tener el índice de metadatos nuevo; un fallo de esa lectura no debe impedir consultar la otra colección.
            result = source_collection.get(
                where={
                    "$and": [
                        {"title_normalized": target_title},
                        {"author_normalized": target_author},
                    ]
                },
                include=["documents", "metadatas"],
            )
        except Exception:
            # La colección histórica puede no tener metadatos normalizados.
            continue

        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []

        for record_id, document, metadata in zip(
            ids, documents, metadatas
        ):
            if not document:
                continue

            metadata = metadata or {}
            stored_title = metadata.get("title", "")
            stored_author = metadata.get("author", "")

            if (
                normalize_text(stored_title) != target_title
                or normalize_text(stored_author) != target_author
            ):
                continue

            # Puede haber copias idénticas entre ambas colecciones; devolver una.
            document_key = (target_title, target_author, document)
            if document_key in seen_documents:
                continue

            seen_documents.add(document_key)
            matches.append({
                "document": document,
                "metadata": metadata,
                "id": record_id,
            })

    return matches


def get_cached_book_research(
    title: str,
    author: str,
) -> BookResearch | None:
    """Devuelve una ficha cacheada solo si su identidad y contenido son válidos.
    La verificación posterior al parseo protege frente a metadatos incompletos
    o documentos antiguos cuyo título/autor no corresponden al candidato.
    """
    records = get_stored_book_research(title, author)

    for record in records:
        research = _parse_stored_document(
            record["document"],
            record["metadata"],
        )

        if research is None:
            continue

        # Verificar la identidad antes de aceptar el registro.
        if (
            normalize_text(research.title) != normalize_text(title)
            or normalize_text(research.author) != normalize_text(author)
        ):
            continue

        if _research_has_content(research):
            # Usar los datos originales del candidato en la salida.
            research.title = title
            research.author = author
            return research

    return None


def search_book_research(
    query: str,
    n_results: int = 3,
    allowed_titles: list[str] | None = None,
) -> list[dict]:
    """Busca investigaciones por similitud semántica dentro de la colección local.
    Esta ruta no se usa para confirmar que una ficha pertenece exactamente a
    un título/autor; `allowed_titles`, cuando se entrega, limita la búsqueda.
    """
    total = collection.count()

    # Evitar pedir embeddings o consultar Chroma si no hay datos o una consulta útil.
    if total == 0 or n_results <= 0 or not query.strip():
        return []

    # Chroma compara el embedding de la consulta con los documentos indexados.
    query_args = {
        "query_embeddings": [embedding_model.embed_query(query)],
        "n_results": min(n_results, total),
        "include": ["documents", "metadatas", "distances"],
    }

    # Restringir por títulos conocidos cuando el llamador solo acepta un conjunto controlado de candidatos, aunque la consulta sea semántica.
    if allowed_titles:
        query_args["where"] = {
            "title": {"$in": allowed_titles}
        }

    results = collection.query(**query_args)

    documents = results.get("documents")
    metadatas = results.get("metadatas")
    distances = results.get("distances")

    if not documents or not documents[0]:
        return []

    # Mantener distancia y metadatos junto al documento permite al llamador interpretar la similitud sin perder la referencia original.
    return [
        {
            "document": document,
            "metadata": metadatas[0][index],
            "distance": distances[0][index],
        }
        for index, document in enumerate(documents[0])
    ]
