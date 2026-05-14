"""本机 GPU 人声分离服务的 HTTP 客户端。

服务端基于 nomadkaraoke/python-audio-separator + 自包 FastAPI，本机生产
部署地址为 ``http://127.0.0.1:83``。

协议（同步阻塞模式，单次 POST 拿 ZIP）：
- ``POST /separate/download``  multipart
  {file=wav, separation_goal=background_preserve, output_format=WAV}
  → ``application/zip``，包含两个 wav：``..._(Vocals)_..._.wav``、
  ``..._(Instrumental)_..._.wav``
- ``GET  /health`` → ``200`` 表示服务健康

注意点：
- 翻译配音背景保留流程直接上传高保真 WAV，不再压成 192kbps MP3。
- 服务端有 1h MD5 缓存：相同文件 + 相同目标 / 格式几乎秒返。
- 单次 POST 可能阻塞 GPU 排队 + 推理 + ZIP 打包，需要较长 read timeout。

异常分两层：
- :class:`SeparationApiUnavailable` — 网络 / 5xx / read timeout。
- :class:`SeparationFailed` — 4xx、ZIP 损坏、stem 缺失等业务问题。
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)


DEFAULT_PRESET = "vocal_balanced"
DEFAULT_SEPARATION_GOAL = "background_preserve"
DEFAULT_OUTPUT_FORMAT = "WAV"
DEFAULT_BASE_URL = "http://127.0.0.1:83"

# 任务总超时（含 API 端内部排队 + GPU 推理 + ZIP 打包 + 流式下载）。
# 单个 POST /separate/download 同步阻塞，给 5 分钟兜底。
DEFAULT_TASK_TIMEOUT = 300.0

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_NETWORK_RETRIES = 3
DEFAULT_NETWORK_RETRY_BACKOFF = 2.0


class SeparationApiUnavailable(RuntimeError):
    """API 端点不可达：连接失败、5xx 等。caller 应降级。"""


class SeparationTimeout(SeparationApiUnavailable):
    """单次 POST 在 :attr:`SeparationClient.task_timeout` 内未返回。

    专门拎出来一个子类，让 runtime / 前端能区分"完全连不上"和
    "API 还在跑但 GPU 排队太久" —— 都走降级，但 UI 标注不同。
    """


class SeparationFailed(RuntimeError):
    """API 业务失败：4xx、ZIP 损坏、stem 缺失等。"""


@dataclass
class SeparationResult:
    vocals_path: str
    accompaniment_path: str
    model: str
    elapsed_seconds: float
    task_id: str  # 同步阻塞模式无 task_id，固定 ""，保留字段供 caller 兼容


class SeparationClient:
    """同步包装：upload → blocking POST → 解压 ZIP，给 runtime 当工具用。

    线程安全：单实例可被多个 task 共享调用（每次 separate 走独立临时目录）。
    """

    def __init__(
        self,
        base_url: str,
        *,
        task_timeout: float = DEFAULT_TASK_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        api_key: str | None = None,
        network_retries: int = DEFAULT_NETWORK_RETRIES,
        network_retry_backoff: float = DEFAULT_NETWORK_RETRY_BACKOFF,
    ):
        if not base_url:
            raise ValueError(f"base_url is required (e.g. {DEFAULT_BASE_URL})")
        self.base_url = base_url.rstrip("/")
        # POST /separate/download 的 read timeout，覆盖 GPU 排队 + 推理 + ZIP 下载。
        self.task_timeout = float(task_timeout)
        self.connect_timeout = float(connect_timeout)
        self.api_key = api_key
        self.network_retries = max(1, int(network_retries))
        self.network_retry_backoff = float(network_retry_backoff)

    def separate(
        self,
        audio_path: str,
        *,
        output_dir: str,
        preset: str | None = DEFAULT_PRESET,
        separation_goal: str = DEFAULT_SEPARATION_GOAL,
        output_format: str = DEFAULT_OUTPUT_FORMAT,
        vocals_filename: str = "vocals.wav",
        accompaniment_filename: str = "accompaniment.wav",
        # 兼容老 caller 仍然传 ``model=...``：旧异步协议是模型名，新协议是
        # ensemble preset。如果显式传了 model 优先用它当 preset。
        model: str | None = None,
    ) -> SeparationResult:
        """同步分离一条音频，返回本地 vocals / accompaniment 路径。"""
        in_p = Path(audio_path)
        if not in_p.is_file():
            raise FileNotFoundError(f"audio not found: {audio_path}")
        out_d = Path(output_dir)
        out_d.mkdir(parents=True, exist_ok=True)

        # 兼容老调用：``model=`` 当作 preset 使用。
        effective_preset = model if model else preset
        effective_goal = (
            separation_goal or DEFAULT_SEPARATION_GOAL
        ).strip() or DEFAULT_SEPARATION_GOAL
        effective_format = (
            output_format or DEFAULT_OUTPUT_FORMAT
        ).strip().upper() or DEFAULT_OUTPUT_FORMAT
        preset_for_request = _preset_for_request(effective_preset)
        model_label = preset_for_request or effective_goal

        started = time.monotonic()

        zip_path = self._post_separate_download(
            in_p,
            separation_goal=effective_goal,
            output_format=effective_format,
            preset=preset_for_request,
        )
        try:
            self._extract_stems(
                zip_path,
                out_d,
                vocals_filename=vocals_filename,
                accompaniment_filename=accompaniment_filename,
            )
        finally:
            try:
                os.unlink(zip_path)
            except OSError:
                pass

        elapsed = time.monotonic() - started
        log.info(
            "[audio_separation] goal=%s preset=%s format=%s done in %.1fs (src=%s)",
            effective_goal,
            preset_for_request or "-",
            effective_format,
            elapsed,
            in_p.name,
        )
        return SeparationResult(
            vocals_path=str(out_d / vocals_filename),
            accompaniment_path=str(out_d / accompaniment_filename),
            model=model_label,
            elapsed_seconds=elapsed,
            task_id="",
        )

    def health(self) -> bool:
        """探活：访问 /health 端点。失败返回 False，不抛。"""
        url = f"{self.base_url}/health"
        try:
            resp = requests.get(
                url,
                timeout=(self.connect_timeout, self.connect_timeout),
                headers=self._headers(),
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _headers(self) -> dict:
        h = {"Accept": "*/*"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _ensure_mp3(self, audio_path: Path) -> Path:
        """Deprecated compatibility hook; production upload no longer transcodes."""
        raise SeparationFailed(
            "mp3 upload transcoding is disabled; pass the source audio directly"
        )

    def _post_separate_download(
        self,
        upload_path: Path,
        *,
        separation_goal: str,
        output_format: str,
        preset: str | None,
    ) -> Path:
        """单次 POST /separate/download，stream 把 ZIP 接到临时文件返回路径。

        重试策略：连接失败 / 5xx / read timeout 重试 ``network_retries`` 次；
        4xx 立即抛 :class:`SeparationFailed` 不重试。
        """
        url = f"{self.base_url}/separate/download"
        last_exc: Exception | None = None
        for attempt in range(self.network_retries):
            fd, zip_name = tempfile.mkstemp(prefix="sep_zip_", suffix=".zip")
            os.close(fd)
            zip_path = Path(zip_name)
            try:
                with upload_path.open("rb") as fh:
                    files = {
                        "file": (
                            upload_path.name,
                            fh,
                            _content_type_for_path(upload_path),
                        )
                    }
                    data = {
                        "separation_goal": separation_goal,
                        "output_format": output_format,
                    }
                    if preset:
                        data["ensemble_preset"] = preset
                    with requests.post(
                        url, files=files, data=data,
                        headers=self._headers(),
                        timeout=(self.connect_timeout, self.task_timeout),
                        stream=True,
                    ) as resp:
                        if resp.status_code >= 500:
                            body = (resp.text or "")[:300]
                            raise SeparationApiUnavailable(
                                f"server error {resp.status_code}: {body}"
                            )
                        if resp.status_code >= 400:
                            body = (resp.text or "")[:300]
                            raise SeparationFailed(
                                f"api rejected request ({resp.status_code}): {body}"
                            )
                        with zip_path.open("wb") as out_fh:
                            for chunk in resp.iter_content(chunk_size=1 << 20):
                                if chunk:
                                    out_fh.write(chunk)
                if zip_path.stat().st_size == 0:
                    raise SeparationFailed(
                        "server returned empty zip body"
                    )
                return zip_path
            except SeparationFailed:
                # 4xx / 业务失败：立即抛，不重试，先清掉临时 zip。
                try:
                    zip_path.unlink()
                except OSError:
                    pass
                raise
            except (requests.ConnectionError, requests.Timeout,
                    SeparationApiUnavailable) as exc:
                # 临时 zip 清理掉再决定要不要重试。
                try:
                    zip_path.unlink()
                except OSError:
                    pass
                last_exc = exc
                if attempt < self.network_retries - 1:
                    time.sleep(self.network_retry_backoff * (attempt + 1))
                    continue
                if isinstance(exc, requests.Timeout):
                    raise SeparationTimeout(
                        f"separation api read timeout after "
                        f"{self.task_timeout:.0f}s: {exc}"
                    ) from exc
                raise SeparationApiUnavailable(
                    f"separation api unreachable after {self.network_retries} "
                    f"attempts: {exc}"
                ) from exc
        # 不应到达
        raise SeparationApiUnavailable(f"unexpected retry exhaustion: {last_exc}")

    def _extract_stems(
        self,
        zip_path: Path,
        out_dir: Path,
        *,
        vocals_filename: str,
        accompaniment_filename: str,
    ) -> None:
        """解压 ZIP，把 Vocals / Instrumental wav 移到目标位置。

        命名规则：``input_<hash>_(Vocals)_preset_<preset>.wav`` 和
        ``input_<hash>_(Instrumental)_preset_<preset>.wav``。匹配
        ``(Vocals)`` / ``(Instrumental)`` 子串（大小写不敏感），仅取 .wav。
        """
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                vocals_name = _pick_stem_member(names, "vocals")
                accomp_name = _pick_stem_member(names, "instrumental")
                if not vocals_name:
                    raise SeparationFailed(
                        f"vocals stem not found in zip; members={names}"
                    )
                if not accomp_name:
                    raise SeparationFailed(
                        f"instrumental stem not found in zip; members={names}"
                    )
                vocals_dest = out_dir / vocals_filename
                accomp_dest = out_dir / accompaniment_filename
                _extract_member_to(zf, vocals_name, vocals_dest)
                _extract_member_to(zf, accomp_name, accomp_dest)
        except zipfile.BadZipFile as exc:
            raise SeparationFailed(f"corrupt zip from server: {exc}") from exc

        # 双保险：解出来如果是空文件视为业务失败。
        if vocals_dest.stat().st_size == 0:
            raise SeparationFailed("vocals stem extracted as empty file")
        if accomp_dest.stat().st_size == 0:
            raise SeparationFailed("instrumental stem extracted as empty file")


def _pick_stem_member(names: list[str], stem_kind: str) -> str | None:
    """从 zip 成员里挑出包含 ``(Vocals)`` 或 ``(Instrumental)`` 的 .wav。

    服务端实测命名形如 ``input_<hash>_(Vocals)_preset_<preset>.wav``。
    匹配大小写不敏感，仅看 .wav 后缀（防止其他附属文件混进来）。
    """
    needle = f"({stem_kind})".lower()
    for name in names:
        if not name.lower().endswith(".wav"):
            continue
        if needle in name.lower():
            return name
    return None


def _preset_for_request(preset: str | None) -> str | None:
    preset_clean = (preset or "").strip()
    if not preset_clean or preset_clean == DEFAULT_PRESET:
        return None
    return preset_clean


def _content_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".flac":
        return "audio/flac"
    if suffix == ".ogg":
        return "audio/ogg"
    if suffix in {".m4a", ".mp4"}:
        return "audio/mp4"
    return "application/octet-stream"


def _extract_member_to(zf: zipfile.ZipFile, member: str, dest: Path) -> None:
    """安全解压：用流式 copy 而非 zf.extract，避免目录穿越和重名冲突。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member, "r") as src, dest.open("wb") as out:
        shutil.copyfileobj(src, out)
