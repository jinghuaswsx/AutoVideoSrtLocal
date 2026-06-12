import json

def main():
    with open('G:/Code/AutoVideoSrtLocal/scratch/task_state_dump.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Check translations[5].description and other descriptions
    for i, t in enumerate(data.get('translations', [])):
        desc = t.get('description')
        if desc:
            print(f"Translation {i} description: {desc}")
            print("=" * 50)
            
    # Also check shot_notes
    shot_notes = data.get('shot_notes', {})
    for i, s in enumerate(shot_notes.get('sentences', [])):
        for f in ['scene', 'action', 'description', 'visual_observation']:
            val = s.get(f)
            if val and len(val) > 100:
                print(f"Shot Note Sentence {i} field '{f}': {val}")
                print("=" * 50)

if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
