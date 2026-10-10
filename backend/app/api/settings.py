"""Settings endpoints for system configuration."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any

from backend.app import models
from backend.app.database import get_db
from backend.app.deps import require_roles

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    settings: dict[str, str]


@router.get("")
def get_settings(
    user: models.User = Depends(require_roles("admin", "finance_controller", "analyst", "viewer")),
    db: Session = Depends(get_db)
):
    rows = db.query(models.SystemSetting).all()
    # Default values
    config = {
        "amount_eps": "1.00",
        "bank_delay_window_days": "7",
        "high_value_threshold": "50000.00",
    }
    for r in rows:
        config[r.key] = r.value
    return config


@router.post("")
def update_settings(
    body: SettingsUpdate,
    user: models.User = Depends(require_roles("admin", "finance_controller")),
    db: Session = Depends(get_db)
):
    for k, v in body.settings.items():
        if k in ("amount_eps", "bank_delay_window_days", "high_value_threshold"):
            setting = db.query(models.SystemSetting).filter_by(key=k).first()
            if not setting:
                setting = models.SystemSetting(key=k, value=v)
                db.add(setting)
            else:
                setting.value = v
    db.commit()
    return {"message": "Settings updated"}
