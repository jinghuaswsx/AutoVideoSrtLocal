@echo off
echo --- Pushing remote_inspect_all_extracted.py to server ---
scp.exe -i C:/Users/admin/.ssh/CC.pem remote_inspect_all_extracted.py root@172.16.254.106:/opt/autovideosrt-test/

echo --- Executing remote_inspect_all_extracted.py ---
ssh.exe -i C:/Users/admin/.ssh/CC.pem root@172.16.254.106 "export PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright ; cd /opt/autovideosrt-test && /opt/autovideosrt/venv/bin/python3 remote_inspect_all_extracted.py"
