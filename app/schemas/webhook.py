# app/schemas/webhook.py
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from enum import Enum

class WeChatMessageType(str, Enum):
    """微信消息类型"""
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"
    SHORTVIDEO = "shortvideo"
    LOCATION = "location"
    LINK = "link"
    EVENT = "event"

class WeChatEventType(str, Enum):
    """微信事件类型"""
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    SCAN = "SCAN"
    LOCATION = "LOCATION"
    CLICK = "CLICK"
    VIEW = "VIEW"

class WeChatMessage(BaseModel):
    """微信消息基础模型"""
    ToUserName: str = Field(..., description="开发者微信号")
    FromUserName: str = Field(..., description="发送方帐号（一个OpenID）")
    CreateTime: int = Field(..., description="消息创建时间 （整型）")
    MsgType: WeChatMessageType = Field(..., description="消息类型")
    MsgId: Optional[int] = Field(default=None, description="消息ID")

class WeChatTextMessage(WeChatMessage):
    """微信文本消息"""
    Content: str = Field(..., description="文本消息内容")
    MsgType: WeChatMessageType = Field(default=WeChatMessageType.TEXT)

class WeChatEventMessage(WeChatMessage):
    """微信事件消息"""
    Event: WeChatEventType = Field(..., description="事件类型")
    EventKey: Optional[str] = Field(default=None, description="事件KEY值")
    Ticket: Optional[str] = Field(default=None, description="二维码ticket")

class WeChatReplyMessage(BaseModel):
    """微信回复消息"""
    ToUserName: str = Field(..., description="接收方帐号")
    FromUserName: str = Field(..., description="开发者微信号")
    CreateTime: int = Field(..., description="消息创建时间")
    MsgType: str = Field(..., description="消息类型")
    Content: Optional[str] = Field(default=None, description="文本内容")
    Articles: Optional[List[Dict[str, str]]] = Field(default=None, description="图文消息")

class WeChatWebhookResponse(BaseModel):
    """微信Webhook响应"""
    code: int = Field(default=0, description="响应码")
    message: str = Field(default="success", description="响应消息")
    reply: Optional[WeChatReplyMessage] = Field(default=None, description="回复消息")

