@echo off
echo --- Pushing remote_check_image_valid.py to server ---
scp.exe -i C:/Users/admin/.ssh/CC.pem remote_check_image_valid.py root@172.16.254.106:/opt/autovideosrt/

echo --- Executing remote_check_image_valid.py ---
ssh.exe -i C:/Users/admin/.ssh/CC.pem root@172.16.254.106 "/opt/autovideosrt/venv/bin/python3 /opt/autovideosrt/remote_check_image_valid.py"
