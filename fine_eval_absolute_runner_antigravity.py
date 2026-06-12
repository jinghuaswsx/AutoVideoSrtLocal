import sys
import argparse
import uuid
import logging

sys.path.insert(0, '/opt/autovideosrt')

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

from appcore.db import query, execute
from appcore.mingkong_fine_ai_auto_evaluation import (
    _claim_running_record,
    _run_candidate,
    _candidate_key,
    _source_rank
)

# Monkeypatch uuid.uuid4 to generate evaluation_run_id starting with 'eval_antigravity_'
original_uuid4 = uuid.uuid4
class AntigravityUUID:
    @property
    def hex(self):
        return "antigravity_" + original_uuid4().hex[:20]

uuid.uuid4 = lambda: AntigravityUUID()

def main():
    parser = argparse.ArgumentParser(description="Antigravity Mingkong Fine AI Direct Evaluation Runner")
    parser.add_argument("--offset", type=int, default=5, help="Offset for candidates query")
    parser.add_argument("--limit", type=int, default=5, help="Limit for candidates query")
    args = parser.parse_args()

    print(f"Starting Fine AI Evaluation for candidates with OFFSET {args.offset}, LIMIT {args.limit}")

    # Fetch unique, unevaluated candidates
    sql = """
        SELECT s.*
        FROM mingkong_material_daily_snapshots s
        INNER JOIN (
          SELECT MIN(s2.id) as min_id
          FROM mingkong_material_daily_snapshots s2
          LEFT JOIN mingkong_fine_ai_auto_evaluations a ON a.material_key = s2.material_key
          WHERE a.id IS NULL
          GROUP BY s2.material_key
        ) grp ON s.id = grp.min_id
        ORDER BY s.cumulative_90_spend DESC, s.id ASC
        LIMIT %s OFFSET %s
    """
    candidates = query(sql, (args.limit, args.offset))
    print(f"Found {len(candidates)} candidates.")

    results = []
    for index, row in enumerate(candidates, start=1):
        material_key = _candidate_key(row)
        video_path = row.get("video_path")
        product_url = row.get("product_url")
        print(f"\nProcessing Candidate {index}/{len(candidates)}: id={row.get('id')}, material_key={material_key}, spend={row.get('cumulative_90_spend')}")
        print(f"Product Link: {product_url}")
        print(f"Video Path: {video_path}")

        # Claim the running record in mingkong_fine_ai_auto_evaluations
        source_bucket = "top500_90d_spend"
        source_rank = _source_rank(row, args.offset + index)
        
        claimed = _claim_running_record(
            row,
            scheduled_run_id=None,
            source_bucket=source_bucket,
            source_rank=source_rank
        )
        if not claimed:
            print(f"Candidate {material_key} was already claimed or processed, skipping.")
            continue

        print(f"Successfully claimed candidate {material_key}. Running evaluation...")

        # Run candidate evaluation
        try:
            res = _run_candidate(
                row,
                scheduled_run_id=None,
                source_bucket=source_bucket,
                source_rank=source_rank,
                already_claimed=True
            )
            print(f"Finished evaluation for {material_key}. Result: {res}")
            results.append({
                "material_key": material_key,
                "status": res.get("status"),
                "evaluation_run_id": res.get("evaluation_run_id"),
                "error": res.get("error")
            })
        except Exception as exc:
            print(f"Exception during evaluation of {material_key}: {exc}")
            results.append({
                "material_key": material_key,
                "status": "failed",
                "evaluation_run_id": None,
                "error": str(exc)
            })

    print("\n================ EVALUATION SUMMARY ================")
    for idx, r in enumerate(results, start=1):
        print(f"{idx}. Material: {r['material_key']}, Status: {r['status']}, Run ID: {r['evaluation_run_id']}, Error: {r['error']}")
    print("====================================================\n")

if __name__ == "__main__":
    main()
