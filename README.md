# 语音克隆独立项目

从 `pypano` 抽出的独立语音克隆服务，推理引擎为 MOSS-TTS-Nano (0.1B, ONNX CPU)。
前后端可独立运行；当前版本补齐了**任务化推理、跨进程并发闸门、鉴权与归属隔离、音色库/长文播报、测试与部署**。

## 架构与目录

```
浏览器 (Vue 3 + Vite)
   │  POST /v1/voice-clone/jobs → 202 + job_id
   │  GET  /v1/voice-clone/jobs/<id> → 进度/结果（每 0.8s 轮询）
   ▼
Flask /v1  ── token_required / rate_limit / request_id / CORS 白名单
   │  jobs.py（内存任务表 + 后台线程）
   │  inference_slots.py（O_EXCL 锁文件，跨进程最多 N 路）
   ▼
moss_tts.py ── auto / onnx(in-process) / cli / http / mock
   │                                      └─ scripts/moss_tts_bridge.py (18090)
   ▼
var/voice-clone/*.wav + *.json   （产物，含 owner）
var/voice-profiles/<uid>/*      （参考声纹 + index.json）
```

| 路径 | 说明 |
|---|---|
| `app.py` / `wsgi.py` | 开发入口 / 生产入口（waitress） |
| `app/config/settings.py` | 所有环境变量集中解析 |
| `app/api/v1/voice_clone.py` | 全部 HTTP 接口 |
| `app/util/jobs.py` | 任务提交 / 状态 / 取消 / TTL 回收 |
| `app/util/inference_slots.py` | 跨进程推理并发闸门 |
| `app/util/moss_tts.py` | 引擎适配层（探测缓存、并发闸门、原子发布、归属校验） |
| `app/util/long_tts.py` / `wav_tools.py` | 长文本分句合成与 WAV 拼接 |
| `app/util/voice_profiles.py` | 按 uid 隔离的语音档案 |
| `app/plugin/auth.py` / `rate_limit.py` | 鉴权 / 滑动窗口限流 |
| `scripts/start_all.py` | 按 backend 决定是否起 bridge，统一启停 |
| `scripts/moss_tts_bridge.py` | 常驻 ONNX 推理服务（可选 token、排队上限） |
| `tests/` / `web/src/**/*.test.ts` | 后端 pytest / 前端 vitest |
| `third_party/`、`.venv-moss/`、`var/` | 权重、虚拟环境、数据（均不进版本库） |

## 快速开始

```bash
# 1) 主服务依赖（Flask / requests / waitress）
.venv-moss/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv-moss/bin/python -m pip install -r requirements.txt          # Linux/macOS

# 2) 复制配置并按需修改
cp .env.example .env

# 3) 一键启动（backend=http 时自动拉起 bridge 并等待 /health）
.venv-moss/Scripts/python.exe scripts/start_all.py                # Windows 也可双击 start.bat
# .venv-moss/bin/python scripts/start_all.py  或  ./start.sh       # Linux/macOS

# 4) 前端
cd web && pnpm install && pnpm dev            # http://localhost:5173
```

只想验证前后端链路、不加载模型：把 `.env` 的 `MOSS_TTS_BACKEND` 设为 `mock`，
或在页面里点「用演示模式试链路」（演示模式是唯一允许前端切换的后端）。

权重（约 728MB）与 `.venv-moss`（约 173MB）已随项目搬迁，无需重新下载；
换机器时执行 `python scripts/setup_moss_tts.py --skip-weights` 重建环境。

## 配置

