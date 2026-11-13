@echo off
REM 按 .env 的 MOSS_TTS_BACKEND 自动决定是否拉起推理 bridge，并等待其就绪
REM 可选参数：--no-bridge（不启 bridge）/ --no-wait（不等健康检查）
.venv-moss\Scripts\python.exe scripts\start_all.py %*
