# app/schemas/common.py
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Generic, TypeVar, List, Any
from datetime import datetime, timezone, timedelta

T = TypeVar('T')

# 上海时区 (UTC+8)
_SHANGHAI_TZ = timezone(timedelta(hours=8))


def _now_factory() -> datetime:
    """返回当前时间（timezone-aware），用于 Pydantic default_factory。"""
    from app.core.config import current_datetime as _cd
    return _cd()


def _serialize_datetime(v: datetime) -> str:
    """序列化 datetime 为 ISO 8601 格式，始终带时区偏移。

    如果 datetime 是 naive 的（从 PG ``timestamp without time zone`` 读回），
    默认为上海时区并补齐 ``+08:00`` 后缀。前端 ``new Date()`` 需要这个后缀
    才能在所有浏览器中一致解析。
    """
    if v.tzinfo is None:
        v = v.replace(tzinfo=_SHANGHAI_TZ)
    return v.isoformat()


class ResponseBase(BaseModel):
    """基础响应模型"""
    code: int = Field(default=0, description="状态码，0表示成功")
    message: str = Field(default="success", description="响应消息")
    timestamp: datetime = Field(default_factory=_now_factory, description="响应时间戳")

    model_config = ConfigDict(json_encoders={
        datetime: _serialize_datetime,
    })

class DataResponse(ResponseBase, Generic[T]):
    """数据响应模型"""
    data: Optional[T] = Field(default=None, description="响应数据")

class ListResponse(ResponseBase, Generic[T]):
    """列表响应模型"""
    data: List[T] = Field(default=[], description="数据列表")
    total: int = Field(default=0, description="总记录数")
    page: int = Field(default=1, description="当前页码")
    page_size: int = Field(default=20, description="每页数量")
    total_pages: int = Field(default=0, description="总页数")

class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量")
    order_by: Optional[str] = Field(default="create_time", description="排序字段")
    order_desc: bool = Field(default=True, description="是否降序")

class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = Field(..., description="服务状态")
    timestamp: datetime = Field(default_factory=_now_factory, description="检查时间")
    services: dict = Field(default={}, description="各服务状态")

class ErrorResponse(BaseModel):
    """错误响应"""
    code: int = Field(..., description="错误码")
    message: str = Field(..., description="错误信息")
    detail: Optional[str] = Field(default=None, description="错误详情")
    timestamp: datetime = Field(default_factory=_now_factory, description="错误时间")
