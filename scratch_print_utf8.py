import json
import sys

def main():
    with open('G:/Code/AutoVideoSrtLocal/scratch/task_state_dump.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    summary = data.get('artifacts', {}).get('av_sync_audit', {}).get('diagnosis', {}).get('video_understanding', {}).get('summary', '')
    print("Video Understanding Summary:")
    print(summary)
    
    # Also check where "手机受控" was found in find_substring
    # Wait, did find_substring print "手机受控"? No, because it got decoded incorrectly or it wasn't matched because of mojibake?
    # Wait, the find_substring printed:
    # "FOUND at artifacts.av_sync_audit.diagnosis.video_understanding.summary"
    # because it matched "手机受控" or "半透明" or "数据开始"?
    # Wait, did it match? Let's check which target matched!
    # Ah! Let's write a python script to dump that part and print it to a file using UTF-8.

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
