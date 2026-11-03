from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str = "user"                # super_admin | user
    can_manage_accounts: bool = True  # false for team users, who post through the admin's accounts

    model_config = {"from_attributes": True}
