"""Public-exchange TOS helpers used by provider pull flows."""

import os

from appcore import tos_clients
from config import TOS_PREFIX


def upload_file(
    local_path: str,
    object_key: str = None,
    expires: int = 3600,
    bucket: str | None = None,
) -> str:
    """Upload a local file to TOS and return a signed download URL."""
    if object_key is None:
        filename = os.path.basename(local_path)
        object_key = TOS_PREFIX + filename

    tos_clients.upload_file(local_path, object_key, bucket=bucket)
    return tos_clients.generate_signed_download_url(object_key, expires=expires, bucket=bucket)


def delete_file(object_key: str, bucket: str | None = None):
    """Delete a temporary public-exchange object from TOS."""
    tos_clients.delete_object(object_key, bucket=bucket)
