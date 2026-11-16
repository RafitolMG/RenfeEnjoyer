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
