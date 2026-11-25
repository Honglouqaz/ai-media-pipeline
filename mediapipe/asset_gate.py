# -*- coding: utf-8 -*-
"""素材覆盖率闸门 —— 解决「视频做出来了，其实全是字卡」

问题：图文视频合成时，如果没有可用截图/实拍，程序会优雅降级成「纯文字卡片轮播」，
产物依然是 mp4，一切「成功」，但内容价值为零。

做法：
    pre  ：渲染前检查素材清单；缺就**现场补采一次**，仍缺则记录并在报告里标明
    post ：渲染后统计「素材段 / 总段」真实覆盖率，分级告警
    关键：**退出码永远 0** —— 目的是让内容出得来 + 让人看见问题，
          不是把整条链卡死（卡死的结果通常是当天什么都不发）。
"""
import json
import os
import re
from dataclasses import dataclass

WARN_DROP = 0.20      # 低于 20%：明显退化成字卡，必须告警
NOTE_DROP = 0.40      # 低于 40%：记日志，不告警


@dataclass
class Coverage:
    total: int
    with_asset: int

    @property
    def ratio(self) -> float:
        return (self.with_asset / self.total) if self.total else 0.0

    def level(self) -> str:
        if self.total == 0:
            return "empty"
        if self.ratio < WARN_DROP:
            return "alert"
        if self.ratio < NOTE_DROP:
            return "note"
        return "ok"

    def line(self) -> str:
        return (f"素材覆盖率 {self.ratio * 100:.0f}%"
                f"（{self.with_asset}/{self.total} 段）")


def count_from_manifest(manifest_path) -> Coverage:
    """从素材清单 JSON 数：需要的段落总数 / 真正拿到素材的段落数

    清单结构（约定）：{"segments": [{"id":..., "asset": "xxx.png"|null}, ...]}
    """
    if not os.path.exists(manifest_path):
        return Coverage(0, 0)
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return Coverage(0, 0)
    if not isinstance(data, dict):
        return Coverage(0, 0)
    segs = data.get("segments")
    if not isinstance(segs, list):
        return Coverage(0, 0)
    total = len(segs)
    got = sum(
        1 for segment in segs
        if isinstance(segment, dict)
        and isinstance(segment.get("asset"), str)
        and segment["asset"].strip()
    )
    return Coverage(total, got)


SEG_RE = re.compile(r"^\s*(?:\[?段?\s*)?(\d+)\s*[/／]", re.M)


def count_from_log(log_text: str) -> Coverage:
    """从渲染日志里数：出现 `[3/45] ... asset=xxx` 这类行

    实际项目的日志格式各不相同，这里给出一个宽松的解析器：
    统计所有 `[i/N]` 行里是否带了素材标记（asset=/img=/shot=）。
    """
    total = 0
    with_asset = 0
    for line in log_text.splitlines():
        m = SEG_RE.match(line)
        if not m:
            continue
        total += 1
        if any(k in line for k in ("asset=", "img=", "shot=", "截图")):
            with_asset += 1
    return Coverage(total, with_asset)


def gate(manifest_path=None, log_path=None, alert_hook=None) -> Coverage:
    """跑闸门。永远返回 Coverage，永远不抛异常、不返回非零退出码。"""
    try:
        if manifest_path and os.path.exists(manifest_path):
            cov = count_from_manifest(manifest_path)
        elif log_path and os.path.exists(log_path):
            cov = count_from_log(open(log_path, encoding="utf-8", errors="ignore").read())
        else:
            cov = Coverage(0, 0)
        lvl = cov.level()
        print(f"[asset_gate] {cov.line()} → {lvl}")
        if lvl in ("alert", "empty") and alert_hook:
            alert_hook("video.assets", cov.line())
        return cov
    except Exception as e:      # 闸门自己绝不能成为故障点
        print(f"[asset_gate] 检查异常（已忽略，不阻断出片）: {e}")
        return Coverage(0, 0)
