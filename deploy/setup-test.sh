#!/bin/bash
# 测试环境部署脚本
# 首次运行会初始化整个测试环境（克隆代码、创建数据库、虚拟环境）
# 后续运行只做 pull + 重启
set -e

APP_DIR=/opt/autovideosrt-test
PROD_DIR=/opt/autovideosrt
SERVICE=autovideosrt-test
DB_NAME=auto_video_test
PROD_DB=auto_video

# ---------- 首次初始化 ----------
if [ ! -d "$APP_DIR/.git" ]; then
  echo "=== 首次初始化测试环境 ==="

  # 1. 克隆代码
  git clone "$PROD_DIR" "$APP_DIR"
  cd "$APP_DIR"
  git remote set-url origin "$(cd $PROD_DIR && git remote get-url origin)"

  # 2. 创建虚拟环境
  python3 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt gunicorn -i https://pypi.org/simple/

  # 3. 复制 .env 并修改为测试配置
  cp "$PROD_DIR/.env" "$APP_DIR/.env"
  # 替换数据库名
  if grep -q "^DB_NAME=" "$APP_DIR/.env"; then
    sed -i "s/^DB_NAME=.*/DB_NAME=$DB_NAME/" "$APP_DIR/.env"
  else
    echo "DB_NAME=$DB_NAME" >> "$APP_DIR/.env"
  fi
  # 替换端口
  sed -i "s|LOCAL_SERVER_BASE_URL=.*|LOCAL_SERVER_BASE_URL=http://127.0.0.1:9999|" "$APP_DIR/.env"

  # 4. 创建测试数据库并从生产库复刻
  echo "=== 复刻数据库 $PROD_DB -> $DB_NAME ==="
  # 从生产 .env 读取数据库密码
  DB_PASS=$(grep '^DB_PASSWORD=' "$PROD_DIR/.env" | cut -d'=' -f2-)
  DB_USER_ENV=$(grep '^DB_USER=' "$PROD_DIR/.env" | cut -d'=' -f2- || echo "root")
  DB_USER_ENV=${DB_USER_ENV:-root}
  MYSQL_OPTS="-u $DB_USER_ENV"
  if [ -n "$DB_PASS" ]; then
    MYSQL_OPTS="$MYSQL_OPTS -p$DB_PASS"
  fi
  mysql $MYSQL_OPTS -e "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
  mysqldump $MYSQL_OPTS "$PROD_DB" | mysql $MYSQL_OPTS "$DB_NAME"
  echo "数据库复刻完成"

  # 5. 创建必要目录
  mkdir -p "$APP_DIR/output" "$APP_DIR/uploads"

  # 6. 安装 systemd service
  cp "$APP_DIR/deploy/autovideosrt-test.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable "$SERVICE"

  echo "=== 初始化完成 ==="
fi

# ---------- 常规部署 ----------
cd "$APP_DIR"
git pull

source venv/bin/activate
pip install -r requirements.txt gunicorn -i https://pypi.org/simple/

# 运行数据库迁移
DB_NAME=$DB_NAME python db/migrate.py

systemctl restart "$SERVICE"
systemctl status "$SERVICE" --no-pager
echo "测试环境部署完成。访问端口 9999。"
