from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import DATABASE_PATH, DATABASE_URL

# check_same_thread=False: the Selenium worker runs off the request thread.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def init_db() -> None:
    """Create missing tables. Safe to call on every startup; never drops data."""
    # SQLite creates the file but not its directory, which on a fresh Windows install
    # (%LOCALAPPDATA%\RenfeEnjoyer) does not exist yet.
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
