import os
import time
from collections.abc import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from gateway.models import Base
from gateway.settings import BACKEND_DIR, database_url, load_dotenv

load_dotenv()


def _create_engine() -> Engine:
    url = database_url()
    if url.startswith("sqlite"):
        return create_engine(url, future=True, connect_args={"check_same_thread": False})
    return create_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_size=_positive_int("GATEWAY_DB_POOL_SIZE", 5),
        max_overflow=_positive_int("GATEWAY_DB_MAX_OVERFLOW", 10),
    )


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def wait_for_database() -> None:
    last: Exception | None = None
    for _ in range(30):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError("Database is not reachable.") from last


def migrate() -> None:
    command.upgrade(alembic_config(), "head")


def init_database() -> None:
    wait_for_database()
    names = set(inspect(engine).get_table_names())
    # A database that already has tables is stamped at the single schema revision.
    # First-time setup creates every table from that revision.
    if "api_keys" in names:
        from alembic.script import ScriptDirectory

        known = {revision.revision for revision in ScriptDirectory.from_config(alembic_config()).walk_revisions()}
        version = None
        if "alembic_version" in names:
            with engine.connect() as connection:
                version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if version not in known:
            if "alembic_version" in names:
                head = ScriptDirectory.from_config(alembic_config()).get_current_head()
                with engine.begin() as connection:
                    connection.execute(text("UPDATE alembic_version SET version_num = :version"), {"version": head})
            else:
                command.stamp(alembic_config(), "head")
            return
    migrate()


def reset_database() -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    Base.metadata.drop_all(engine)
    migrate()
