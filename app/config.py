"""Carga las credenciales de servicios externos desde el entorno del proyecto.
Este módulo centraliza las claves para que los componentes las lean desde un
solo lugar. Las variables pueden provenir del entorno del sistema o del `.env`.
"""

import os
from dotenv import load_dotenv

# Busca un archivo `.env` según las reglas de python-dotenv y carga sus variables en `os.environ` sin reemplazar las que ya estén definidas.
load_dotenv()


# Las claves se dejan como `None` cuando no están configuradas; cada integración decide si su proveedor es opcional o si debe informar un error al utilizarlo.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EXA_API_KEY = os.getenv("EXA_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")