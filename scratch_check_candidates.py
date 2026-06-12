import sys
sys.path.insert(0, '/opt/autovideosrt')

from appcore.db import query

# Let's find details of '0faf7cf4486d5fc1e2c0c49172cae3c88e01bcc7ea570b9573c1768a3f566e36'
sql = """
    SELECT s.material_key, s.cumulative_90_spend
    FROM mingkong_material_daily_snapshots s
    WHERE s.material_key = '0faf7cf4486d5fc1e2c0c49172cae3c88e01bcc7ea570b9573c1768a3f566e36'
"""
print(query(sql))
