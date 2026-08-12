import sys
import json
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import text


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BASE_DIR))

from helpers.credentials import get_graph_config, get_postgres_wescale_engine
from helpers.logger import log_info, log_success, log_error
from helpers.postgres_copy import psql_insert_copy


SCHEMA_DESTINO = "public"


# ==========================================
# EXTRAÇÃO (Microsoft Graph API - Planner)
# ==========================================

def get_access_token(config: dict) -> str:
    url = f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token"

    payload = {
        "grant_type": "client_credentials",
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "scope": "https://graph.microsoft.com/.default",
    }

    response = requests.post(url, data=payload, timeout=60)
    response.raise_for_status()

    return response.json()["access_token"]


def graph_get_all(url, headers):
    """Segue a paginação (@odata.nextLink) do Graph API e retorna todos os itens."""
    items = []

    while url:
        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()

        data = response.json()
        items.extend(data.get("value", []))
        url = data.get("@odata.nextLink")

    return items


def fetch_plan(plan_id, headers):
    url = f"https://graph.microsoft.com/v1.0/planner/plans/{plan_id}"

    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()

    return response.json()


def fetch_buckets(plan_id, headers):
    url = f"https://graph.microsoft.com/v1.0/planner/plans/{plan_id}/buckets"
    return graph_get_all(url, headers)


def fetch_tasks(plan_id, headers):
    url = f"https://graph.microsoft.com/v1.0/planner/plans/{plan_id}/tasks"
    return graph_get_all(url, headers)


# ==========================================
# TRANSFORMAÇÃO
# ==========================================

def converter_datetime(valor):
    if not valor:
        return None

    ts = pd.to_datetime(valor, errors="coerce", utc=True)

    if pd.isna(ts):
        return None

    return ts.to_pydatetime().replace(tzinfo=None)


def montar_df_plan(plan_json) -> pd.DataFrame:
    return pd.DataFrame([{
        "id_plan": plan_json["id"],
        "titulo": plan_json.get("title"),
        "dt_criacao": converter_datetime(plan_json.get("createdDateTime")),
    }])


def montar_df_buckets(buckets_json) -> pd.DataFrame:
    registros = [
        {
            "id_bucket": b["id"],
            "id_plan": b.get("planId"),
            "nome": b.get("name"),
        }
        for b in buckets_json
    ]

    return pd.DataFrame(registros)


def montar_df_tasks(tasks_json) -> pd.DataFrame:
    registros = []

    for t in tasks_json:
        responsaveis = list((t.get("assignments") or {}).keys())
        categorias = [k for k, v in (t.get("appliedCategories") or {}).items() if v]

        registros.append({
            "id_task": t["id"],
            "titulo": t.get("title"),
            "id_plan": t.get("planId"),
            "id_bucket": t.get("bucketId"),
            "percentual_concluido": t.get("percentComplete"),
            "prioridade": t.get("priority"),
            "dt_inicio": converter_datetime(t.get("startDateTime")),
            "dt_vencimento": converter_datetime(t.get("dueDateTime")),
            "dt_conclusao": converter_datetime(t.get("completedDateTime")),
            "dt_criacao": converter_datetime(t.get("createdDateTime")),
            "fl_tem_descricao": bool(t.get("hasDescription")),
            "qtd_checklist_ativos": t.get("activeCheckitemCount"),
            "qtd_checklist_total": t.get("totalCheckitemCount"),
            "responsaveis": json.dumps(responsaveis),
            "categorias": json.dumps(categorias),
        })

    return pd.DataFrame(registros)


# ==========================================
# CARGA (TRUNCATE + INSERT nas 3 tabelas, numa unica transacao)
# ==========================================

