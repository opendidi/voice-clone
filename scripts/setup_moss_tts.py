# -*- coding: UTF-8 -*-
"""
一键准备 MOSS-TTS-Nano (0.1B) ONNX CPU 语音克隆环境（本地功能测试用）。

    python scripts/setup_moss_tts.py
    python scripts/setup_moss_tts.py --skip-weights                      # 只 clone + 装依赖
    python scripts/setup_moss_tts.py --pip-index https://pypi.tuna.tsinghua.edu.cn/simple
    python scripts/setup_moss_tts.py --hf-mirror https://hf-mirror.com  # 权重下载走镜像

做三件事，产物全部落在 .gitignore 忽略的目录，不污染项目主环境：
  1. git clone --depth 1 官方仓库 -> third_party/MOSS-TTS-Nano
  2. 建独立虚拟环境 .venv-moss，装 requirements-moss-tts.txt
  3. 用仓库自带样例音频跑一次 infer_onnx.py，触发 ONNX 权重自动下载并验证推理

跑完后重启主服务（python app.py 或 python wsgi.py），刷新页面，状态应显示「引擎就绪」。
app/util/moss_tts.py 会自动探测 .venv-moss，无需手工设置环境变量。

⚠️ 需要能访问 GitHub / PyPI / HuggingFace，首次会下载数百 MB 权重；
   下载慢时加 --hf-mirror https://hf-mirror.com，并确保进程有足够磁盘空间。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "third_party" / "MOSS-TTS-Nano"
VENV = ROOT / ".venv-moss"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQUIREMENTS = ROOT / "requirements-moss-tts.txt"
GIT_URL = "https://github.com/OpenMOSS/MOSS-TTS-Nano.git"


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int | None = None,
        env: dict | None = None) -> None:
    print("\n$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, timeout=timeout,
                   env=env, check=True)


ZIP_URL = "https://codeload.github.com/OpenMOSS/MOSS-TTS-Nano/zip/refs/heads/main"


def _download_zip() -> bool:
    """公司/本地代理常拒绝 git 的 CONNECT 隧道，但放行 codeload 的 HTTPS GET。"""
    import io
    import shutil
    import urllib.request
    import zipfile

    print(f"  下载源码包：{ZIP_URL}")
    try:
        with urllib.request.urlopen(ZIP_URL, timeout=300) as resp:
            payload = resp.read()
    except Exception as exc:
        print(f"  zip 下载失败：{exc}")
        return False

    tmp = REPO.parent / "_moss_src_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            zf.extractall(tmp)
        roots = [p for p in tmp.iterdir() if p.is_dir()]
        source = roots[0] if len(roots) == 1 else tmp
        if REPO.exists():
            shutil.rmtree(REPO)
        shutil.move(str(source), str(REPO))
    except (zipfile.BadZipFile, OSError, IndexError) as exc:
        print(f"  zip 解压失败：{exc}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return (REPO / "infer_onnx.py").is_file()


def step_clone() -> bool:
    if (REPO / "infer_onnx.py").is_file():
        print(f"\n[1/3] 官方仓库已存在，跳过：{REPO}")
        return True
    print("\n[1/3] 准备官方仓库源码…")
    REPO.parent.mkdir(parents=True, exist_ok=True)

    if _download_zip():
        print(f"  ✓ 源码已就位：{REPO}")
        return True

    print("  zip 方式不可用，回退 git clone…")
    try:
        run(["git", "clone", "--depth", "1", GIT_URL, str(REPO)], timeout=600)
    except FileNotFoundError:
        print("✗ 未找到 git，请手动下载 " + ZIP_URL + " 解压到 " + str(REPO))
        return False
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"✗ git clone 也失败：{exc}")
        print("  手动方案：浏览器打开 " + ZIP_URL + "，解压后把 MOSS-TTS-Nano-main")
        print("  改名为 MOSS-TTS-Nano，放到 " + str(REPO))
        return False
    return (REPO / "infer_onnx.py").is_file()


# 官方 onnx_tts_runtime.py 顶层 `import torch` + `import torchaudio`，
# 但只用 torchaudio.load / torchaudio.functional.resample 读参考音频，其余都是真 torch 张量操作。
# 主环境已有 CPU 版 torch，开启 system-site-packages 复用即可；torchaudio 用这个极薄兼容层补齐，
# 避免下载 200MB+ 且 PyPI 版本(2.11.x)与主环境 torch(2.15)不匹配的 wheel。
TORCH_SHIM = '''"""Minimal torchaudio shim for MOSS-TTS-Nano ONNX runtime.
Only load()/functional.resample are used, returning real torch.Tensor objects.
"""
import soundfile as sf
import torch


def load(path, *args, **kwargs):
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)  # (frames, channels)
    return torch.from_numpy(data.T.copy()), int(sr)                  # (channels, frames)


class _Functional:
    @staticmethod
    def resample(waveform, orig_freq, new_freq, *args, **kwargs):
        if int(orig_freq) == int(new_freq):
            return waveform
        import torch.nn.functional as F
        n = max(1, int(round(waveform.shape[-1] * int(new_freq) / int(orig_freq))))
        return F.interpolate(waveform.unsqueeze(1), size=n, mode="linear",
                             align_corners=False).squeeze(1)


functional = _Functional()
'''


def _prepare_torch_compat() -> bool:
    """让独立环境能 import 官方 ONNX runtime。"""
    cfg = VENV / "pyvenv.cfg"
    try:
        if cfg.is_file():
            text = cfg.read_text(encoding="utf-8")
            cfg.write_text(
                text.replace("include-system-site-packages = false",
                             "include-system-site-packages = true"),
                encoding="utf-8",
            )
    except OSError as exc:
        print(f"  ✗ 修改 pyvenv.cfg 失败：{exc}")
        return False

    site = VENV / (
        "Lib/site-packages" if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    pkg = site / "torchaudio"
    try:
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text(TORCH_SHIM, encoding="utf-8")
    except OSError as exc:
        print(f"  ✗ 写入 torchaudio 兼容层失败：{exc}")
        return False

    try:
        subprocess.run(
            [str(VENV_PY), "-c", "import onnx_tts_runtime; print('  runtime import OK')"],
            cwd=str(REPO), check=True, timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"  ✗ 官方 runtime 仍无法导入：{exc}")
        print("    若主环境没有 torch，请先装 CPU 版：")
        print("    " + str(VENV_PY) + " -m pip install torch --index-url https://download.pytorch.org/whl/cpu")
        return False
    return True


def step_venv(pip_index: str) -> bool:
    print("\n[2/3] 准备独立虚拟环境…")
    if not VENV_PY.is_file():
        try:
            run([sys.executable, "-m", "venv", str(VENV)], timeout=300)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"✗ 创建虚拟环境失败：{exc}")
            return False
    if not VENV_PY.is_file():
        print(f"✗ 找不到虚拟环境解释器：{VENV_PY}")
        return False

    if not REQUIREMENTS.is_file():
        print(f"✗ 缺少依赖清单：{REQUIREMENTS}")
        return False
    cmd = [str(VENV_PY), "-m", "pip", "install", "-r", str(REQUIREMENTS)]
    if pip_index:
        cmd += ["--index-url", pip_index]
    try:
        run(cmd, timeout=1200)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"✗ 依赖安装失败：{exc}")
        print("  可加 --pip-index <国内镜像> 后重试")
        return False
    return _prepare_torch_compat()


def step_weights(hf_mirror: str) -> bool:
    print("\n[3/3] 预热推理（首次会自动下载 ONNX 权重，可能耗时数分钟）…")
    env = os.environ.copy()
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if hf_mirror:
        env["HF_ENDPOINT"] = hf_mirror

    # 官方 ensure_browser_onnx_model_dir 只按 TTS 侧的 browser_poc_manifest.json 判断，
    # 若上次在下载 codec 仓库时代理中断，会留下空目录并误判「已就绪」，随后直接崩在
    # 读 codec_browser_onnx_meta.json。这里主动补齐，让脚本能从半下载状态自愈。
    codec_dir = REPO / "models" / "MOSS-Audio-Tokenizer-Nano-ONNX"
    if not (codec_dir / "codec_browser_onnx_meta.json").is_file():
        print("  TTS 权重已存在但 codec 权重不完整，单独补下载…")
        snippet = (
            "from huggingface_hub import snapshot_download; "
            "snapshot_download(repo_id='OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX', "
            f"local_dir=r'{codec_dir}', allow_patterns=['*.onnx','*.data','*.json'])"
        )
        try:
            run([str(VENV_PY), "-c", snippet], timeout=1800, env=env)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(f"  codec 权重补下载失败：{exc}，已下载文件会缓存，重跑本脚本可续传")
            return False

    out_dir = REPO / "generated_audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "setup_check.wav"

    ref = next((REPO / "assets" / "audio").glob("*.wav"), None)
    cmd = [
        str(VENV_PY), "infer_onnx.py",
        "--text", "MOSS-TTS-Nano 语音克隆环境检查。",
        "--output-audio-path", str(out),
        "--disable-wetext-processing",
        "--cpu-threads", "4",
        "--max-new-frames", "96",
    ]
    if ref and ref.is_file():
        cmd += ["--prompt-audio-path", str(ref)]
    else:
        print("  未找到样例参考音频，改用内置音色预热（不影响权重下载）")

    started = time.perf_counter()
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            run(cmd, cwd=REPO, timeout=1800, env=env)
            break
        except subprocess.TimeoutExpired:
            print("✗ 预热超时（30 分钟），请检查网络或改天重试")
            return False
        except subprocess.CalledProcessError as exc:
            last_error = exc
            print(f"  第 {attempt}/3 次未成功，下载中断时会复用已缓存文件重试…")
            if attempt < 3:
                time.sleep(5)
    else:
        print(f"✗ 预热推理连续失败 3 次：{last_error}")
        print("  常见原因：代理 502 / 权重下载中断 / onnxruntime 版本不匹配")
        print("  huggingface_hub 会复用已下载文件，稍后重跑本脚本即可续传")
        return False

    if out.is_file() and out.stat().st_size > 1024:
        print(f"✓ 推理成功：{out}（{out.stat().st_size} 字节，"
              f"耗时 {time.perf_counter() - started:.1f}s）")
        return True
    print("✗ 未生成有效音频，请查看上面的推理日志")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="准备 MOSS-TTS-Nano ONNX CPU 环境")
    parser.add_argument("--skip-weights", action="store_true", help="跳过第三步权重预热")
    parser.add_argument("--pip-index", default="", help="pip 镜像地址")
    parser.add_argument("--hf-mirror", default="", help="HuggingFace 镜像，如 https://hf-mirror.com")
    args = parser.parse_args()

    print("MOSS-TTS-Nano 环境准备")
    print(f"  仓库：{REPO}")
    print(f"  环境：{VENV_PY}")

    if not step_clone():
        return 1
    if not step_venv(args.pip_index):
        return 1
    if args.skip_weights:
        print("\n已跳过权重预热（--skip-weights）。首次在页面点生成时会自动下载权重。")
    elif not step_weights(args.hf_mirror):
        return 1

    print("\n" + "=" * 60)
    print("环境就绪。接下来：")
    print("  1) 重启 Flask：Ctrl+C 停掉 starter.py，再执行 python starter.py")
    print("  2) 打开 http://localhost:5173/voice-clone，刷新页面")
    print("  3) 状态应显示「引擎就绪」，把推理后端切回「自动选择」即可真实克隆")
    print("  主服务会自动探测 .venv-moss，一般无需设置 MOSS_TTS_PYTHON / MOSS_TTS_REPO")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
