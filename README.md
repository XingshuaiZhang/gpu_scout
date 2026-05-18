# GPUScout

监控 NVIDIA GPU 空闲状态，当有显卡变为空闲时通过微信推送通知（Server酱）。

适用场景：多人共用 GPU 服务器，随时掌握哪张卡可以用。

---

## 目录结构

```
GPUScout/
├── gpu_scout.py          # 主程序
├── config.example.yaml   # 配置模板（提交到 git）
├── config.yaml           # 实际配置（含 SendKey，已 gitignore）
├── install.sh            # 一键安装脚本
├── requirements.txt      # Python 依赖
├── gpu_scout.log         # 运行日志（自动生成，已 gitignore）
└── README.md
```

> `config.yaml` 和 `*.log` 已加入 `.gitignore`，不会被提交。

---

## 依赖

- Python 3.10+
- NVIDIA 驱动（`nvidia-smi` 可用）

---

## 快速开始

### 方式一：一键安装（推荐）

```bash
git clone <your-repo-url>
cd GPUScout
bash install.sh
```

脚本会自动完成：
- 安装 Python 依赖
- 从模板创建 `config.yaml`（若不存在）
- 根据当前用户和路径生成 systemd 服务文件并安装

安装完成后，填写 SendKey 再启动服务：

```bash
# 1. 编辑配置，填入 SendKey
nano config.yaml

# 2. 启动服务
sudo systemctl start gpu_scout
```

---

### 方式二：手动安装

**第 1 步：安装依赖**

```bash
pip install -r requirements.txt
```

**第 2 步：获取 Server酱 SendKey**

1. 访问 [https://sct.ftqq.com](https://sct.ftqq.com)
2. 用微信扫码登录，复制 **SendKey**（格式：`SCT...`）
3. 手机微信关注「方糖」公众号（推送从这里发出）

**第 3 步：创建配置文件**

```bash
cp config.example.yaml config.yaml
nano config.yaml          # 填写 serverchan_key
```

配置项说明：

```yaml
serverchan_key: "SCTxxxxxxxxxxxxxxxx"  # Server酱 SendKey

poll_interval: 60              # 轮询间隔（秒）
idle_memory_threshold_mib: 500 # 显存低于此值视为空闲
notify_cooldown_seconds: 300   # 同一张卡再次通知的最短间隔（秒）
log_file: "gpu_scout.log"      # 日志路径，相对路径以配置文件所在目录为基准
```

**第 4 步：验证推送**

```bash
python3 -c "
import yaml, requests
cfg = yaml.safe_load(open('config.yaml'))
r = requests.post(
    f'https://sctapi.ftqq.com/{cfg[\"serverchan_key\"]}.send',
    data={'title': 'GPUScout 测试', 'desp': '推送正常'}
)
print(r.json())
"
```

返回 `{'code': 0, ...}` 表示成功。

**第 5 步：启动**

前台运行（测试用）：

```bash
python3 gpu_scout.py monitor
```

安装为 systemd 服务（开机自启）：

```bash
bash install.sh
sudo systemctl start gpu_scout
```

---

## 命令参考

| 命令 | 说明 |
|------|------|
| `python3 gpu_scout.py status` | 一次性打印所有 GPU 当前状态 |
| `python3 gpu_scout.py monitor` | 持续监控，有空闲卡时推送通知 |
| `python3 gpu_scout.py --config /path/to/config.yaml monitor` | 指定配置文件路径 |

---

## 推送规则

| 触发时机 | 通知内容 |
|----------|----------|
| 服务启动，已有空闲卡 | 列出所有当前空闲卡 |
| 某卡从「占用」变为「空闲」 | 列出新增空闲卡 |
| 同一张卡短时间内反复变化 | 受冷却时间限制，不重复推送 |

冷却时间由 `notify_cooldown_seconds` 控制，默认 5 分钟。

---

## 查看日志

```bash
# 实时跟踪
tail -f gpu_scout.log

# systemd 日志
journalctl -u gpu_scout -f
```

日志格式示例：

```
2026-05-18 14:32:01  INFO  GPUScout 启动  间隔=60s  空闲阈值=500MiB  冷却=300s
2026-05-18 14:32:01  INFO  启动时所有 GPU 均在使用中
2026-05-18 14:33:01  INFO  巡检完成  空闲: 0/10
2026-05-18 14:34:01  INFO  GPU 3 变为空闲 (210 MiB used)
2026-05-18 14:34:01  INFO  推送成功: GPUScout: 1 张卡空闲了
```

---

## 服务管理

```bash
sudo systemctl start gpu_scout     # 启动
sudo systemctl stop gpu_scout      # 停止
sudo systemctl restart gpu_scout   # 重启（修改配置后执行）
sudo systemctl status gpu_scout    # 查看运行状态
sudo systemctl disable gpu_scout   # 取消开机自启
```

---

## 常见问题

**Q: 推送没有收到？**
- 检查 `config.yaml` 中的 `serverchan_key` 是否填写正确（不含多余空格）
- 微信是否关注了「方糖」公众号
- 执行上方的测试命令，观察返回值

**Q: 想降低 CPU 占用？**
- 将 `poll_interval` 改大，如 `120`（2 分钟），重启服务生效

**Q: 显存有几百 MiB 占用，但实际没有任务（如 jupyter kernel 占用）？**
- 调高 `idle_memory_threshold_mib`，如改为 `1000`

**Q: 多台机器共用同一份配置？**
- `log_file` 使用相对路径（默认 `gpu_scout.log`），配置文件放项目根目录即可，路径自动适配
