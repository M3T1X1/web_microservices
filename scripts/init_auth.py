"""Utwórz lokalne klucze RSA i pierwszego administratora bez domyślnego hasła."""
import argparse
from getpass import getpass
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption,
    load_pem_private_key, load_pem_public_key,
)
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from app.database import Base, make_database
from app.db_models import UserRow
from app.models.user import UserCreate, Role
from app.services.auth import AuthService


def generate_keys(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    private, public = directory / "jwt-private.pem", directory / "jwt-public.pem"
    if private.exists() or public.exists():
        if not (private.exists() and public.exists()):
            raise RuntimeError("Incomplete key pair; existing keys will not be overwritten")
        key = load_pem_private_key(private.read_bytes(), password=None)
        verification_key = load_pem_public_key(public.read_bytes())
        if (not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048
                or not isinstance(verification_key, rsa.RSAPublicKey)
                or key.public_key().public_numbers() != verification_key.public_numbers()):
            raise RuntimeError("Existing JWT keys do not form a valid RSA key pair")
        return
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # O_EXCL zapobiega nadpisaniu istniejącego klucza.
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    with public.open("xb") as out:
        out.write(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--keys-dir", type=Path, default=Path(".local"))
    parser.add_argument("--database-url", default=os.getenv("AUTH_DATABASE_URL", "sqlite:///./auth.db"))
    args = parser.parse_args()
    password = getpass("Hasło administratora (minimum 12 znaków): ")
    if password != getpass("Powtórz hasło: "):
        raise SystemExit("Hasła są różne")
    try:
        body = UserCreate(email=args.email, password=password, role=Role.ADMIN)
    except ValidationError:
        raise SystemExit("Niepoprawny email lub długość hasła (wymagane 12–128 znaków)") from None
    engine, sessions = make_database(args.database_url)
    try:
        Base.metadata.create_all(engine, tables=[UserRow.__table__])
        with sessions() as db:
            if db.scalar(select(UserRow.id).limit(1)) is not None:
                raise SystemExit("Bootstrap requires an empty users table")
        generate_keys(args.keys_dir)
        try:
            user = AuthService(sessions).create_user(body, bootstrap=True)
        except HTTPException as exc:
            raise SystemExit(exc.detail) from exc
        print(f"Utworzono administratora: {user.email}. Klucze: {args.keys_dir}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
