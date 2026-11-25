# -*- coding: utf-8 -*-
"""登录态熔断器 —— 解决「凭据过期 → 每个平台各重试一遍 → 整条链拖到天亮」

问题：平台登录态（cookie/session）过期时，发布器会失败并重试；如果不管，
同一条链上的每个平台都会各自失败一遍，还会在第二天继续撞墙。

做法：每个「通道(lane)」维护自己的状态机：
    ok ──失败达阈值──> 熔断(blocked) ──到点──> 半开(half_open) ──成功──> ok
熔断期间该通道直接跳过，不再浪费一次真实请求（真实请求会拉高风控分数）。
"""
import json
import os
import time
from dataclasses import dataclass, asdict, field

OK, FAILING, BLOCKED = "ok", "failing", "blocked"


@dataclass
class LaneState:
    lane: str
    status: str = OK
    fail_count: int = 0
    last_fail_ts: float = 0.0
    last_ok_ts: float = 0.0
    blocked_until: float = 0.0
    last_reason: str = ""
    history: list = field(default_factory=list)   # [(ts, ok, reason)] 只留最近 20 条

    def is_blocked(self, now=None) -> bool:
        now = time.time() if now is None else now
        return self.status == BLOCKED and now < self.blocked_until

    def seconds_left(self, now=None) -> int:
        now = time.time() if now is None else now
        return max(0, int(self.blocked_until - now))


class AuthBreaker:
    """一个通道一把锁。持久化到 JSON，进程重启不丢状态。"""

    def __init__(self, path="state/lanes.json", threshold=3, cool_seconds=6 * 3600):
        self.path = path
        self.threshold = threshold
        self.cool_seconds = cool_seconds
        self.lanes = {}
        self._load()

    # ---------- 持久化 ----------
    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            self.lanes = {k: LaneState(**v) for k, v in raw.items()}
        except Exception:
            self.lanes = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # Replace atomically so an interrupted write does not truncate the last
        # known-good state file. Keep the temporary file beside the target so
        # os.replace stays on the same filesystem.
        tmp_path = self.path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({k: asdict(v) for k, v in self.lanes.items()},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def lane(self, name) -> LaneState:
        return self.lanes.setdefault(name, LaneState(lane=name))

    # ---------- 状态流转 ----------
    def can_run(self, name, now=None) -> tuple:
        """返回 (是否放行, 原因)"""
        st = self.lane(name)
        now = time.time() if now is None else now
        if st.is_blocked(now):
            return False, f"熔断中，剩余 {st.seconds_left(now)}s（上次：{st.last_reason}）"
        if st.status == BLOCKED and now >= st.blocked_until:
            st.status = FAILING        # 半开：允许试探一次
            st.fail_count = self.threshold - 1
            self.save()
        return True, "ok"

    def record_ok(self, name, now=None):
        st = self.lane(name)
        now = time.time() if now is None else now
        st.status, st.fail_count, st.last_ok_ts, st.blocked_until = OK, 0, now, 0.0
        st.history = (st.history + [[now, True, ""]])[-20:]
        self.save()

    def record_fail(self, name, reason="", now=None):
        st = self.lane(name)
        now = time.time() if now is None else now
        st.fail_count += 1
        st.last_fail_ts, st.last_reason = now, reason[:200]
        st.history = (st.history + [[now, False, st.last_reason]])[-20:]
        if st.fail_count >= self.threshold:
            st.status = BLOCKED
            st.blocked_until = now + self.cool_seconds
        else:
            st.status = FAILING
        self.save()
        return st

    def reset(self, name):
        st = self.lane(name)
        st.status, st.fail_count, st.blocked_until, st.last_reason = OK, 0, 0.0, ""
        self.save()

    def report(self, now=None) -> list:
        now = time.time() if now is None else now
        out = []
        for k in sorted(self.lanes):
            st = self.lanes[k]
            out.append({
                "lane": k, "status": st.status, "fail_count": st.fail_count,
                "blocked": st.is_blocked(now), "left_s": st.seconds_left(now),
                "last_reason": st.last_reason,
            })
        return out
