"""Cliente MCP por stdio para invocar la búsqueda desde el grafo."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters


# La raíz se usa para que el proceso hijo pueda importar el paquete app.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _child_environment() -> dict[str, str]:
    """Prepara el entorno mínimo que necesita el proceso MCP hijo."""
    # El servidor arranca en otro proceso: se le indica dónde está el proyecto y se fuerza UTF-8 para que las tramas stdio no dependan de la consola de Windows.
    environment = {
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONIOENCODING": "utf-8",
    }
    # Solo se reenvían las credenciales que usan las búsquedas comerciales.
    for name in (
        "MERCADOLIBRE_ACCESS_TOKEN",
        "EXA_API_KEY",
        "TAVILY_API_KEY",
    ):
        value = os.getenv(name)
        if value:
            environment[name] = value
    return environment


async def _call_server(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Inicia el servidor, invoca una herramienta y normaliza su respuesta."""
    # Ejecuta el servidor con el mismo intérprete Python y el módulo MCP del proyecto; Client administra el ciclo de vida de esa conexión.
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.availability_server"],
        env=_child_environment(),
    )
    async with Client(server) as client:
        response = await client.call_tool(tool_name, arguments)

    # Los errores del protocolo se convierten en excepciones con el detalle textual que envió el servidor, para que el grafo pueda registrarlos.
    if response.is_error:
        details = [
            item.text
            for item in (response.content or [])
            if getattr(item, "text", None)
        ]
        raise RuntimeError(
            "Availability MCP devolvió un error: "
            + ("; ".join(details) if details else "sin detalle")
        )

    # Primero se intenta leer el contenido estructurado, que es el formato preferido cuando el servidor MCP lo devuelve.
    data = response.structured_content
    if isinstance(data, dict):
        if "offers" in data and "statuses" in data:
            return data
        nested = data.get("result")
        if isinstance(nested, dict) and "offers" in nested:
            return nested

    # Algunas respuestas MCP llegan como texto JSON; intenta interpretarlas como alternativa al contenido estructurado.
    for item in response.content or []:
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            # Un bloque de texto que no sea JSON no impide revisar los demás.
            continue
        if isinstance(data, dict):
            if "offers" in data and "statuses" in data:
                return data
            nested = data.get("result")
            if isinstance(nested, dict) and "offers" in nested:
                return nested

    # No devolver una estructura vacía: el llamador debe saber que la respuesta no tenía el formato de ofertas y estados que necesita.
    raise RuntimeError(
        "Availability MCP devolvió una respuesta sin ofertas ni estados."
    )


def call_mcp_tool(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Permite invocar MCP tanto desde código síncrono como asíncrono."""
    # Sin un event loop activo, asyncio.run puede gestionar la llamada completa.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call_server(tool_name, arguments))

    # Si el host ya tiene un event loop, lo ejecuta en un hilo separado para no intentar anidar asyncio.run() dentro del loop existente.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(
            asyncio.run,
            _call_server(tool_name, arguments),
        ).result()
