# -*- coding: utf-8 -*-
"""ETA-aware adaptive polling controller for 115 offline download tasks."""

import time
from typing import Dict, Optional, Tuple

INITIAL_WAIT_SECONDS = 3.0
MIN_POLL_INTERVAL = 5.0
MAX_ACTIVE_INTERVAL = 90.0
DEFAULT_STALL_TIMEOUT_SECONDS = 600.0  # 10 minutes without any progress -> early stop
STALL_BACKOFF_SCHEDULE = (15.0, 30.0, 60.0, 120.0, 180.0)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class AdaptivePollController:
    """
    Dynamically computes the next poll interval based on task progress velocity (ETA)
    while respecting 115 API rate limits and stopping early when tasks stall.
    """

    def __init__(
        self,
        stall_timeout: float = DEFAULT_STALL_TIMEOUT_SECONDS,
        min_interval: float = MIN_POLL_INTERVAL,
        max_active_interval: float = MAX_ACTIVE_INTERVAL,
        start_time: Optional[float] = None,
    ):
        self.stall_timeout = float(stall_timeout)
        self.min_interval = float(min_interval)
        self.max_active_interval = float(max_active_interval)
        now = time.monotonic() if start_time is None else float(start_time)
        self.last_check_ts: Optional[float] = None
        self.last_progress_ts: float = now
        self.prev_percents: Dict[str, float] = {}
        self.stall_rounds: int = 0
        self.total_rounds: int = 0

    def next_wait(
        self,
        active_percents: Dict[str, float],
        now: Optional[float] = None,
    ) -> Tuple[float, bool, str]:
        """
        Compute next wait duration after observing incomplete tasks' percentages.

        Args:
            active_percents: mapping of {link_key: percent_done (0..99.9)} for still-running tasks.
            now: optional monotonic timestamp for deterministic unit testing.

        Returns:
            (wait_seconds, should_early_stop, reason)
        """
        now_ts = time.monotonic() if now is None else float(now)
        self.total_rounds += 1

        if not active_percents:
            self.prev_percents = {}
            self.last_check_ts = now_ts
            return 0.0, False, "所有任务已结束"

        dt = (now_ts - self.last_check_ts) if self.last_check_ts is not None else 0.0

        # Detect any progress: either some previously tracked task finished (dropped out),
        # or at least one active task advanced in percentage.
        dropped_completed = bool(self.prev_percents) and any(
            k not in active_percents for k in self.prev_percents
        )

        etas = []
        any_percent_up = False
        max_percent = 0.0

        for key, pct_raw in active_percents.items():
            try:
                pct = max(0.0, min(99.9, float(pct_raw or 0.0)))
            except (TypeError, ValueError):
                pct = 0.0
            if pct > max_percent:
                max_percent = pct

            if key in self.prev_percents and dt > 0:
                delta = pct - self.prev_percents[key]
                if delta > 0.01:
                    any_percent_up = True
                    rate = delta / dt  # percent per second
                    remaining = max(0.1, 100.0 - pct)
                    etas.append(remaining / rate)

        first_round_with_progress = (self.total_rounds == 1 and max_percent > 0.0)
        made_progress = dropped_completed or any_percent_up or first_round_with_progress

        # Save snapshot for next round
        self.prev_percents = {
            k: max(0.0, min(99.9, float(v or 0.0))) for k, v in active_percents.items()
        }
        self.last_check_ts = now_ts

        if made_progress:
            self.last_progress_ts = now_ts
            self.stall_rounds = 0

            if etas:
                min_eta = min(etas)
                max_eta = max(etas)
                # Sprint mode: whole batch or the closest-to-finish task is within ~30s of 100%
                if max_eta <= 30.0:
                    wait = _clamp(max_eta * 0.6, self.min_interval, 15.0)
                    return wait, False, f"临近全部完成(预计约{int(round(max_eta))}秒)"
                if max_percent >= 90.0 and min_eta <= 30.0:
                    wait = _clamp(min_eta * 0.6, self.min_interval, 15.0)
                    return wait, False, f"高进度冲刺(最高{int(max_percent)}%, 预计约{int(round(min_eta))}秒)"

                # Steady progress mode: scale poll interval gently with ETA
                ref_eta = max_eta if len(etas) == len(active_percents) else min_eta
                wait = _clamp(ref_eta * 0.4, 15.0, self.max_active_interval)
                return wait, False, f"稳定下载中(预计剩余约{int(round(ref_eta))}秒)"

            # Progress detected without measurable velocity (e.g. round 1 or another task just finished)
            if max_percent >= 90.0:
                return 8.0, False, f"高进度接近完成(最高{int(max_percent)}%)"
            return 15.0, False, f"进度更新中(最高{int(max_percent)}%)"

        # No progress observed in this round -> backoff & check stall early-stop
        self.stall_rounds += 1
        stalled_for = now_ts - self.last_progress_ts
        if stalled_for >= self.stall_timeout:
            return 0.0, True, f"连续 {int(stalled_for)} 秒无进度变化，提前结束等待"

        idx = min(self.stall_rounds - 1, len(STALL_BACKOFF_SCHEDULE) - 1)
        backoff_wait = STALL_BACKOFF_SCHEDULE[idx]
        remaining_before_stop = max(self.min_interval, self.stall_timeout - stalled_for)
        wait = _clamp(min(backoff_wait, remaining_before_stop), self.min_interval, STALL_BACKOFF_SCHEDULE[-1])
        return wait, False, f"暂无进度变化(已停滞{int(stalled_for)}秒，退避等待{int(round(wait))}秒)"
