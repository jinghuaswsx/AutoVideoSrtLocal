from __future__ import annotations

from pathlib import Path


def test_translation_task_page_assets_include_task_entrypoints():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "medias_translation_tasks.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "medias_translation_tasks.js").read_text(encoding="utf-8")

    assert "翻译任务管理" in template
    assert "translationTasksApp" in template
    assert "medias_translation_tasks.js" in template
    assert "重新启动" in script
    assert "从中断点继续" in script
    assert "去选声音" in script
    assert "/medias/api/products/${productId}/translation-tasks" in script
