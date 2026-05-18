#!/usr/bin/env bash
# GPUScout 安装脚本：安装依赖、生成 systemd user 服务并启用（无需 sudo）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="gpu_scout"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/${SERVICE_NAME}.service"

# ---- 检测环境 ----

PYTHON_BIN="$(which python3)"

echo "==> 安装目录:  $SCRIPT_DIR"
echo "==> Python:    $PYTHON_BIN"
echo "==> 服务文件:  $SERVICE_FILE"

# ---- 安装 Python 依赖 ----

echo ""
echo "==> 安装 Python 依赖..."
"$PYTHON_BIN" -m pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
echo "    依赖安装完成"

# ---- 初始化配置文件 ----

if [[ ! -f "$SCRIPT_DIR/config.yaml" ]]; then
    echo ""
    echo "==> 未找到 config.yaml，从模板创建..."
    cp "$SCRIPT_DIR/config.example.yaml" "$SCRIPT_DIR/config.yaml"
    echo "    已创建 config.yaml，请填写 SendKey 后再启动服务："
    echo "    编辑：$SCRIPT_DIR/config.yaml"
fi

# ---- 生成 systemd user service 文件 ----

echo ""
echo "==> 生成 ${SERVICE_NAME}.service..."

mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=GPUScout GPU Idle Monitor
After=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${PYTHON_BIN} ${SCRIPT_DIR}/gpu_scout.py monitor
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF

# ---- 启用服务 ----

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"

echo ""
echo "======================================"
echo " 安装完成！"
echo "======================================"
echo ""
echo " 下一步："
echo "   1. 编辑配置：$SCRIPT_DIR/config.yaml"
echo "      填写 serverchan_key（Server酱 SendKey）"
echo ""
echo "   2. 启动服务："
echo "      systemctl --user start $SERVICE_NAME"
echo ""
echo "   3. 查看状态："
echo "      systemctl --user status $SERVICE_NAME"
echo "      tail -f $SCRIPT_DIR/gpu_scout.log"
echo ""
