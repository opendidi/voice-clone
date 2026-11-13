# -*- coding: UTF-8 -*-
"""v1 路由：独立项目里只保留语音克隆一组接口。"""
from flask import Blueprint

from app.api.v1.voice_clone import voice_clone_api


def create_v1():
    bp_v1 = Blueprint("v1", __name__)
    bp_v1.register_blueprint(voice_clone_api, url_prefix="/voice-clone")
    return bp_v1
