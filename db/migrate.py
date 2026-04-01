"""Run once to create tables. Safe to re-run (uses IF NOT EXISTS)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv()
import pymysql
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
with open(schema_path, encoding="utf-8") as f:
    sql = f.read()

conn = pymysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                       password=DB_PASSWORD, charset="utf8mb4")
cursor = conn.cursor()
for stmt in sql.split(";"):
    stmt = stmt.strip()
    if stmt:
        cursor.execute(stmt)
conn.commit()
cursor.close()
conn.close()
print("Migration complete.")
