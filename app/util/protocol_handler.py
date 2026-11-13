# -*- coding: UTF-8 -*-
"""统一响应封装（独立版，去掉了原项目对 middleware request_id 的强依赖）。"""
import time
from typing import Any
from uuid import uuid4

from flask import g

from app.config.protocol import API_PROTOCOL, StatusCode


class ProtocolBuilder:

    @staticmethod
    def build_response(
        data: Any, code: StatusCode = StatusCode.SUCCESS, message: str = ""
    ) -> dict:
        request_id = (
            g.get("request_id")
            if hasattr(g, "request_id") and g.get("request_id")
            else str(uuid4()).replace("-", "")
        )
        return {
            "code": code.value,
            "data": data,
            "message": message,
            "request_id": request_id,
            "timestamp": int(time.time()),
            "version": API_PROTOCOL["current_version"].value,
        }