完整示例见 `.env.example`，关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MOSS_TTS_BACKEND` | `auto` | `http`（bridge）/ `onnx` / `cli` / `auto` / `mock` |
| `MOSS_TTS_SERVICE_URL` | 空 | bridge 地址，http 后端必填 |
| `MOSS_TTS_TIMEOUT` | `600` | 单次推理超时（秒） |
| `MOSS_BRIDGE_TOKEN` | 空 | bridge 的 Bearer token，主服务会自动带上 |
| `VOICE_CLONE_API_KEY` | 空 | 单 key 鉴权；留空 = 单机免登录 |
| `VOICE_CLONE_USERS` | 空 | 多用户映射 `{"<key>":"<uid>"}`，决定档案归属 |
| `VOICE_CLONE_CORS_ORIGINS` | localhost:5173/5002 | 逗号分隔白名单，设为 `*` 才放开 |
| `VOICE_CLONE_MAX_INFERENCE` | `2` | 全局并发推理路数（跨进程生效） |
| `VOICE_CLONE_SLOT_WAIT` / `SLOT_LEASE` | `30` / `900` | 排队等待、崩溃锁租约（秒） |
| `VOICE_CLONE_JOB_TTL` / `JOB_MAX` | `3600` / `200` | 任务保留时长与上限 |
| `VOICE_CLONE_RETENTION` / `MAX_FILES` | `3600` / `200` | 产物保留时长与数量上限 |
| `VOICE_CLONE_PROBE_CACHE` | `8` | 引擎探测结果缓存秒数 |
| `VOICE_CLONE_LONG_TTS_MAX_CHARS` | `5000` | 长文播报上限 |
| `VOICE_CLONE_DATA_DIR` | `<root>/var` | 数据目录（声纹 + 产物） |
| `VOICE_CLONE_ALLOW_BACKEND_OVERRIDE` | `0` | 是否允许前端指定 onnx/cli/http 后端 |

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（公开，返回鉴权/并发/数据目录摘要） |
| GET | `/v1/voice-clone/status` | 引擎探测、配置、安装指引（强制重探） |
| POST | `/v1/voice-clone/jobs` | **推荐**：提交任务 → `202 + job_id` |
| GET | `/v1/voice-clone/jobs/<id>` | 任务状态、阶段、进度、结果 |
| DELETE | `/v1/voice-clone/jobs/<id>` | 取消任务 |
| POST | `/v1/voice-clone/clone` | 同步克隆（兼容旧客户端） |
| GET | `/v1/voice-clone/audio/<id>` | 播放 / `?download=1` 下载 WAV |
| GET | `/v1/voice-clone/meta/<id>` | 生成元数据 |
| GET/POST/DELETE | `/v1/voice-clone/profiles[/<id>]` | 语音档案 |
| POST | `/v1/voice-clone/tts` | 用已存音色同步合成（超 1000 字截断） |
| POST | `/v1/voice-clone/tts/segment` | 句级合成（实时播报逐句调用） |

`POST /jobs` 按 Content-Type 分流：

```bash
# multipart：上传参考音频 + 文本 → kind=clone
curl -X POST http://127.0.0.1:5002/v1/voice-clone/jobs \
  -F "audio=@ref.wav" -F "text=你好，这是一段克隆语音。"

# JSON：使用已存音色 → kind=tts；long=true 走自动分句 + 拼接 → kind=long_tts
curl -X POST http://127.0.0.1:5002/v1/voice-clone/jobs \
  -H "Content-Type: application/json" \
  -d '{"text":"第一句。第二句。","profile_id":"<id>","long":true}'

curl http://127.0.0.1:5002/v1/voice-clone/jobs/<job_id>
# {"code":200,"data":{"status":"running","stage":"正在推理","percent":60, ...}}
```

任务状态：`queued → running → succeeded / failed / canceled`。所有接口除 `/health` 外都走鉴权，
响应统一为 `code/data/message/request_id/timestamp` envelope，且 `code` 与 HTTP 状态码一致。

## 鉴权与数据隔离

- **单机免登录**（默认）：所有 key 为空，档案归 `local`。不再信任前端 `X-User-Id`。
- **单 key**：设置 `VOICE_CLONE_API_KEY`，请求需带 `Authorization: Bearer <key>`；
  前端在浏览器执行 `localStorage.setItem('auth-token', JSON.stringify('<key>'))`
  （也兼容直接写纯文本）。比较使用 `hmac.compare_digest`。
- **多用户**：设置 `VOICE_CLONE_USERS='{"key-a":"alice","key-b":"bob"}'`，key 决定 uid，
  档案目录与产物 `owner` 都按 uid 隔离；跨用户访问音频/元数据返回 404。

启用 API Key 后，前端播放器与下载改用 `fetch + Blob URL`（带鉴权头），
因为 `<audio src>` / `<a download>` 无法自定义请求头。

## 并发与任务模型

- 每次真实推理先占用一个槽位（`var/locks/slot-N.lock`），`O_EXCL` 文件锁跨进程/跨线程生效；
  排队超过 `VOICE_CLONE_SLOT_WAIT` 返回 503，崩溃残留锁靠 `SLOT_LEASE` 判定过期后原子重建。
- 长文播报整个任务只占一个槽位，逐句合成时复用（`slot_already_held`），不会把闸门占满。
- 推理在后台线程执行，HTTP 请求立即返回；前端轮询进度条，可随时取消。
  取消是**协作式**：排队/分句阶段立即生效，已进入 CLI 子进程的推理无法中断，
  但结果会被丢弃并删除已生成音频。
- `probe()` 结果带 TTL 缓存，避免每次推理都遍历 728MB 权重目录和发 HTTP 探活；
  `/status` 刷新按钮强制重探。
- 后台维护线程每 60s 回收过期产物、孤儿 json、临时目录、过期任务与 stale 锁文件。

## 数据目录与隐私

产物与声纹从旧的 `app/static/` 迁到 **`var/`**，且 `Flask(static_folder=None)` 已关闭静态路由
（`smoke_test.py` 会断言 `/static/<path>` 不存在）。**不要把 `var/` 交给 Nginx / CDN 静态托管**：
`voice-profiles/<uid>/index.json` 是固定可猜路径，泄露即等于泄露全部参考声纹。

- `var/voice-clone/<id>.wav|json`：产物；json 含 `owner` 与合成文本，接口按 uid 校验归属。
- `var/voice-profiles/<uid>/`：参考音频 + 原子写的 `index.json`。
- 写文件全部走 `.tmp` + `os.replace`；音频 `Cache-Control: private`。

> 旧目录 `app/static/voice-clone/`、`app/static/voice-profiles/` 已废弃，如里面有历史数据请手工搬到 `var/` 对应目录。

## 前端

两个 tab：「克隆实验室」与「音色库与播报」。

- 上传/录音统一在浏览器转成 48kHz 16-bit PCM WAV；校验 ≤20MB、≥0.8s，录音 15s 自动停止，
  并给出静音/削波/音量偏小的质量提示。
- 提交后显示阶段进度与耗时，可取消；最近 6 条结果存 localStorage（1 小时过期）。
- 音色库支持列表、选择、删除；播报支持**长文模式**（自动分句 + 拼接）与
  **逐句实时播报**（边合成下一句边播放当前句，可随时停止）。
- 上传区支持键盘操作（Enter/Space）与 focus 样式。
- 组件样式改用 SCSS：装 `sass` 预处理器（Vite 原生支持，无需额外插件），
  三个组件通过 `<style lang="scss" scoped src="...">` 引用同名 `.scss` 文件；
  共享变量与 mixin 在 `web/src/styles/_variables.scss` / `_mixins.scss`，
  预处理器参数统一在 `vite.config.ts` 的 `css.preprocessorOptions.scss` 配置。
- antd 只按需引入 message 样式，生产产物 CSS 49KB / JS 255KB。

## 生产部署

```bash
# 单进程多线程（推荐，任务表在进程内）
HOST=0.0.0.0 WEB_CONCURRENCY=8 python wsgi.py

