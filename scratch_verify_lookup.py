import sys
sys.path.append('/opt/autovideosrt')
from appcore.pushes import lookup_mk_id

print("=== Running Verification: lookup_mk_id ===")
code = 'face-framing-layered-wig-collection-rjc'
mk_id, status = lookup_mk_id(code)
print(f"lookup_verify_result: mk_id={mk_id}, status={status}")
if mk_id == 28908 and status == 'ok':
    print("SUCCESS: matched correct wig product ID 28908!")
else:
    print("FAILED: mismatch or query failure.")
