from __future__ import annotations

import argparse

from app.core.config import Settings
from app.queue.postgres_store import PostgresQueueStore


def main():
    parser=argparse.ArgumentParser(description="Apply or explicitly roll back Wellar PostgreSQL schema migrations")
    parser.add_argument("direction",choices=("up","down"))
    args=parser.parse_args();settings=Settings.from_env();settings.validate()
    if settings.queue_backend!="postgres":parser.error("WELLAR_QUEUE_BACKEND=postgres is required")
    store=PostgresQueueStore(settings.postgres_dsn,capacity=settings.queue_capacity,max_attempts=settings.max_attempts,
        lease_seconds=settings.lease_seconds,retry_base_seconds=settings.retry_base_seconds,
        min_pool_size=settings.postgres_pool_min,max_pool_size=settings.postgres_pool_max,
        pool_timeout=settings.postgres_pool_timeout,connect_timeout=settings.postgres_connect_timeout,
        statement_timeout_ms=settings.postgres_statement_timeout_ms,migrate=False)
    try:
        if args.direction=="up":store.migrate()
        else:store.migrate_down()
    finally:store.close()


if __name__=="__main__":main()
