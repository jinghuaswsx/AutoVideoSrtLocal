@echo off
echo --- Pushing remote_inspect_download.py to server ---
scp.exe -i C:/Users/admin/.ssh/CC.pem remote_inspect_download.py root@172.16.254.106:/opt/autovideosrt/

echo --- Executing remote_inspect_download.py ---
ssh.exe -i C:/Users/admin/.ssh/CC.pem root@172.16.254.106 "/opt/autovideosrt/venv/bin/python3 /opt/autovideosrt/remote_inspect_download.py"
