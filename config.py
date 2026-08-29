import  os
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")


LIBSQL_URL = os.getenv("TURSO_DATABASE_URL") or os.getenv("LIBSQL_URL") or "https://monitor-db-jian1021.aws-ap-northeast-1.turso.io"
LIBSQL_TOKEN = os.getenv("TURSO_AUTH_TOKEN") or os.getenv("LIBSQL_TOKEN")