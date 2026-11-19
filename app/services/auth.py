from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException
from sqlalchemy import select

from app.db_models import UserRow
from app.models.user import UserCreate, UserRead
from app.repositories import write_transaction


def user_payload(row):
    return UserRead(id=row.id, email=row.email, role=row.role, is_active=row.is_active)


class AuthService:
    def __init__(self, sessions):
        self.sessions = sessions
        self.hasher = PasswordHasher()
        # Weryfikacja nieistniejącego konta również wykonuje kosztowny hash.
        self.dummy_hash = self.hasher.hash(str(uuid4()))

    def create_user(self, body: UserCreate, *, bootstrap=False):
        password_hash = self.hasher.hash(body.password.get_secret_value())
        with write_transaction(self.sessions) as db:
            if bootstrap and db.scalar(select(UserRow.id).limit(1)) is not None:
                raise HTTPException(409, "Bootstrap requires an empty users table")
            if db.scalar(select(UserRow.id).where(UserRow.email == body.email)):
                raise HTTPException(409, "Email already exists")
            row = UserRow(id=uuid4(), email=body.email, password_hash=password_hash,
                          role=body.role.value, is_active=True)
            db.add(row)
            result = user_payload(row)
        return result

    def login(self, body):
        with self.sessions() as db:
            row = db.scalar(select(UserRow).where(UserRow.email == str(body.email).casefold()))
            try:
                self.hasher.verify(row.password_hash if row else self.dummy_hash, body.password.get_secret_value())
            except (VerificationError, InvalidHashError):
                row = None
            if row is None or not row.is_active:
                raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
            return user_payload(row)

    def get_user(self, user_id):
        with self.sessions() as db:
            row = db.get(UserRow, user_id)
            if row is None or not row.is_active:
                raise HTTPException(401, "User is inactive or missing", headers={"WWW-Authenticate": "Bearer"})
            return user_payload(row)

    def update_user(self, user_id, body):
        with write_transaction(self.sessions) as db:
            row = db.get(UserRow, user_id)
            if row is None:
                raise HTTPException(404, "User not found")
            if row.role == "ADMIN" and not body.is_active:
                admins = db.scalars(select(UserRow).where(UserRow.role == "ADMIN", UserRow.is_active.is_(True))).all()
                if row.is_active and len(admins) <= 1:
                    raise HTTPException(409, "Cannot deactivate the last active administrator")
            row.is_active = body.is_active
            result = user_payload(row)
        return result
