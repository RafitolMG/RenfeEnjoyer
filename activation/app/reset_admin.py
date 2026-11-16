"""Forget a changed admin password and fall back to ADMIN_PASSWORD.

For when the password has been lost. Run it inside the container, e.g. from Coolify's
terminal for the app or over SSH:

    docker exec <container> python -m app.reset_admin

It also signs out every admin session.
"""

from sqlmodel import Session, SQLModel, delete

from app.db import engine
from app.models import AdminCredential, AdminSession


def main() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.exec(delete(AdminCredential))
        session.exec(delete(AdminSession))
        session.commit()
    print("Contraseña de admin restablecida a ADMIN_PASSWORD; sesiones cerradas.")


if __name__ == "__main__":
    main()
