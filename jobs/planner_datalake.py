import sys
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

# Graph API limita quantos valores cabem num filtro "id in (...)" por chamada
TAMANHO_LOTE_USUARIOS = 15


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


def fetch_plan_details(plan_id, headers) -> dict:
    """Traz categoryDescriptions: mapeia os slots (category1..category25) pros
    rotulos de texto configurados nesse plano especifico."""
    url = f"https://graph.microsoft.com/v1.0/planner/plans/{plan_id}/details"

    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()

    return response.json().get("categoryDescriptions", {}) or {}


def fetch_buckets(plan_id, headers):
    url = f"https://graph.microsoft.com/v1.0/planner/plans/{plan_id}/buckets"
    return graph_get_all(url, headers)


def fetch_tasks(plan_id, headers):
    url = f"https://graph.microsoft.com/v1.0/planner/plans/{plan_id}/tasks"
    return graph_get_all(url, headers)


def fetch_users(user_ids, headers) -> list:
    """Resolve id -> nome/email em lotes, via GET /users?$filter=id in (...).
    Requer a permissao de aplicativo User.Read.All no App Registration."""
    if not user_ids:
        return []

    usuarios = []
    ids_unicos = sorted(set(user_ids))

    for inicio in range(0, len(ids_unicos), TAMANHO_LOTE_USUARIOS):
        lote = ids_unicos[inicio:inicio + TAMANHO_LOTE_USUARIOS]
        filtro = " or ".join(f"id eq '{id_usuario}'" for id_usuario in lote)
        url = (
            "https://graph.microsoft.com/v1.0/users"
            f"?$filter={filtro}&$select=id,displayName,mail"
        )

        response = requests.get(url, headers=headers, timeout=60)
        response.raise_for_status()

        usuarios.extend(response.json().get("value", []))

    return usuarios


def coletar_ids_responsaveis(tasks_json) -> list:
    ids = set()

    for t in tasks_json:
        ids.update((t.get("assignments") or {}).keys())

    return sorted(ids)


# ==========================================
# TRANSFORMAÇÃO (tudo consolidado numa linha por tarefa)
# ==========================================

def converter_datetime(valor):
    if not valor:
        return None

    ts = pd.to_datetime(valor, errors="coerce", utc=True)

    if pd.isna(ts):
        return None

    return ts.to_pydatetime().replace(tzinfo=None)


def montar_df_planner(plan_json, buckets_json, tasks_json, category_descriptions: dict, nomes_por_id: dict) -> pd.DataFrame:
    buckets_dict = {b["id"]: b.get("name") for b in buckets_json}

    registros = []

    for t in tasks_json:
        ids_responsaveis = (t.get("assignments") or {}).keys()
        nomes_responsaveis = sorted(nomes_por_id.get(id_user, id_user) for id_user in ids_responsaveis)

        slots_categoria = [k for k, v in (t.get("appliedCategories") or {}).items() if v]
        rotulos_categoria = sorted(category_descriptions.get(slot, slot) for slot in slots_categoria)

        id_bucket = t.get("bucketId")

        registros.append({
            "id_task": t["id"],
            "id_plan": plan_json["id"],
            "plano": plan_json.get("title"),
            "id_bucket": id_bucket,
            "bucket": buckets_dict.get(id_bucket),
            "tarefa": t.get("title"),
            "percentual_concluido": t.get("percentComplete"),
            "prioridade": t.get("priority"),
            "dt_inicio": converter_datetime(t.get("startDateTime")),
            "dt_vencimento": converter_datetime(t.get("dueDateTime")),
            "dt_conclusao": converter_datetime(t.get("completedDateTime")),
            "dt_criacao": converter_datetime(t.get("createdDateTime")),
            "fl_tem_descricao": bool(t.get("hasDescription")),
            "qtd_checklist_ativos": t.get("activeCheckitemCount"),
            "qtd_checklist_total": t.get("totalCheckitemCount"),
            "responsaveis": ", ".join(nomes_responsaveis),
            "categorias": ", ".join(rotulos_categoria),
        })

    return pd.DataFrame(registros)


