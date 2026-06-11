import os
import shutil
import subprocess
import json
import logging
from datetime import datetime
from appcore.db import query_one, execute
from appcore.llm_client import invoke_generate

log = logging.getLogger(__name__)

def collect_system_status() -> dict:
    """采集服务器（磁盘、内存、CPU、NVIDIA GPU）的实时状态指标。"""
    status = {
        "disk": {"total": 0, "used": 0, "free": 0, "percent": 0.0, "partition": "/"},
        "memory": {"total": 0, "used": 0, "free": 0, "percent": 0.0, "swap_total": 0, "swap_used": 0, "swap_percent": 0.0},
        "cpu": {"load_1m": 0.0, "load_5m": 0.0, "load_15m": 0.0, "percent": 0.0, "cores": os.cpu_count() or 1},
        "gpu": None,
        "is_windows": os.name == 'nt'
    }
    
    # 1. 磁盘空间采集
    try:
        path = "C:\\" if os.name == 'nt' else "/"
        total, used, free = shutil.disk_usage(path)
        status["disk"]["total"] = total
        status["disk"]["used"] = used
        status["disk"]["free"] = free
        status["disk"]["percent"] = round((used / total) * 100, 2) if total else 0.0
        status["disk"]["partition"] = path
    except Exception as e:
        log.warning("[server_health] disk collection failed: %s", e)

    # 2. 内存 & CPU 采集
    if os.name == 'nt':
        # Windows 降级采集（优先尝试导入 psutil）
        try:
            import psutil
            mem = psutil.virtual_memory()
            status["memory"]["total"] = mem.total
            status["memory"]["used"] = mem.used
            status["memory"]["free"] = mem.available
            status["memory"]["percent"] = mem.percent
            
            swap = psutil.swap_memory()
            status["memory"]["swap_total"] = swap.total
            status["memory"]["swap_used"] = swap.used
            status["memory"]["swap_percent"] = swap.percent
            
            status["cpu"]["percent"] = psutil.cpu_percent(interval=0.1)
            status["cpu"]["load_1m"] = round(status["cpu"]["percent"] / 100 * status["cpu"]["cores"], 2)
            status["cpu"]["load_5m"] = status["cpu"]["load_1m"]
            status["cpu"]["load_15m"] = status["cpu"]["load_1m"]
        except ImportError:
            # Windows fallback 模拟数据（开发环境兜底）
            status["memory"]["total"] = 32 * 1024 * 1024 * 1024
            status["memory"]["used"] = 16 * 1024 * 1024 * 1024
            status["memory"]["free"] = 16 * 1024 * 1024 * 1024
            status["memory"]["percent"] = 50.0
            
            status["cpu"]["percent"] = 15.0
            status["cpu"]["load_1m"] = 0.5
            status["cpu"]["load_5m"] = 0.4
            status["cpu"]["load_15m"] = 0.3
    else:
        # Linux 原生采集
        # 2a. 内存采集 (解析 free -b)
        try:
            out = subprocess.check_output(["free", "-b"], text=True)
            lines = out.strip().split('\n')
            for line in lines:
                parts = line.split()
                if not parts:
                    continue
                if parts[0].startswith("Mem:"):
                    total = int(parts[1])
                    free_mem = int(parts[6]) if len(parts) > 6 else int(parts[3])
                    used = total - free_mem
                    status["memory"]["total"] = total
                    status["memory"]["used"] = used
                    status["memory"]["free"] = free_mem
                    status["memory"]["percent"] = round((used / total) * 100, 2) if total else 0.0
                elif parts[0].startswith("Swap:"):
                    swap_total = int(parts[1])
                    swap_used = int(parts[2])
                    status["memory"]["swap_total"] = swap_total
                    status["memory"]["swap_used"] = swap_used
                    status["memory"]["swap_percent"] = round((swap_used / swap_total) * 100, 2) if swap_total else 0.0
        except Exception as e:
            log.warning("[server_health] memory collection failed: %s", e)

        # 2b. CPU 负载 & 使用率采集 (解析 /proc/loadavg 与 /proc/stat)
        try:
            with open("/proc/loadavg", "r") as f:
                load_parts = f.readline().split()
                status["cpu"]["load_1m"] = float(load_parts[0])
                status["cpu"]["load_5m"] = float(load_parts[1])
                status["cpu"]["load_15m"] = float(load_parts[2])
            
            with open("/proc/stat", "r") as f:
                line = f.readline()
                parts = line.split()
                prev_idle = float(parts[4])
                prev_total = sum(float(x) for x in parts[1:8])
            
            import time
            time.sleep(0.1)
            
            with open("/proc/stat", "r") as f:
                line = f.readline()
                parts = line.split()
                idle = float(parts[4])
                total = sum(float(x) for x in parts[1:8])
                
            diff_idle = idle - prev_idle
            diff_total = total - prev_total
            status["cpu"]["percent"] = round((1.0 - diff_idle / diff_total) * 100, 2) if diff_total else 0.0
        except Exception as e:
            log.warning("[server_health] CPU collection failed: %s", e)

    # 3. GPU 显卡状态采集 (NVIDIA GPU via nvidia-smi)
    try:
        cmd = ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        parts = out.strip().split(',')
        if len(parts) >= 5:
            status["gpu"] = {
                "name": parts[0].strip(),
                "utilization": float(parts[1].strip()),
                "memory_used": int(parts[2].strip()) * 1024 * 1024, # 转换为 bytes
                "memory_total": int(parts[3].strip()) * 1024 * 1024, # 转换为 bytes
                "temperature": float(parts[4].strip()),
                "processes": []
            }
            # 获取正在运行的 GPU 进程
            try:
                proc_cmd = ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]
                proc_out = subprocess.check_output(proc_cmd, text=True, stderr=subprocess.DEVNULL)
                for line in proc_out.strip().split('\n'):
                    if not line.strip():
                        continue
                    p_parts = line.split(',')
                    if len(p_parts) >= 3:
                        status["gpu"]["processes"].append({
                            "pid": int(p_parts[0].strip()),
                            "name": p_parts[1].strip(),
                            "memory": int(p_parts[2].strip()) * 1024 * 1024 # 转换为 bytes
                        })
            except Exception:
                pass
    except Exception:
        status["gpu"] = None
        
    return status

