#!/bin/bash
set -e
APP_DIR=/opt/autovideosrt

# Pull latest code
cd $APP_DIR
git pull

# Install/update deps
source venv/bin/activate
pip install -r requirements.txt gunicorn -i https://pypi.org/simple/

# Run DB migration
python db/migrate.py

# Create admin user if not exists
python db/create_admin.py

# Restart service
systemctl restart autovideosrt
systemctl status autovideosrt --no-pager
curl -fsS http://127.0.0.1/ >/dev/null
echo "Deploy complete. Running on port 80."
