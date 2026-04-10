from contextlib import contextmanager

import mysql.connector
from mysql.connector import Error

from app.core.config import settings


def get_connection():
    return mysql.connector.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        autocommit=False,
    )


@contextmanager
def db_cursor(dictionary: bool = False):
    connection = get_connection()
    cursor = connection.cursor(dictionary=dictionary)
    try:
        yield connection, cursor
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def test_connection() -> bool:
    try:
        with db_cursor() as (_, cursor):
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Error:
        return False
