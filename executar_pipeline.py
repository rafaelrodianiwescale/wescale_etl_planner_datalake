import sys
import subprocess
from datetime import datetime
from pathlib import Path

from helpers.logger import log_info, log_success, log_error
from helpers.credentials import get_postgres_wescale_engine
from helpers.db_log import garantir_tabela_log, registrar_execucao


BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = BASE_DIR / "jobs"

PIPELINE_NOME = "wescale_planner_datalake"

JOBS = [
    JOBS_DIR / "planner_datalake.py",
]


def executar_pipeline():
    log_info("Início do pipeline Planner -> Data Lake.")

    engine = get_postgres_wescale_engine()
    garantir_tabela_log(engine)

    for job in JOBS:
        script_nome = job.name
        log_info(f"Iniciando job: {script_nome}")

        inicio = datetime.now()
        resultado = subprocess.run([sys.executable, str(job)])
        fim = datetime.now()

        if resultado.returncode != 0:
            log_error(f"Job falhou: {script_nome}")
            registrar_execucao(
                engine=engine,
                pipeline=PIPELINE_NOME,
                script=script_nome,
                inicio=inicio,
                fim=fim,
                status="falha",
                mensagem_erro=(
                    f"Falhou com código de saída {resultado.returncode}. "
                    f"Ver log do Airflow para detalhes."
                ),
            )
            raise RuntimeError(f"Pipeline interrompido no job: {script_nome}")

        log_success(f"Job finalizado: {script_nome}")
        registrar_execucao(
            engine=engine,
            pipeline=PIPELINE_NOME,
            script=script_nome,
            inicio=inicio,
            fim=fim,
            status="sucesso",
        )

    log_success("Pipeline Planner -> Data Lake finalizado com sucesso.")


if __name__ == "__main__":
    try:
        executar_pipeline()
    except Exception as e:
        log_error(str(e))
        raise
