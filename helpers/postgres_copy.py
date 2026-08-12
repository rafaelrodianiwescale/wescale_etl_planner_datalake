import csv
import io


def psql_insert_copy(table, conn, keys, data_iter):
    dbapi_conn = conn.connection

    with dbapi_conn.cursor() as cur:
        s_buf = io.StringIO()

        writer = csv.writer(
            s_buf,
            quoting=csv.QUOTE_MINIMAL,
            quotechar='"',
            escapechar='\\',
            doublequote=False,
            lineterminator='\n'
        )

        writer.writerows(data_iter)
        s_buf.seek(0)

        columns = ", ".join([f'"{k}"' for k in keys])

        table_name = f'"{table.name}"'

        if table.schema:
            table_name = f'"{table.schema}"."{table.name}"'

        sql = f"""
            COPY {table_name} ({columns})
            FROM STDIN WITH CSV QUOTE '"' ESCAPE '\\'
        """

        cur.copy_expert(sql=sql, file=s_buf)
