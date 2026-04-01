"""TOS storage helpers used by the pipeline."""
import os

from appcore import tos_clients
from config import TOS_PREFIX


def upload_file(local_path: str, object_key: str = None, expires: int = 3600) -> str:
    """
    上传本地文件到 TOS，返回带签名的临时访问 URL（供豆包 ASR 等第三方服务拉取）

    Args:
        local_path: 本地文件路径
        object_key: TOS 对象键，默认用 PREFIX + 文件名
        expires: 预签名 URL 有效期（秒），默认 1 小时

    Returns:
        str: 预签名 HTTPS URL
    """
    if object_key is None:
        filename = os.path.basename(local_path)
        object_key = TOS_PREFIX + filename

    tos_clients.upload_file(local_path, object_key)
    return tos_clients.generate_signed_download_url(object_key, expires=expires)


def delete_file(object_key: str):
    """删除 TOS 上的文件（用于任务完成后清理临时音频）"""
    tos_clients.delete_object(object_key)
