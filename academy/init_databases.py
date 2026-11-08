"""Job no EKS: cria tabelas ausentes e registra a chave usada entre servicos."""
import hashlib
import os
from pathlib import Path
import sys
import psycopg2
from psycopg2 import sql
from urllib.parse import urlsplit, urlunsplit, unquote


def main():
    for name in ("auth", "flag", "targeting"):
        database_url = os.environ[name.upper() + "_URL"]
        parts = urlsplit(database_url)
        database = unquote(parts.path.strip("/"))
        admin_url = urlunsplit(parts._replace(path="/postgres"))
        # CREATE DATABASE nao pode executar dentro de transacao.
        admin = psycopg2.connect(admin_url, connect_timeout=15)
        try:
            admin.autocommit = True
            with admin.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
                if cursor.fetchone() is None:
                    cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        finally:
            admin.close()
        with psycopg2.connect(database_url, connect_timeout=15) as connection:
            with connection.cursor() as cursor:
                cursor.execute((Path("/bootstrap") / (name + "-service.sql")).read_text())
                if name == "auth":
                    digest = hashlib.sha256(os.environ["SERVICE_API_KEY"].encode()).hexdigest()
                    cursor.execute("INSERT INTO api_keys (name, key_hash, is_active) VALUES (%s, %s, true) "
                                   "ON CONFLICT (key_hash) DO NOTHING", ("academy-services", digest))
        print(name + ": schema preparado; dados existentes preservados.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # libpq pode incluir host/usuario/senha na mensagem: nao reproduzir.
        print("Inicializacao RDS falhou: " + type(exc).__name__ + ". Confira senha, schemas e acesso de rede.", file=sys.stderr)
        sys.exit(1)