def garantir_tabelas(engine):
    log_info("Garantindo tabelas planner_plans, planner_buckets e planner_tasks.")

    sql = f"""
    CREATE TABLE IF NOT EXISTS "{SCHEMA_DESTINO}".planner_plans (
        id_plan             VARCHAR(64) PRIMARY KEY,
        titulo              VARCHAR(255),
        dt_criacao          TIMESTAMP,
        dt_atualizacao_etl  TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS "{SCHEMA_DESTINO}".planner_buckets (
        id_bucket           VARCHAR(64) PRIMARY KEY,
        id_plan             VARCHAR(64) REFERENCES "{SCHEMA_DESTINO}".planner_plans(id_plan) ON DELETE CASCADE,
        nome                VARCHAR(255),
        dt_atualizacao_etl  TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS "{SCHEMA_DESTINO}".planner_tasks (
        id_task                 VARCHAR(64) PRIMARY KEY,
        titulo                  VARCHAR(500),
        id_plan                 VARCHAR(64) REFERENCES "{SCHEMA_DESTINO}".planner_plans(id_plan) ON DELETE CASCADE,
        id_bucket                VARCHAR(64) REFERENCES "{SCHEMA_DESTINO}".planner_buckets(id_bucket) ON DELETE SET NULL,
        percentual_concluido    INT,
        prioridade              INT,
        dt_inicio               TIMESTAMP,
        dt_vencimento           TIMESTAMP,
        dt_conclusao            TIMESTAMP,
        dt_criacao              TIMESTAMP,
        fl_tem_descricao        BOOLEAN,
        qtd_checklist_ativos    INT,
        qtd_checklist_total     INT,
        responsaveis            JSON,
        categorias              JSON,
        dt_atualizacao_etl      TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS ix_planner_tasks_id_plan   ON "{SCHEMA_DESTINO}".planner_tasks(id_plan);
    CREATE INDEX IF NOT EXISTS ix_planner_tasks_id_bucket ON "{SCHEMA_DESTINO}".planner_tasks(id_bucket);
    """

    with engine.begin() as conn:
        conn.execute(text(sql))

    log_success("Tabelas garantidas com sucesso.")


def carregar_postgres(engine, df_plan, df_buckets, df_tasks):
    log_info(
        f"Carregando 1 plano, {len(df_buckets)} buckets e {len(df_tasks)} tarefas "
        f"(TRUNCATE + INSERT numa unica transacao)."
    )

    with engine.begin() as conn:
        conn.execute(text(
            f'TRUNCATE TABLE "{SCHEMA_DESTINO}".planner_tasks, '
            f'"{SCHEMA_DESTINO}".planner_buckets, '
            f'"{SCHEMA_DESTINO}".planner_plans RESTART IDENTITY CASCADE;'
        ))

        df_plan.to_sql(
            name="planner_plans",
            schema=SCHEMA_DESTINO,
            con=conn,
            if_exists="append",
            index=False,
            method=psql_insert_copy,
        )

        if not df_buckets.empty:
            df_buckets.to_sql(
                name="planner_buckets",
                schema=SCHEMA_DESTINO,
                con=conn,
                if_exists="append",
                index=False,
                method=psql_insert_copy,
            )

        if not df_tasks.empty:
            df_tasks.to_sql(
                name="planner_tasks",
                schema=SCHEMA_DESTINO,
                con=conn,
                if_exists="append",
                index=False,
                method=psql_insert_copy,
            )

    log_success("Carga realizada com sucesso.")


def executar():
    log_info("Início do job: planner_datalake.py.")

    config = get_graph_config()
    engine = get_postgres_wescale_engine()

    log_info("Solicitando token de acesso ao Microsoft Graph.")
    token = get_access_token(config)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    log_info("Buscando dados do plano.")
    plan_json = fetch_plan(config["plan_id"], headers)
    df_plan = montar_df_plan(plan_json)

    log_info("Buscando buckets do plano.")
    buckets_json = fetch_buckets(config["plan_id"], headers)
    df_buckets = montar_df_buckets(buckets_json)
    log_info(f"{len(df_buckets)} buckets encontrados.")

    log_info("Buscando tarefas do plano.")
    tasks_json = fetch_tasks(config["plan_id"], headers)
    df_tasks = montar_df_tasks(tasks_json)
    log_info(f"{len(df_tasks)} tarefas encontradas.")

    garantir_tabelas(engine)
    carregar_postgres(engine, df_plan, df_buckets, df_tasks)

    log_success("Job finalizado: planner_datalake.py.")


if __name__ == "__main__":
    try:
        executar()
    except Exception as e:
        log_error(str(e))
        raise
