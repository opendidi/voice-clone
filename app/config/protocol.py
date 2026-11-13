# -*- coding: UTF-8 -*-
"""独立版 API 协议定义：code 值与原 pypano 保持一致，前端无需改动。"""
from enum import Enum


class ProtocolVersion(str, Enum):
    V1 = "1.0"
    V2 = "2.0"


class StatusCode(Enum):
    SUCCESS = 200
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    UNSUPPORTED_MEDIA_TYPE = 415
    METHOD_NOT_ALLOWED = 405
    REQUEST_ENTITY_TOO_LARGE = 413
    TOO_MANY_REQUESTS = 429
    INTERNAL_ERROR = 500
    SERVICE_UNAVAILABLE = 503


API_PROTOCOL = {
    "current_version": ProtocolVersion.V2,
    "response_template": {
        "code": StatusCode.SUCCESS,
        "data": None,
        "message": "",
        "request_id": "",
        "timestamp": 0,
    },
    "signature_required": False,
}
