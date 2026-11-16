
"""Modelos para identificar obras y normalizar sus respuestas estructuradas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkIdentity(BaseModel):
    """Identidad de una obra asociada a un candidato del catálogo."""

    # Rechaza campos adicionales para detectar respuestas inesperadas del modelo.
    model_config = ConfigDict(extra="forbid")

    # candidate_id enlaza esta clasificación con el libro enviado al modelo;
    # group_id reúne ediciones o títulos alternativos de una misma obra.
    candidate_id: str = Field(
        description="Exact candidate_id supplied in the input."
    )
    group_id: str = Field(
        description="Shared identifier for the same underlying literary work."
    )
    work_type: Literal[
        "standalone", "collection", "adaptation", "uncertain"
    ] = Field(description="Classification of the candidate.")

    # En una colección, indica los candidatos que contiene; en otros casos suele estar vacío.
    included_works: list[str] = Field(
        description="Exact candidate IDs of included works. Empty if none."
    )

    # Confianza numérica y evidencia breve para justificar la clasificación.
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(
        description="Brief evidence supporting the classification."
    )


class WorkIdentityResults(BaseModel):
    """Respuesta con una identidad por candidato enviado al modelo."""

    # Evita aceptar propiedades que no forman parte del contrato de respuesta.
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def normalize_model_response(cls, data):
        # Algunos modelos devuelven directamente la lista en vez del objeto contenedor {"works": [...]} que solicita el esquema.
        if isinstance(data, list):
            data = {"works": data} #data = {"works": ["Libro 1", "Libro 2"]}

        if not isinstance(data, dict):
            return data #data = "texto cualquiera" No es ni lista ni diccionario, se devuelve tal cual para que falle la validación de Pydantic.

        # Copiar evita modificar el diccionario que entregó el proveedor.
        data = data.copy()

        # Acepta nombres alternativos de la lista para tolerar variaciones de formato sin relajar la validación de cada identidad.
        if "works" not in data:
            for alternative_key in ("results", "candidates"):
                if isinstance(data.get(alternative_key), list):
                    data["works"] = data.pop(alternative_key)
                    break

        # los "thoughts" de un llm, no forma parte del resultado que consume el grafo.
        data.pop("thoughts", None)

        if isinstance(data.get("works"), list):
            normalized = []

            for raw in data["works"]:
                if not isinstance(raw, dict):
                    normalized.append(raw)
                    continue

                item = raw.copy()

                # Convierte el nombre alternativo al campo canónico y descarta campos descriptivos que no pertenecen a WorkIdentity.
                if "work_type" not in item and "classification" in item:
                    item["work_type"] = item["classification"]

                item.pop("classification", None)
                item.pop("title", None)
                item.pop("author", None)
                item.setdefault("included_works", [])

                # Una clasificación desconocida queda explícitamente incierta; no se afirma una identidad que el modelo no pudo reconocer.
                if item.get("work_type") not in {
                    "standalone", "collection", "adaptation", "uncertain"
                }:
                    item["work_type"] = "uncertain"

                normalized.append(item)

            data["works"] = normalized

        return data

    # Cada elemento se valida contra WorkIdentity después de normalizar el envoltorio.
    works: list[WorkIdentity] = Field(
        description="One identity result for every supplied candidate."
    )


class ReferenceTitleTranslations(BaseModel):
    """Títulos originales y variantes traducidas de libros de referencia."""

    # La lista permite excluir una referencia aunque aparezca bajo otro título.
    model_config = ConfigDict(extra="forbid")

    titles: list[str] = Field(
        description="Original reference titles and confidently identified "
        "alternative Spanish or English titles."
    )
