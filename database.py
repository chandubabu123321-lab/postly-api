from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from config import get_settings

settings = get_settings()

# SQLite needs check_same_thread=False; other DBs don't need this kwarg
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    echo=settings.DEBUG,
)

# Enable WAL mode for SQLite — better concurrent read performance
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Create all tables. Called on startup."""
    from models import user, connected_account, media_asset, post_draft, post_target, publish_job, audit_log  # noqa
    Base.metadata.create_all(bind=engine)

    # create_all never alters an existing table, so add newer columns here.
    with engine.begin() as conn:
        columns = {c["name"] for c in inspect(conn).get_columns("post_targets")}
        if "post_type" not in columns:
            conn.execute(text("ALTER TABLE post_targets ADD COLUMN post_type VARCHAR DEFAULT 'post'"))
        if "media_asset_id" not in columns:
            conn.execute(text("ALTER TABLE post_targets ADD COLUMN media_asset_id VARCHAR"))

        user_columns = {c["name"] for c in inspect(conn).get_columns("users")}
        if "role" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR DEFAULT 'user'"))
        if "owner_id" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN owner_id VARCHAR"))

        # The first time roles exist, whoever owns the connected social
        # accounts becomes the super admin.
        if not conn.execute(text("SELECT 1 FROM users WHERE role = 'super_admin'")).first():
            conn.execute(text(
                "UPDATE users SET role = 'super_admin' WHERE id IN "
                "(SELECT DISTINCT user_id FROM connected_accounts WHERE status = 'active')"
            ))
