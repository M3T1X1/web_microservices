"""RS256: tylko Auth otrzymuje klucz prywatny, usługi weryfikują publicznym."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models.user import Role

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class TokenVerifier:
    public_key: bytes
    issuer: str = "warehouse-auth"
    audience: str = "warehouse-api"

    @classmethod
    def from_env(cls):
        key = Path(os.getenv("JWT_PUBLIC_KEY_FILE", ".local/jwt-public.pem")).read_bytes()
        public = load_pem_public_key(key)
        if not isinstance(public, RSAPublicKey) or public.key_size < 2048:
            raise ValueError("JWT requires an RSA public key of at least 2048 bits")
        return cls(key, os.getenv("JWT_ISSUER", "warehouse-auth"), os.getenv("JWT_AUDIENCE", "warehouse-api"))

    def decode(self, token):
        try:
            claims = jwt.decode(token, self.public_key, algorithms=["RS256"], issuer=self.issuer,
                                audience=self.audience,
                                options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "role", "jti"]})
            UUID(claims["sub"])
            Role(claims["role"])
            return claims
        except (jwt.InvalidTokenError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"}) from exc


def issue_token(user, private_key, verifier, ttl=900):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user.id), "role": user.role.value, "iss": verifier.issuer,
                       "aud": verifier.audience, "iat": now, "nbf": now,
                       "exp": now + timedelta(seconds=ttl), "jti": str(uuid4())},
                      private_key, algorithm="RS256")


def require_roles(*roles):
    def authorize(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not request.app.state.auth_enabled:
            return None  # Tylko jawnie wyłączony tryb historycznych testów LAB1–LAB4.
        if credentials is None:
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
        claims = request.app.state.token_verifier.decode(credentials.credentials)
        if claims["role"] not in {"ADMIN", *roles}:
            raise HTTPException(403, "Insufficient permissions")
        return claims
    return authorize


def forwarded_headers(request):
    authorization = request.headers.get("authorization")
    return {"Content-Type": "application/json", **({"Authorization": authorization} if authorization else {})}
