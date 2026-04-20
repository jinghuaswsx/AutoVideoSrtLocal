class UpstreamServiceError(Exception):
    """上游（AutoVideo OpenAPI）返回非 2xx 的异常。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
