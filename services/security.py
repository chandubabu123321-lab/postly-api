"""Auth helpers — JWT tokens, password hashing, token encryption for OAuth."""
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
import bcrypt
from cryptography.fernet import Fernet
import base64
from config import get_settings
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

settings = get_settings()
bearer = HTTPBearer()

# Fernet key must be 32 url-safe base64-encoded bytes
def _fernet():
    key = settings.ENCRYPTION_KEY.encode()[:32].ljust(32, b"0")
    return Fernet(base64.urlsafe_b64encode(key))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()


def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    # A disabled user's token stops working at once, not when it expires.
    from database import SessionLocal
    from models.user import User
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="This account is disabled.")
    finally:
        db.close()
    return user_id
