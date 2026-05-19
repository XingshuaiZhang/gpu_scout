#!/usr/bin/env python3
"""GPUScout: 监控 GPU 空闲状态并通过 Server酱推送微信通知."""

import argparse
import logging
import pwd
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml
import pynvml

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class GPUInfo:
    index: int
    name: str
    mem_used_mib: int
    mem_total_mib: int
    util_pct: int
    users: list[str] = field(default_factory=list)

    @property
    def is_idle(self) -> bool:
        return (
            self.mem_used_mib < _cfg["idle_memory_threshold_mib"]
            and self.util_pct < _cfg.get("idle_util_threshold_pct", 5)
        )

    @property
    def mem_pct(self) -> int:
        return self.mem_used_mib * 100 // self.mem_total_mib if self.mem_total_mib else 0

    def summary(self) -> str:
        status = "空闲 ✓" if self.is_idle else "占用"
        return (
            f"GPU {self.index} [{status}] "
            f"{self.mem_used_mib}/{self.mem_total_mib} MiB  "
            f"利用率 {self.util_pct}%"
        )

    def notify_line(self) -> str:
        mark = "🟢 空闲" if self.is_idle else "🔴 占用"
        users = ",".join(self.users) if self.users else "—"
        return (
            f"- [GPU {self.index}] {mark}  "
            f"{self.mem_used_mib}/{self.mem_total_mib} MiB ({self.mem_pct}%)  "
            f"用户: {users}"
        )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_cfg: dict = {}

def load_config(path: str) -> None:
    global _cfg
    config_path = Path(path).resolve()
    with open(config_path) as f:
        _cfg = yaml.safe_load(f)
    # Resolve log_file relative to the config file's directory
    log_file = _cfg.get("log_file", "")
    if log_file and not Path(log_file).is_absolute():
        _cfg["log_file"] = str(config_path.parent / log_file)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        handlers=handlers,
    )


# ---------------------------------------------------------------------------
# GPU querying
# ---------------------------------------------------------------------------

def _process_users(handle) -> list[str]:
    """Return a sorted, de-duplicated list of usernames running on this GPU."""
    procs = []
    for fn in (pynvml.nvmlDeviceGetComputeRunningProcesses,
               pynvml.nvmlDeviceGetGraphicsRunningProcesses):
        try:
            procs += fn(handle)
        except pynvml.NVMLError:
            pass
    seen: set[str] = set()
    for p in procs:
        try:
            with open(f"/proc/{p.pid}/status") as f:
                for line in f:
                    if line.startswith("Uid:"):
                        uid = int(line.split()[1])
                        seen.add(pwd.getpwuid(uid).pw_name)
                        break
        except (FileNotFoundError, KeyError, ProcessLookupError, PermissionError):
            continue
    return sorted(seen)


def query_gpus() -> list[GPUInfo]:
    pynvml.nvmlInit()
    count = pynvml.nvmlDeviceGetCount()
    gpus = []
    for i in range(count):
        h = pynvml.nvmlDeviceGetHandleByIndex(i)
        name = pynvml.nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode()
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
        except pynvml.NVMLError:
            util = -1
        gpus.append(GPUInfo(
            index=i,
            name=name,
            mem_used_mib=mem.used // (1024 ** 2),
            mem_total_mib=mem.total // (1024 ** 2),
            util_pct=util,
            users=_process_users(h),
        ))
    pynvml.nvmlShutdown()
    return gpus


# ---------------------------------------------------------------------------
# Notification via Server酱
# ---------------------------------------------------------------------------

def send_serverchan(title: str, content: str) -> bool:
    key = _cfg.get("serverchan_key", "")
    if not key or key == "YOUR_SENDKEY_HERE":
        logging.warning("Server酱 SendKey 未配置，跳过推送")
        return False
    url = f"https://sctapi.ftqq.com/{key}.send"
    try:
        resp = requests.post(url, data={"title": title, "desp": content}, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            logging.info("推送成功: %s", title)
            return True
        logging.warning("推送返回异常: %s", data)
        return False
    except Exception as e:
        logging.error("推送失败: %s", e)
        return False


# ---------------------------------------------------------------------------
# Status command (one-shot print)
# ---------------------------------------------------------------------------

def cmd_status() -> None:
    load_config(args_global.config)
    gpus = query_gpus()
    idle = [g for g in gpus if g.is_idle]
    busy = [g for g in gpus if not g.is_idle]
    print(f"\n{'='*50}")
    print(f"共 {len(gpus)} 张 GPU  |  空闲: {len(idle)}  占用: {len(busy)}")
    print(f"空闲阈值: 显存 < {_cfg['idle_memory_threshold_mib']} MiB  且  利用率 < {_cfg.get('idle_util_threshold_pct', 5)}%")
    print(f"{'='*50}")
    for g in gpus:
        print(" ", g.summary())
    print()


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------

def cmd_monitor() -> None:
    load_config(args_global.config)
    setup_logging(_cfg.get("log_file"))

    poll_interval = _cfg.get("poll_interval", 60)
    cooldown = _cfg.get("notify_cooldown_seconds", 4 * 3600)
    threshold = _cfg.get("idle_memory_threshold_mib", 500)

    logging.info("GPUScout 启动  轮询=%ds  休眠=%ds  空闲阈值=%dMiB",
                 poll_interval, cooldown, threshold)

    while True:
        # ---- 活跃期：每 poll_interval 秒巡检一次 ----
        try:
            gpus = query_gpus()
        except Exception as e:
            logging.error("查询 GPU 失败: %s", e)
            time.sleep(poll_interval)
            continue

        total = len(gpus)
        idle = [g for g in gpus if g.is_idle]
        logging.info("巡检完成  空闲: %d/%d", len(idle), total)

        if idle:
            title = f"当前有 {len(idle)}/{total} 张空闲显卡"
            content = "\n".join(g.notify_line() for g in gpus)
            if send_serverchan(title, content):
                # ---- 休眠期 ----
                logging.info("推送成功，进入休眠 %ds", cooldown)
                time.sleep(cooldown)
                logging.info("休眠结束，恢复活跃期")
                continue
            logging.info("推送未成功，留在活跃期")

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

args_global: argparse.Namespace

def main() -> None:
    global args_global
    parser = argparse.ArgumentParser(description="GPUScout - GPU 空闲监控与推送")
    parser.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"),
                        help="配置文件路径")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="查看当前 GPU 状态（一次性输出）")
    sub.add_parser("monitor", help="持续监控并推送通知（默认命令）")

    args_global = parser.parse_args()

    if args_global.cmd == "status":
        cmd_status()
    else:
        cmd_monitor()


if __name__ == "__main__":
    main()
