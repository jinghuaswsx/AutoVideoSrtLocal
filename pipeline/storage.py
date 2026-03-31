"""
TOS 对象存储工具：上传文件并返回可公开访问的 URL
用于将本地音频文件上传到火山引擎 TOS，供豆包 ASR 服务拉取
"""
import os
import tos
from config import (
    TOS_ACCESS_KEY, TOS_SECRET_KEY,
    TOS_ENDPOINT, TOS_REGION,
    TOS_BUCKET, TOS_PREFIX,
)

_client: tos.TosClientV2 = None


def _get_client() -> tos.TosClientV2:
    global _client
    if _client is None:
        _client = tos.TosClientV2(
            ak=TOS_ACCESS_KEY,
            sk=TOS_SECRET_KEY,
            endpoint=TOS_ENDPOINT,
            region=TOS_REGION,
        )
    return _client


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

    client = _get_client()
    client.put_object_from_file(TOS_BUCKET, object_key, local_path)

    # 生成预签名 URL，豆包服务端可直接访问私有 bucket 文件
    signed = client.pre_signed_url(tos.HttpMethodType.Http_Method_Get, TOS_BUCKET, object_key, expires=expires)
    return signed.signed_url


def delete_file(object_key: str):
    """删除 TOS 上的文件（用于任务完成后清理临时音频）"""
    client = _get_client()
    client.delete_object(TOS_BUCKET, object_key)
