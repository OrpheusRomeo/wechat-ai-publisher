# app/schemas/ip_whitelist.py
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class IPWhitelistCreate(BaseModel):
    ip_or_cidr: str = Field(..., min_length=1, max_length=64, description="IP 或 CIDR，如 192.168.1.1 或 10.0.0.0/8")
    label: str = Field(default="", max_length=128, description="备注说明")
    enabled: bool = Field(default=True)


class IPWhitelistUpdate(BaseModel):
    ip_or_cidr: Optional[str] = Field(default=None, max_length=64)
    label: Optional[str] = Field(default=None, max_length=128)
    enabled: Optional[bool] = None


class IPWhitelistResponse(BaseModel):
    id: int
    ip_or_cidr: str
    label: str
    enabled: bool
    created_at: Optional[datetime] = None
    created_by: str = ""

    model_config = {"from_attributes": True}


class IPCheckResponse(BaseModel):
    ip: str
    allowed: bool
    message: str
