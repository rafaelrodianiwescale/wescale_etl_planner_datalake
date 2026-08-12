# helpers/credentials.py

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine


BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)


def get_env_var(name: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.getenv(name, default)

    if required and not value:
        raise ValueError(
            f"Variável de ambiente obrigatória não encontrada: {name}. "
            f"Arquivo .env esperado em: {ENV_PATH}"
        )

    return value


def get_graph_config() -> dict:
    return {
        "tenant_id": get_env_var("AZURE_TENANT_ID"),
        "client_id": get_env_var("AZURE_CLIENT_ID"),
        "client_secret": get_env_var("AZURE_CLIENT_SECRET"),
        "plan_id": get_env_var("PLANNER_PLAN_ID"),
    }


def get_postgres_wescale_engine():
    host = get_env_var("POSTGRES_WESCALE_HOST")
    port = get_env_var("POSTGRES_WESCALE_PORT")
    database = get_env_var("POSTGRES_WESCALE_DATABASE")
    user = get_env_var("POSTGRES_WESCALE_USER")
    password = get_env_var("POSTGRES_WESCALE_PASSWORD")

    url = (
        f"postgresql+psycopg2://{user}:{password}"
        f"@{host}:{port}/{database}"
    )

    return create_engine(url)
