"""多语种视频翻译：语言规则包。

每种语言一份模块（de.py / fr.py / ...），声明字幕规则、TTS 语言码、
前后处理函数。Prompt 不在这里——走 llm_prompt_configs 数据库表。
"""