# 或容器化（需先 pnpm --dir web run build）
docker compose up -d --build
# web(8080) → nginx → api(5002) → bridge(18090)，权重通过 volume 挂载
```

- `deploy/nginx.conf` 提供 `/v1/` 反代与 SPA fallback，`client_max_body_size 25m`。
- `docker-compose.yml` 内置两个 healthcheck；bridge 可选 `MOSS_BRIDGE_TOKEN`。
- 前端构建产物 `web/dist` 由 Nginx 托管，`VITE_GLOB_API_URL=/v1` 走同源反代。
- 结构化日志含 method / path / status / 耗时 / request_id；`X-Request-Id` 会透传到响应头。
- 多进程部署时推理闸门仍然有效，但**任务表与限流计数不跨进程共享**，
  需要多实例请改用 Redis + 独立 worker，或让网关按用户做粘性会话。

## 测试与 CI

```bash
.venv-moss/Scripts/python.exe -m pytest -q tests     # 17 个后端单测/接口测试
.venv-moss/Scripts/python.exe scripts/smoke_test.py  # 路由 + 导入 + 静态路由关闭
cd web && pnpm run type-check && pnpm run test && pnpm run build
```

`.github/workflows/ci.yml` 在 push / PR 时跑：pytest + smoke_test、vue-tsc、vitest、vite build。

## 已知限制 / 安全清单

- 声纹属于生物特征数据：默认明文落盘、无加密、无审计；对外提供服务前请补充用户同意、加密存储与删除审计。
- 任务表是进程内存态，服务重启后 `job_id` 失效；产物仍在 `var/` 可通过 `/audio/<id>` 访问。
- `/tts/segment` 仍是同步接口，单句推理约等于音频时长；超长文本请用 `long` 任务。
- CLI 后端每次推理起一个子进程并加载权重，并发高时建议用 `http` + 常驻 bridge。
- bridge 即使开了 token 也只建议内网使用；`MOSS_TTS_BACKEND=mock` 只用于链路联调。
- WeTextProcessing 在 Windows 上依赖 pynini 编译，已显式关闭；中文标点/数字清洗仍保留。

## 本次完善清单

- **P0**：跨进程并发闸门；鉴权重写（不再信任 `X-User-Id`、`compare_digest`、uid 归属校验）；
  CORS 白名单；任务化 `/jobs`（进度/取消，解决前端 300s 与后端 600s 超时错配）；
  数据目录迁出 `static` 并关闭静态路由。
- **P1**：音色库 + 长文播报 + 逐句实时播报页（原 `profiles/tts/tts/segment` 前端死代码已接入）；
  统一请求头；长文自动分句拼接；前端大小/时长/音质校验、录音上限、history 持久化、a11y。
- **P2**：`git init`；17 个 pytest + 10 个 vitest + CI；Dockerfile / compose / Nginx；
  waitress + 结构化日志 + 优雅停机 + `start_all.py`；probe TTL 缓存、原子写与定时清理；
  bridge token/排队上限/优雅停机；注释与文档重写。
