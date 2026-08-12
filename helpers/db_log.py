from datetime import datetime

from sqlalchemy import text


def garantir_tabela_log(engine) -> None:
    sql = """
        CREATE TABLE IF NOT EXISTS public.log_execucao_pipeline (
            id SERIAL PRIMARY KEY,
            pipeline TEXT NOT NULL,
            script TEXT NOT NULL,
            inicio TIMESTAMP NOT NULL,
            fim TIMESTAMP NOT NULL,
            duracao_segundos INTEGER,
            status TEXT NOT NULL,
            mensagem_erro TEXT
        );
    """

    with engine.begin() as conn:
        conn.execute(text(sql))


def registrar_execucao(
    engine,
    pipeline: str,
    script: str,
    inicio: datetime,
    fim: datetime,
    status: str,
    mensagem_erro: str | None = None,
) -> None:
    sql = text("""
        INSERT INTO public.log_execucao_pipeline (
            pipeline, script, inicio, fim, duracao_segundos, status, mensagem_erro
        )
        VALUES (
            :pipeline, :script, :inicio, :fim, :duracao_segundos, :status, :mensagem_erro
        )
    """)

    duracao_segundos = int((fim - inicio).total_seconds())

    with engine.begin() as conn:
        conn.execute(sql, {
            "pipeline": pipeline,
            "script": script,
            "inicio": inicio,
            "fim": fim,
            "duracao_segundos": duracao_segundos,
            "status": status,
            "mensagem_erro": mensagem_erro,
        })
