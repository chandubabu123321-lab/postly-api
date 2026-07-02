"""Media router — upload files, list user assets."""
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models.media_asset import MediaAsset
from services.security import get_current_user_id
from services.storage import save_file
import uuid

router = APIRouter(prefix="/media", tags=["media"])


@router.post("/upload", status_code=201)
async def upload(
    file: UploadFile = File(...),
    user_id: str     = Depends(get_current_user_id),
    db: Session      = Depends(get_db),
):
    result = await save_file(file)
    asset  = MediaAsset(
        id            = str(uuid.uuid4()),
        user_id       = user_id,
        original_name = result["original_name"],
        file_url      = result["file_url"],
        file_type     = result["file_type"],
        mime_type     = result["mime_type"],
        file_size     = result["file_size"],
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return {
        "id":            asset.id,
        "original_name": asset.original_name,
        "file_url":      asset.file_url,
        "file_type":     asset.file_type,
        "mime_type":     asset.mime_type,
        "file_size":     asset.file_size,
    }


@router.get("/")
def list_assets(user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    assets = db.query(MediaAsset).filter(MediaAsset.user_id == user_id).order_by(MediaAsset.created_at.desc()).all()
    return [{"id": a.id, "original_name": a.original_name, "file_url": a.file_url,
             "file_type": a.file_type, "created_at": str(a.created_at)} for a in assets]


@router.delete("/{asset_id}", status_code=204)
def delete_asset(asset_id: str, user_id: str = Depends(get_current_user_id), db: Session = Depends(get_db)):
    asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id, MediaAsset.user_id == user_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found.")
    db.delete(asset)
    db.commit()