def evaluate_and_save_record() -> int:
    """执行资源评估，判定异常并调用 Gemini 3.5 Flash 生成 Codex 建议，最后存档。"""
    status_data = collect_system_status()
    issues = []
    
    # 磁盘空间监控 (警告线: 85%, 危险线: 92%)
    disk_pct = status_data["disk"]["percent"]
    if disk_pct >= 92.0:
        issues.append(f"磁盘剩余空间严重不足，使用率已达 {disk_pct}%")
    elif disk_pct >= 85.0:
        issues.append(f"磁盘剩余空间紧张，使用率已达 {disk_pct}%")
        
    # 物理内存监控 (可用内存 < 10% 报警)
    mem_pct = status_data["memory"]["percent"]
    if mem_pct >= 90.0:
        issues.append(f"物理内存告急，已使用 {mem_pct}%")
        
    # CPU 负载监控 (1分钟负载 > 核心数 * 1.5 报警)
    cpu_load = status_data["cpu"]["load_1m"]
    cpu_cores = status_data["cpu"]["cores"]
    if cpu_load > cpu_cores * 1.5:
        issues.append(f"CPU 负载过高 (1m load: {cpu_load}, Cores: {cpu_cores})")
        
    # GPU 温度监控 (温度 > 80℃ 报警)
    if status_data["gpu"] and status_data["gpu"]["temperature"] > 80.0:
        issues.append(f"GPU 温度过高，当前已达 {status_data['gpu']['temperature']}℃")
        
    # 评定整体状态等级
    if any("严重" in issue or "告急" in issue for issue in issues):
        status = "critical"
    elif issues:
        status = "warning"
    else:
        status = "healthy"
        
    # 生成优化建议与 Codex 指令
    if status == "healthy":
        suggestions = "系统各项硬指标处于健康状态，目前无需任何干预和调整。"
    else:
        prompt = f"""
服务器当前出现以下资源或硬件警报，请针对这些异常情况进行分析，并给出一个优化排查建议。
此外，你必须专门为服务器上的 Codex（AI助手）提供一套可直接解释并安全执行的指令，用于自动执行相关的清理或优化操作。

【系统配置核心数】: {cpu_cores} 核

【当前资源详情】:
- 磁盘使用：{json.dumps(status_data['disk'])}
- 内存使用：{json.dumps(status_data['memory'])}
- CPU负载：{json.dumps(status_data['cpu'])}
- GPU状态：{json.dumps(status_data['gpu'], ensure_ascii=False)}

【检测到的异常问题】:
{json.dumps(issues, ensure_ascii=False)}

【输出要求】:
1. 先给出简明扼要的硬件报警分析与优化建议。
2. 必须提供一键复制的 Codex 执行指令，放在单独的 ```bash 代码块中。Codex 在执行时可以直接运行这些 Linux shell 脚本。
指令内容必须安全（比如：清理指定的无用临时文件夹、轮转/清空特定超大日志文件、或者利用 mysqladmin 释放 binlog，禁止使用危险的 rm -rf / 或杀掉核心服务等操作）。如果是内存问题，可以提供释放缓存（如 sync; echo 3 > /proc/sys/vm/drop_caches）或者定位并列出吃内存的大进程的指令。
"""
        try:
            res = invoke_generate(
                "server_health.audit",
                prompt=prompt,
                system="你是一位资深的 Linux 运维专家。你的任务是分析服务器硬件资源警报，并给出优化方案及可安全执行的一键修复指令。"
            )
            suggestions = res.get("text", "")
        except Exception as e:
            log.warning("[server_health] LLM suggestion generation failed: %s", e)
            suggestions = f"调用 AI 获取优化建议失败: {e}"
            
    # 将记录写入数据库
    sql = """
        INSERT INTO server_health_records (
            status, system_load, cpu_usage, memory_usage, gpu_usage, disk_usage, issues, suggestions
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    cpu_info = {"percent": status_data["cpu"]["percent"], "cores": cpu_cores}
    
    record_id = execute(sql, (
        status,
        f"{status_data['cpu']['load_1m']}, {status_data['cpu']['load_5m']}, {status_data['cpu']['load_15m']}",
        json.dumps(cpu_info),
        json.dumps(status_data["memory"]),
        json.dumps(status_data["gpu"]),
        json.dumps(status_data["disk"]),
        json.dumps(issues, ensure_ascii=False),
        suggestions
    ))
    
    log.info("[server_health] Saved server health record id=%s, status=%s", record_id, status)
    return record_id

def register(scheduler) -> None:
    """注册每日凌晨 02:30 的后台自动巡查任务。"""
    from appcore import scheduled_tasks
    scheduled_tasks.add_controlled_job(
        scheduler,
        "server_health_check",
        evaluate_and_save_record,
        "cron",
        hour=2,
        minute=30,
        id="server_health_check"
    )
