"""模型手动导入（下载被网络阻断时的兜底）。

用户把已经下载好的模型文件（zip 或单独的 .gguf）放进来，应用安全解压到受管目录：

- 解压严格防 Zip-Slip：拒绝绝对路径、拒绝用 ../ 越级写出受管目录。
- 按内容分流：
  * zip 内含 .gguf  -> 当作 LLM 模型，落到 llm_models/<model_id>.gguf；
  * 单独的 .gguf   -> 同上（直接写盘，无需解压）；
  * 语音模型不提供手动导入：应用只支持从设置页下载并校验官方 Qwen3-ASR-0.6B。

说明：文字模型导入仍会经过严格的路径安全检查；语音模型统一走 Qwen 下载流程，
避免把旧版或不兼容的语音模型写入本机。
"""
from __future__ import annotations

import io
import os
import zipfile

from ..logutil import get_logger

LOG = get_logger("model_import")


class SecurityError(Exception):
    """解压时发现路径穿越等不安全成员。"""


def safe_extract_zip(zf: zipfile.ZipFile, dest: str) -> list[str]:
    """把 zip 安全解压到 dest，返回解出的相对成员名。

    任何会写出 dest 之外的成员都抛 SecurityError（Zip-Slip）。
    """
    os.makedirs(dest, exist_ok=True)
    dest = os.path.abspath(dest)
    extracted: list[str] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        # 防御 1：成员名本身是绝对路径
        if os.path.isabs(info.filename):
            raise SecurityError(f"拒绝绝对路径成员: {info.filename}")
        # 防御 2：成员名含 .. 越级（含各种归一化后逃出 dest 的情况）
        target = os.path.abspath(os.path.join(dest, info.filename))
        if target != dest and not target.startswith(dest + os.sep):
            raise SecurityError(f"拒绝路径穿越成员: {info.filename} -> {target}")
        # 防御 3：zip 自身记录的外部属性里的符号链接（不跟踪，直接写普通文件）
        parent = os.path.dirname(target)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            out.write(src.read())
        extracted.append(info.filename)
    return extracted


def _is_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


def import_model_archive(data: bytes, *, model_type: str, model_id: str,
                          llm_models_dir: str) -> dict:
    """导入模型档案。

    model_type: 'auto' | 'llm'
    model_id:   LLM 导入时的目标模型 id（用于文件名 <id>.gguf）
    返回 {type, files, dest}
    """
    if not data:
        raise ValueError("空文件")

    if model_type == "whisper":
        raise ValueError("语音模型只支持 Qwen3-ASR-0.6B，请在设置页下载并校验")

    if not _is_zip(data):
        # 单独 .gguf 直传
        if model_type == "whisper":
            raise ValueError("whisper 模型需以 zip 形式（含完整缓存目录）导入")
        os.makedirs(llm_models_dir, exist_ok=True)
        dest = os.path.join(llm_models_dir, f"{model_id}.gguf")
        with open(dest, "wb") as f:
            f.write(data)
        LOG.info("导入 LLM 模型(裸 gguf): %s (%d bytes)", dest, len(data))
        return {"type": "llm", "files": [dest], "dest": dest}

    # zip 分支
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        has_gguf = any(n.lower().endswith(".gguf") for n in names)

        if model_type == "auto":
            if not has_gguf:
                raise ValueError("不支持导入语音模型；请在设置页下载 Qwen3-ASR-0.6B")
            model_type = "llm"

        if model_type == "llm":
            # 仅取 .gguf，安全解压后合并为单个 <id>.gguf
            gguf_members = [n for n in names if n.lower().endswith(".gguf")]
            if not gguf_members:
                raise ValueError("zip 内未找到 .gguf 文件")
            # 安全解压到临时目录再挑出
            tmp = os.path.join(llm_models_dir, ".import_tmp")
            safe_extract_zip(zf, tmp)
            try:
                os.makedirs(llm_models_dir, exist_ok=True)
                dest = os.path.join(llm_models_dir, f"{model_id}.gguf")
                # 若 zip 内含多个 gguf，取体积最大的；通常只有一个
                chosen = max(gguf_members, key=lambda n: _member_size(zf, n))
                src = os.path.join(os.path.abspath(tmp), chosen)
                with open(src, "rb") as f:
                    blob = f.read()
                with open(dest, "wb") as f:
                    f.write(blob)
            finally:
                _rmtree(tmp)
            LOG.info("导入 LLM 模型(zip): %s", dest)
            return {"type": "llm", "files": [dest], "dest": dest}

        raise ValueError("语音模型只支持 Qwen3-ASR-0.6B，请在设置页下载并校验")


def _member_size(zf: zipfile.ZipFile, name: str) -> int:
    try:
        return zf.getinfo(name).file_size
    except Exception:  # noqa: BLE001
        return 0


def _rmtree(path: str) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
