# ETL Planner -> Data Lake

Extrai plano, buckets e tarefas de um ou mais Microsoft Planners (via Microsoft Graph API),
resolve nomes de responsáveis e rótulos de categorias, e carrega tudo já consolidado (uma linha
por tarefa) no Data Lake PostgreSQL (`wescale`, host `10.10.2.186`), na tabela `microsoft_planners`.

Roda no Airflow (servidor `10.10.0.14`), não mais via Agendador de Tarefas do Windows.

## Estrutura

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
├── .env                       # Credenciais (Azure AD, PostgreSQL) + lista de planners
└── requirements.txt
```

## 1. Criar o App Registration no Azure AD

O script autentica na Microsoft Graph API via **OAuth 2.0 Client Credentials** (aplicativo, sem
usuário logado), então é preciso um **App Registration** com permissões de aplicativo:

1. Portal do Azure → **Azure Active Directory** → **App registrations** → **New registration**.
2. Após criado, em **Certificates & secrets** → **New client secret** → copie o valor gerado
   (só aparece uma vez) para `AZURE_CLIENT_SECRET` no `.env`. O **Application (client) ID** vai em
   `AZURE_CLIENT_ID`.
3. Em **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**
   → adicione `Tasks.Read.All` (leitura de todos os planos do Planner na organização) e
   `User.Read.All` (resolução de nomes dos responsáveis — precisa ser **Application**, não
   Delegated, senão a chamada falha com 403 mesmo com consent concedido).
4. Clique em **Grant admin consent for [tenant]** — sem isso a chamada retorna 403.

## 2. Adicionar ou remover planners rastreados

A lista de planners fica em `PLANNER_PLAN_IDS` no `.env`, separados por vírgula:

```
PLANNER_PLAN_IDS=5ngJnJVlNEuZp8OT9qVZ52UAAW6g,outroPlanoId,maisUmPlanoId
```

Pra incluir ou remover um planner, basta editar essa lista (local e no servidor) e rodar a DAG de
novo — não precisa alterar código.

## 3. Tabela no banco

`microsoft_planners` é criada automaticamente pelo próprio job (`garantir_tabela()` em
`jobs/planner_datalake.py`) na primeira execução — não precisa rodar DDL à parte. Uma linha por
tarefa, já com plano, bucket, responsáveis (nomes separados por vírgula) e categorias (rótulos
separados por vírgula) resolvidos — sem necessidade de relacionamento adicional no Power BI.

## 4. Estratégia de carga

A cada execução, o job faz **TRUNCATE + INSERT** na tabela, dentro de uma única transação: apaga o
snapshot anterior e grava o estado atual de todos os planners configurados. Isso reflete fielmente
o que existe no Planner a cada rodada, mas **não preserva histórico** de tarefas excluídas. Se no
futuro for necessário manter histórico, trocar por upsert (`INSERT ... ON CONFLICT DO UPDATE`) em
vez de truncar.

## 5. Orquestração (Airflow)

DAG `wescale_planner_datalake_diario`, uma tarefa (`atualizar_dados`), 1x por dia.

## Observações

- Se `User.Read.All` (Application) ainda não tiver sido concedido, a resolução de responsáveis
  falha com 403 — o job não trava por causa disso, só grava os IDs brutos em `responsaveis` até a
  permissão ser corrigida.
- Log de cada execução fica na tabela `log_execucao_pipeline` do Postgres (`wescale`) e nos logs do
  Airflow — não usa mais a tabela `log_execuxao_alarme_teams` nem alerta via Teams (esses eram do
  padrão antigo, baseado em Agendador de Tarefas).
