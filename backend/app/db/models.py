from sqlmodel import Field, SQLModel

from app.credentials import EncryptedString


class UserBase(SQLModel):
    username: str = Field(index=True, unique=True)
    email: str
    # The bot types it into Renfe's login, so it is encrypted rather than hashed.
    password: str = Field(sa_type=EncryptedString)
    abono: str


class User(UserBase, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
