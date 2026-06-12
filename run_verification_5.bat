@echo off
echo --- Pushing remote_run_fresh_audit.py to production server ---
scp.exe -i C:/Users/admin/.ssh/CC.pem remote_run_fresh_audit.py root@172.16.254.106:/opt/autovideosrt/

echo --- Executing remote_run_fresh_audit.py in production ---
ssh.exe -i C:/Users/admin/.ssh/CC.pem root@172.16.254.106 "export PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright ; cd /opt/autovideosrt && /opt/autovideosrt/venv/bin/python3 remote_run_fresh_audit.py"
