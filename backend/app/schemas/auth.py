import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

class SignUpRequest(BaseModel):
    email: EmailStr
    password:str = Field(min_length=8)
    name: str = Field(default="",max_length=255)

class LoginRequest(BaseModel):
    email:EmailStr
    password:str

class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:uuid.UUID
    email:str
    email_verified:bool
    name:str
    avatar_url: str

class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=1)


class AuthResponse(BaseModel):
    user: UserRead



