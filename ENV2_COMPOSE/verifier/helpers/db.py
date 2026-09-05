"""Thin DB connection helpers for the four datastore kinds the verifiers
touch: payouts/fts/x-balances MySQL, ledger Postgres, cfa Mongo, and Redis
(reservation store / idempotency mutex inspection).

Every connect_* function raises ConnectionUnavailable (never a raw driver
exception) on failure, so conftest.py fixtures can catch exactly one
exception type and pytest.skip() with a clear, fixture-naming reason -- per
the task brief's "tolerant of missing fixtures" requirement.
"""
import os


class ConnectionUnavailable(Exception):
    pass


def connect_mysql(host_env, port_env, user_env, password_env, db_env, defaults):
    import pymysql
    import pymysql.cursors

    host = os.environ.get(host_env, defaults.get("host"))
    port = int(os.environ.get(port_env, defaults.get("port", 3306)))
    user = os.environ.get(user_env, defaults.get("user", "root"))
    password = os.environ.get(password_env, defaults.get("password", ""))
    db = os.environ.get(db_env, defaults.get("db"))
    if not host or not db:
        raise ConnectionUnavailable("%s/%s not set (mysql)" % (host_env, db_env))
    try:
        return pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=db,
            connect_timeout=5,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
    except Exception as exc:  # noqa: BLE001 -- normalize every driver error to ConnectionUnavailable
        raise ConnectionUnavailable("mysql %s:%s/%s unreachable: %s" % (host, port, db, exc)) from exc


def connect_postgres(host_env, port_env, user_env, password_env, db_env, defaults):
    import psycopg
    from psycopg.rows import dict_row

    host = os.environ.get(host_env, defaults.get("host"))
    port = int(os.environ.get(port_env, defaults.get("port", 5432)))
    user = os.environ.get(user_env, defaults.get("user", "postgres"))
    password = os.environ.get(password_env, defaults.get("password", ""))
    db = os.environ.get(db_env, defaults.get("db"))
    if not host or not db:
        raise ConnectionUnavailable("%s/%s not set (postgres)" % (host_env, db_env))
    try:
        return psycopg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=db,
            connect_timeout=5,
            autocommit=True,
            row_factory=dict_row,
        )
    except Exception as exc:  # noqa: BLE001
        raise ConnectionUnavailable("postgres %s:%s/%s unreachable: %s" % (host, port, db, exc)) from exc


def connect_mongo(uri_env, default_uri):
    import pymongo

    uri = os.environ.get(uri_env, default_uri)
    if not uri:
        raise ConnectionUnavailable("%s not set (mongo)" % uri_env)
    try:
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        return client
    except Exception as exc:  # noqa: BLE001
        raise ConnectionUnavailable("mongo %s unreachable: %s" % (uri, exc)) from exc


def connect_redis(url_env, default_url):
    import redis

    url = os.environ.get(url_env, default_url)
    if not url:
        raise ConnectionUnavailable("%s not set (redis)" % url_env)
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001
        raise ConnectionUnavailable("redis %s unreachable: %s" % (url, exc)) from exc


def fetchone(conn, sql, params=()):
    """MySQL/Postgres-agnostic single-row fetch (both drivers configured for dict rows above)."""
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row


def fetchall(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def execute(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    cur.close()
