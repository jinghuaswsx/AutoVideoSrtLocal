import json
import sys

def find_substring(obj, target, path=""):
    if isinstance(obj, str):
        if target in obj:
            print(f"FOUND target '{target}' at {path}:")
            print(obj[:300])
            print("=" * 60)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            find_substring(v, target, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            find_substring(item, target, f"{path}[{idx}]")

def main():
    with open('G:/Code/AutoVideoSrtLocal/scratch/task_state_dump.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    for word in ["数据", "开始", "符号", "结束", "受控", "框架"]:
        find_substring(data, word)

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
