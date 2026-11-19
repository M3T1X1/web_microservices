from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class Role(str, Enum):
    ADMIN = "ADMIN"
    WAREHOUSE = "WAREHOUSE"
    SHIPPING = "SHIPPING"


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: Annotated[SecretStr, Field(min_length=12, max_length=128)]
    role: Role

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return str(value).casefold()


class Login(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class UserRead(BaseModel):
    id: UUID
    email: str
    role: Role
    is_active: bool


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool = Field(strict=True)


class AccessToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
