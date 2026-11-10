from collections.abc import Generator

from time import perf_counter

from sqlalchemy import create_engine, event
from app.telemetry import metrics
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def _engine_options(url: str) -> dict:
    return {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}


_database_url = normalize_database_url(get_settings().database_url)
engine = create_engine(_database_url, **_engine_options(_database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@event.listens_for(engine, "before_cursor_execute")
def _before_query(conn, cursor, statement, parameters, context, executemany):
    context._latepayer_started = perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _after_query(conn, cursor, statement, parameters, context, executemany):
    timings = metrics.get()
    if timings is not None:
        timings["db"] = timings.get("db", 0.0) + (perf_counter() - context._latepayer_started) * 1000

# Register transactional projection invalidation after model definitions exist.
@event.listens_for(Session, "before_flush")
def _collect_projection_changes(db, context, instances):
    from app.services.projections import collect_changed_clients
    collect_changed_clients(db, context, instances)


@event.listens_for(Session, "before_commit")
def _refresh_projections(db):
    from app.services.projections import update_projections
    update_projections(db)


@event.listens_for(Session, "after_rollback")
def _clear_projection_changes(db):
    db.info.pop("projection_clients", None)


@event.listens_for(Session, "after_commit")
def _invalidate_committed_projection(db):
    if db.info.pop("projection_was_refreshed", False):
        from app.http_cache import invalidate
        invalidate()
