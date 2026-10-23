import os

import psycopg
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
CONNECT_TIMEOUT_SECONDS = 5


class DatabaseNotConfigured(RuntimeError):
    """DATABASE_URL is not set."""


def get_connection():
    if not DATABASE_URL:
        raise DatabaseNotConfigured("DATABASE_URL is not set.")
    # Fail fast when the database is unreachable instead of hanging the request.
    return psycopg.connect(DATABASE_URL, connect_timeout=CONNECT_TIMEOUT_SECONDS)
