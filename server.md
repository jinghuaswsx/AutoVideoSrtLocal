# Server Info

Updated: 2026-04-01

Internal use only. This file records the production server access information for AutoVideoSrt.

## SSH Info

| Item | Value |
| --- | --- |
| SSH alias | `openclaw-noobird` |
| Server IP | `14.103.220.208` |
| SSH user | `root` |
| SSH port | `22` |
| SSH key file | `C:\Users\admin\.ssh\openclaw-noobird.pem` |

## Production

| Item | Value |
| --- | --- |
| Deploy directory | `/opt/autovideosrt` |
| systemd service | `autovideosrt.service` |
| App listen port | `8888` |
| nginx external mapping | Not configured. External access goes directly to app port `8888`. |
| Public URL | `http://14.103.220.208:8888` |
| Gunicorn entry | `main:app` |
| Gunicorn worker | `eventlet`, 1 worker, timeout 300s |
| venv path | `/opt/autovideosrt/venv` |

## Deploy Steps

```bash
ssh -i "C:\Users\admin\.ssh\openclaw-noobird.pem" root@14.103.220.208
cd /opt/autovideosrt
git pull
systemctl restart autovideosrt
systemctl status autovideosrt --no-pager
```

或者直接一行（本地执行）：

```bash
ssh -i "C:\Users\admin\.ssh\openclaw-noobird.pem" root@14.103.220.208 "cd /opt/autovideosrt && git pull && systemctl restart autovideosrt"
```

## Notes

1. 本项目和 `ad_kaogujia_web` 部署在同一台服务器，互不干扰。
2. 没有配置 nginx 反向代理，直接通过 8888 端口对外。
3. 代码仓库：`https://github.com/jinghuaswsx/AutoVideoSrt`
