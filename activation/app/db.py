from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session, create_engine

from app import config

engine = create_engine(
    f"sqlite:///{config.DATABASE_PATH}", connect_args={"check_same_thread": False}
)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def add_missing_columns() -> None:
    """Add columns new models gained since the table was created.

    `create_all` only creates missing tables, and the deployed database predates some
    columns. Every one added this way is nullable, so existing rows stay valid.
    """
    from sqlmodel import SQLModel

    with engine.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            existing = {
                row[1]
                for row in connection.exec_driver_sql(f"PRAGMA table_info({table.name})")
            }
            for column in table.columns:
                if column.name not in existing:
                    kind = column.type.compile(dialect=engine.dialect)
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table.name} ADD COLUMN {column.name} {kind}"
                    )
