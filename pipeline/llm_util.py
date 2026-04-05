"""公共 LLM 工具：统一的 JSON 响应解析。"""
import json
import re


def parse_json_response(text: str):
    """从 LLM 响应中提取 JSON。

    支持：纯 JSON、markdown code block 包裹、JSON 前有文本。
    返回解析后的 dict 或 list。
    无法解析时抛出 ValueError 或 json.JSONDecodeError。
    """
    if text is None:
        raise TypeError("LLM 返回内容为 None")
    text = text.strip()
    if not text:
        raise ValueError("LLM 返回内容为空")

    # 1. 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 尝试去除 markdown code block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3. 尝试找第一个 { 或 [ 到最后一个 } 或 ]
    for open_char, close_char in [("{", "}"), ("[", "]")]:
        start = text.find(open_char)
        end = text.rfind(close_char)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

    raise ValueError(f"无法从 LLM 响应中解析 JSON: {text[:200]}")
