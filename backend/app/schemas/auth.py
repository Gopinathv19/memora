import uuid

from pydantic import BaseModel, ConfigDict, Field

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



