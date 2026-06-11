import unittest
import json
from unittest.mock import patch, MagicMock
from appcore.server_health import collect_system_status, evaluate_and_save_record

class TestServerHealth(unittest.TestCase):
    def test_collect_system_status(self):
        """验证数据采集方法是否返回合法且完整的数据字典结构。"""
        status = collect_system_status()
        self.assertIn("disk", status)
        self.assertIn("memory", status)
        self.assertIn("cpu", status)
        self.assertIn("gpu", status)
        
        # 验证 disk 结构
        self.assertIn("total", status["disk"])
        self.assertIn("used", status["disk"])
        self.assertIn("percent", status["disk"])
        self.assertIn("partition", status["disk"])
        
        # 验证 memory 结构
        self.assertIn("total", status["memory"])
        self.assertIn("used", status["memory"])
        self.assertIn("percent", status["memory"])
        
        # 验证 cpu 结构
        self.assertIn("load_1m", status["cpu"])
        self.assertIn("load_5m", status["cpu"])
        self.assertIn("load_15m", status["cpu"])
        self.assertIn("percent", status["cpu"])
        self.assertIn("cores", status["cpu"])

    @patch("appcore.server_health.execute")
    @patch("appcore.server_health.invoke_generate")
    @patch("appcore.server_health.collect_system_status")
    def test_evaluate_and_save_record_healthy(self, mock_collect, mock_invoke, mock_execute):
        """测试在所有硬件指标正常健康时，状态评定、AI调用与DB落库行为。"""
        # 模拟健康的指标数据
        mock_collect.return_value = {
            "disk": {"total": 1000, "used": 500, "free": 500, "percent": 50.0, "partition": "/"},
            "memory": {"total": 1000, "used": 400, "free": 600, "percent": 40.0, "swap_total": 0, "swap_used": 0, "swap_percent": 0.0},
            "cpu": {"load_1m": 0.5, "load_5m": 0.4, "load_15m": 0.3, "percent": 15.0, "cores": 4},
            "gpu": None,
            "is_windows": True
        }
        mock_execute.return_value = 42  # 模拟返回新插入的 ID
        
        record_id = evaluate_and_save_record()
        
        self.assertEqual(record_id, 42)
        # 健康时，不需要发起 LLM 询问调用
        mock_invoke.assert_not_called()
        
        # 验证写入 DB 的参数中，状态为 'healthy'
        args = mock_execute.call_args[0]
        params = args[1]
        self.assertEqual(params[0], "healthy")
        self.assertEqual(params[1], "0.5, 0.4, 0.3")

    @patch("appcore.server_health.execute")
    @patch("appcore.server_health.invoke_generate")
    @patch("appcore.server_health.collect_system_status")
    def test_evaluate_and_save_record_warning(self, mock_collect, mock_invoke, mock_execute):
        """测试在磁盘空间紧张（使用率 88%）时，能否触发 warning 评级与 AI 生成建议。"""
        # 模拟警告的指标数据 (磁盘使用率 88.0%)
        mock_collect.return_value = {
            "disk": {"total": 1000, "used": 880, "free": 120, "percent": 88.0, "partition": "/"},
            "memory": {"total": 1000, "used": 400, "free": 600, "percent": 40.0, "swap_total": 0, "swap_used": 0, "swap_percent": 0.0},
            "cpu": {"load_1m": 0.5, "load_5m": 0.4, "load_15m": 0.3, "percent": 15.0, "cores": 4},
            "gpu": None,
            "is_windows": True
        }
        mock_execute.return_value = 99
        mock_invoke.return_value = {"text": "Gemini: Clean up /tmp...\n```bash\nrm -rf /tmp/test-venv\n```"}
        
        record_id = evaluate_and_save_record()
        
        self.assertEqual(record_id, 99)
        # 应当调用了 LLM 产生优化方案
        mock_invoke.assert_called_once()
        
        # 验证写入 DB 的状态为 'warning'，且包含了正确匹配到的 issue 问题
        args = mock_execute.call_args[0]
        params = args[1]
        self.assertEqual(params[0], "warning")
        
        issues = json.loads(params[6])
        self.assertEqual(len(issues), 1)
        self.assertIn("磁盘剩余空间紧张，使用率已达 88.0%", issues[0])
        
        self.assertEqual(params[7], "Gemini: Clean up /tmp...\n```bash\nrm -rf /tmp/test-venv\n```")
