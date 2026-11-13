# -*- coding: UTF-8 -*-
"""v1 路由：语音克隆 + 语音聊天。"""
from flask import Blueprint

from app.api.v1.voice_chat import voice_chat_api
from app.api.v1.voice_clone import voice_clone_api


def create_v1():
    bp_v1 = Blueprint("v1", __name__)
    bp_v1.register_blueprint(voice_clone_api, url_prefix="/voice-clone")
    bp_v1.register_blueprint(voice_chat_api, url_prefix="/voice-chat")
    return bp_v1
