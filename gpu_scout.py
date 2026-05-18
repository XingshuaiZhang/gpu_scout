#!/usr/bin/env python3
"""GPUScout: 监控 GPU 空闲状态并通过 Server酱推送微信通知."""

import argparse
import logging
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

    @property
    def is_idle(self) -> bool:
        return (
            self.mem_used_mib < _cfg["idle_memory_threshold_mib"]
            and self.util_pct < _cfg.get("idle_util_threshold_pct", 5)
        )

    def summary(self) -> str:
        status = "空闲 ✓" if self.is_idle else "占用"
        return (
            f"GPU {self.index} [{status}] "
            f"{self.mem_used_mib}/{self.mem_total_mib} MiB  "
            f"利用率 {self.util_pct}%"
        )


@dataclass
class GPUState:
    """Per-GPU tracking: previous idle status and last notification time."""
    was_idle: bool = False
    last_notified: float = 0.0


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
        ))
    pynvml.nvmlShutdown()
    return gpus


# ---------------------------------------------------------------------------
# Notification via Server酱
# ---------------------------------------------------------------------------

def send_serverchan(title: str, content: str) -> None:
    key = _cfg.get("serverchan_key", "")
    if not key or key == "YOUR_SENDKEY_HERE":
        logging.warning("Server酱 SendKey 未配置，跳过推送")
        return
    url = f"https://sctapi.ftqq.com/{key}.send"
    try:
        resp = requests.post(url, data={"title": title, "desp": content}, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            logging.info("推送成功: %s", title)
        else:
            logging.warning("推送返回异常: %s", data)
    except Exception as e:
        logging.error("推送失败: %s", e)


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

    interval = _cfg.get("poll_interval", 60)
    cooldown = _cfg.get("notify_cooldown_seconds", 300)
    threshold = _cfg.get("idle_memory_threshold_mib", 500)

    logging.info("GPUScout 启动  间隔=%ds  空闲阈值=%dMiB  冷却=%ds",
                 interval, threshold, cooldown)

    states: dict[int, GPUState] = {}

    # 启动时发一次总览
    gpus = query_gpus()
    idle_at_start = [g for g in gpus if g.is_idle]
    for g in gpus:
        states[g.index] = GPUState(was_idle=g.is_idle)

    if idle_at_start:
        lines = "\n".join(f"- {g.summary()}" for g in idle_at_start)
        send_serverchan(
            f"GPUScout 启动: {len(idle_at_start)} 张卡空闲",
            f"**空闲显卡列表**\n\n{lines}",
        )
        logging.info("启动时发现 %d 张空闲卡", len(idle_at_start))
    else:
        logging.info("启动时所有 GPU 均在使用中")

    while True:
        time.sleep(interval)
        try:
            gpus = query_gpus()
        except Exception as e:
            logging.error("查询 GPU 失败: %s", e)
            continue

        now = time.time()
        newly_idle: list[GPUInfo] = []

        for g in gpus:
            st = states.setdefault(g.index, GPUState(was_idle=g.is_idle))
            if g.is_idle and not st.was_idle:
                # 从忙碌变为空闲
                if now - st.last_notified >= cooldown:
                    newly_idle.append(g)
                    st.last_notified = now
            st.was_idle = g.is_idle

        if newly_idle:
            total = len(gpus)
            all_idle = [g for g in gpus if g.is_idle]
            new_lines = "\n".join(f"- {g.summary()}" for g in newly_idle)
            all_lines = "\n".join(f"- {g.summary()}" for g in all_idle)
            send_serverchan(
                f"GPUScout: {len(newly_idle)} 张卡空闲（当前共 {len(all_idle)}/{total} 张空闲）",
                f"**新增空闲**\n\n{new_lines}\n\n**当前所有空闲卡**\n\n{all_lines}",
            )
            for g in newly_idle:
                logging.info("GPU %d 变为空闲 (%d MiB used)", g.index, g.mem_used_mib)
        else:
            idle_count = sum(1 for g in gpus if g.is_idle)
            logging.info("巡检完成  空闲: %d/%d", idle_count, len(gpus))


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
