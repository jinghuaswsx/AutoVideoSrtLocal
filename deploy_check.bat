@echo off
ssh -i C:/Users/admin/.ssh/CC.pem root@172.16.254.106 "python3 -c \"with open('/opt/autovideosrt/appcore/link_check_fetcher.py') as f: print(''.join(f.readlines()[120:140]))\""