# ==========================================
# CARGA (TRUNCATE + INSERT numa unica tabela)
# ==========================================

def garantir_tabela(engine):
    log_info("Garantindo tabela microsoft_planners.")

    sql = f"""
    CREATE TABLE IF NOT EXISTS "{SCHEMA_DESTINO}".microsoft_planners (
        id_task               VARCHAR(64) PRIMARY KEY,
        id_plan               VARCHAR(64),
        plano                 VARCHAR(255),
        id_bucket             VARCHAR(64),
        bucket                VARCHAR(255),
        tarefa                VARCHAR(500),
        percentual_concluido  INT,
        prioridade             INT,
        dt_inicio              TIMESTAMP,
        dt_vencimento          TIMESTAMP,
        dt_conclusao           TIMESTAMP,
        dt_criacao             TIMESTAMP,
        fl_tem_descricao       BOOLEAN,
        qtd_checklist_ativos   INT,
        qtd_checklist_total    INT,
        responsaveis           TEXT,
        categorias             TEXT,
        dt_atualizacao_etl     TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS ix_microsoft_planners_id_plan   ON "{SCHEMA_DESTINO}".microsoft_planners(id_plan);
    CREATE INDEX IF NOT EXISTS ix_microsoft_planners_id_bucket ON "{SCHEMA_DESTINO}".microsoft_planners(id_bucket);
    """

    with engine.begin() as conn:
        conn.execute(text(sql))

    log_success("Tabela garantida com sucesso.")


def carregar_postgres(engine, df_final):
    log_info(f"Carregando {len(df_final)} tarefas (TRUNCATE + INSERT numa unica transacao).")

    with engine.begin() as conn:
        conn.execute(text(f'TRUNCATE TABLE "{SCHEMA_DESTINO}".microsoft_planners;'))

        if not df_final.empty:
            df_final.to_sql(
                name="microsoft_planners",
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

    log_info(f"{len(config['plan_ids'])} planner(s) configurados: {', '.join(config['plan_ids'])}.")

    dados_por_plano = []

    for plan_id in config["plan_ids"]:
        log_info(f"Buscando dados do planner {plan_id}.")

        plan_json = fetch_plan(plan_id, headers)
        buckets_json = fetch_buckets(plan_id, headers)
        tasks_json = fetch_tasks(plan_id, headers)
        category_descriptions = fetch_plan_details(plan_id, headers)

        log_info(
            f"Planner '{plan_json.get('title')}': {len(buckets_json)} buckets, "
            f"{len(tasks_json)} tarefas, {len(category_descriptions)} rotulos."
        )

        dados_por_plano.append((plan_json, buckets_json, tasks_json, category_descriptions))

    todas_tasks_json = [t for _, _, tasks_json, _ in dados_por_plano for t in tasks_json]

    log_info("Resolvendo nomes dos responsaveis pelas tarefas.")
    ids_responsaveis = coletar_ids_responsaveis(todas_tasks_json)

    try:
        users_json = fetch_users(ids_responsaveis, headers)
        nomes_por_id = {u["id"]: u.get("displayName") for u in users_json}
        log_info(f"{len(nomes_por_id)} de {len(ids_responsaveis)} responsaveis resolvidos.")
    except requests.exceptions.HTTPError as e:
        nomes_por_id = {}
        log_error(f"Falha ao resolver responsaveis (mantendo os IDs brutos): {e}")

    df_final = pd.concat(
        [
            montar_df_planner(plan_json, buckets_json, tasks_json, category_descriptions, nomes_por_id)
            for plan_json, buckets_json, tasks_json, category_descriptions in dados_por_plano
        ],
        ignore_index=True,
    )

    log_info(f"{len(df_final)} tarefas consolidadas no total.")

    garantir_tabela(engine)
    carregar_postgres(engine, df_final)

    log_success("Job finalizado: planner_datalake.py.")


if __name__ == "__main__":
    try:
        executar()
    except Exception as e:
        log_error(str(e))
        raise
