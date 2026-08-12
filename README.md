# ETL Planner -> Data Lake

Extrai o plano, os buckets e as tarefas do Microsoft Planner (via Microsoft Graph API) e carrega no
Data Lake PostgreSQL (`wescale`, host `10.10.2.186`), em três tabelas com DDL fixo (`CREATE TABLE
IF NOT EXISTS`, não usa `to_sql(if_exists='replace')`, para preservar tipos, chaves e índices entre
execuções).

Roda no Airflow (servidor `10.10.0.14`), não mais via Agendador de Tarefas do Windows.

## 📁 Estrutura

```
etl_planner_datalake/
├── executar_pipeline.py       # Orquestrador: roda os jobs e registra em log_execucao_pipeline
├── jobs/
│   └── planner_datalake.py    # Extração (Graph API), transformação e carga
├── helpers/
│   ├── credentials.py         # Config do Graph API + engine do Postgres (wescale)
│   ├── logger.py              # log_info / log_success / log_error
│   ├── db_log.py              # Log de execução em tabela Postgres (log_execucao_pipeline)
│   └── postgres_copy.py       # Bulk insert via COPY
├── .env                       # Credenciais (Azure AD, PostgreSQL)
└── requirements.txt
```

## 🔑 1. Criar o App Registration no Azure AD

O script autentica na Microsoft Graph API via **OAuth 2.0 Client Credentials** (aplicativo, sem
usuário logado), então é preciso um **App Registration** com permissão de aplicativo para o Planner:

1. Portal do Azure → **Azure Active Directory** → **App registrations** → **New registration**.
2. Após criado, em **Certificates & secrets** → **New client secret** → copie o valor gerado
   (só aparece uma vez) para `AZURE_CLIENT_SECRET` no `.env`. O **Application (client) ID** vai em
   `AZURE_CLIENT_ID`.
3. Em **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**
   → adicione `Tasks.Read.All` (leitura de todos os planos do Planner na organização).
4. Clique em **Grant admin consent for [tenant]** — sem isso a chamada retorna 403.

`AZURE_TENANT_ID` e `PLANNER_PLAN_ID` já estão preenchidos no `.env`. **`AZURE_CLIENT_ID` e
`AZURE_CLIENT_SECRET` ainda estão vazios** — o ETL não autentica até esses dois passos acima serem
concluídos e os valores preenchidos no `.env` (local e no servidor).

## 🗄️ 2. Tabelas no banco

`planner_plans`, `planner_buckets` e `planner_tasks` são criadas automaticamente pelo próprio job
(`garantir_tabelas()` em `jobs/planner_datalake.py`) na primeira execução — não precisa rodar DDL
à parte.

## ⚙️ 3. Estratégia de carga

A cada execução, o job faz **TRUNCATE + INSERT** nas três tabelas, dentro de uma única transação:
apaga o snapshot anterior e grava o estado atual do plano/buckets/tarefas. Isso reflete fielmente
o que existe no Planner a cada rodada, mas **não preserva histórico** de tarefas excluídas. Se no
futuro for necessário manter histórico, trocar por upsert (`INSERT ... ON CONFLICT DO UPDATE`) em
vez de truncar.

## 🕑 4. Orquestração (Airflow)

DAG `wescale_planner_datalake_diario`, uma tarefa (`atualizar_dados`), 1x por dia.

## 📝 Observações

- `responsaveis` e `categorias` em `planner_tasks` são gravados como JSON (listas de IDs), pois o
  Graph API retorna esses campos como dicionários; para nomes legíveis de responsáveis seria
  necessário consultar `/users/{id}` adicionalmente (fora do escopo inicial).
- Log de cada execução fica na tabela `log_execucao_pipeline` do Postgres (`wescale`) e nos logs do
  Airflow — não usa mais a tabela `log_execuxao_alarme_teams` nem alerta via Teams (esses eram do
  padrão antigo, baseado em Agendador de Tarefas).
