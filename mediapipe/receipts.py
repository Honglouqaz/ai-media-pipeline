# -*- coding: utf-8 -*-
"""发布回执审计 —— 解决「日志说成功，其实没发出去」

问题：发布脚本打印「✅ 已发布」，但平台静默失败（转码中、审核中、登录态半失效）。
第二天你发现某个平台空了一天，而日志里全是绿的。

做法：每发一条就落一条**回执**（lane / 内容 id / 平台回执 id / 状态 / 时间），
审计时按「预期矩阵」对账：今天该发的每个通道，有没有一条成功回执。
"""
import json
import os
import time
import threading
from dataclasses import dataclass, asdict

_WRITE_LOCKS = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _write_lock(path):
    key = os.path.abspath(path)
    with _WRITE_LOCKS_GUARD:
        return _WRITE_LOCKS.setdefault(key, threading.Lock())


POSTED, PENDING, FAILED = "posted", "pending", "failed"


@dataclass
class Receipt:
    day: str                  # YYYYMMDD
    lane: str                 # 平台通道，如 wechat / bilibili / douyin
    item: str                 # 内容标识（本地文件名或稿件 id）
    status: str = PENDING
    remote_id: str = ""       # 平台返回的 id / url，是「真的发出去了」的硬证据
    ts: float = None
    note: str = ""

    def __post_init__(self):
        if self.ts is None:
            self.ts = time.time()


class ReceiptBook:
    def __init__(self, path="state/receipts.jsonl"):
        self.path = path

    def add(self, r: Receipt):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = (json.dumps(asdict(r), ensure_ascii=False) + "\n").encode("utf-8")
        # One append syscall per JSONL record avoids Python text-buffer interleaving.
        # The process-local lock also serializes threads sharing this path.
        with _write_lock(self.path):
            fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o666)
            try:
                written = os.write(fd, payload)
                if written != len(payload):
                    raise OSError("incomplete JSONL append")
            finally:
                os.close(fd)

    def load(self, day=None) -> list:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(d, dict):
                continue
            if day and d.get("day") != day:
                continue
            out.append(d)
        return out

    def audit(self, day, expected_lanes) -> dict:
        """对账：每个预期通道是否有 posted 且带 remote_id 的回执"""
        rows = self.load(day)
        got = {r["lane"]: r for r in rows if r.get("status") == POSTED and r.get("remote_id")}
        # 「有回执但拿不出平台 id」= 假成功（unverified），不能算作 missing，
        # 否则两类问题混成一个数字，看报告的人分不清「没发」和「说发了但没证据」
        suspicious = [r["lane"] for r in rows if r.get("status") == POSTED and not r.get("remote_id")]
        missing = [l for l in expected_lanes if l not in got and l not in suspicious]
        return {
            "day": day,
            "expected": len(expected_lanes),
            "confirmed": len(got),
            "missing": missing,                 # 压根没成功
            "unverified": suspicious,           # 说成功但拿不出平台 id —— 同样要查
            "ok": not missing and not suspicious,
        }
