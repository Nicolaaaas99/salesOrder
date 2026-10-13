import pyodbc
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import config

evo_connection_string = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={config.SERVER};"
    f"DATABASE={config.COMPANY_DATABASE};"
    f"UID={config.DB_USERNAME};"
    f"PWD={config.DB_PASSWORD};"
    f"TrustServerCertificate=yes;"
    f"Connection Timeout=5;"
)

engine = create_engine(
    "mssql+pyodbc://",
    creator=lambda: pyodbc.connect(evo_connection_string),
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
