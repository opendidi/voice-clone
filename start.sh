#!/usr/bin/env bash
# 按 .env 的 MOSS_TTS_BACKEND 自动决定是否拉起推理 bridge，并等待其就绪
# 可选参数：--no-bridge / --no-wait
set -e
cd "$(dirname "$0")"
exec .venv-moss/bin/python scripts/start_all.py "$@"
