#!/usr/bin/env python3
"""
简单测试 115 API 速率限制器逻辑
不依赖完整环境，仅测试速率限制的核心逻辑
"""
import time
from datetime import datetime


class MockRateLimiter:
    """模拟速率限制器"""
    def __init__(self):
        self._last_request_time = 0
        self._rate_limit_interval = 1.0  # 秒

    def wait_if_needed(self):
        """如果需要，等待以满足速率限制"""
        current_time = time.time()
        if self._last_request_time > 0:  # 不是第一次请求
            time_since_last_request = current_time - self._last_request_time
            if time_since_last_request < self._rate_limit_interval:
                sleep_time = self._rate_limit_interval - time_since_last_request
                print(f"    [Rate Limit] 等待 {sleep_time:.3f}秒...")
                time.sleep(sleep_time)

        # 更新最后请求时间（在请求发出之前更新）
        self._last_request_time = time.time()


def test_rate_limiter():
    """测试速率限制器"""
    print("=" * 60)
    print("测试速率限制器逻辑 (1请求/秒)")
    print("=" * 60)
    print()

    limiter = MockRateLimiter()
    print(f"✓ 速率限制器初始化")
    print(f"  限制间隔: {limiter._rate_limit_interval}秒")
    print()

    # 执行5次连续请求
    test_requests = 5
    request_times = []

    print(f"开始执行 {test_requests} 次连续模拟请求...")
    print("-" * 60)

    test_start_time = time.time()

    for i in range(test_requests):
        # 记录真实的请求开始时间（在速率限制之前）
        request_start = time.time()
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        print(f"请求 #{i+1:02d} 准备 | {timestamp}")

        # 应用速率限制（这会等待直到可以发送请求）
        limiter.wait_if_needed()

        # 记录实际发送请求的时间点（速率限制后）
        actual_send_time = time.time()

        # 模拟API调用（实际处理时间）
        time.sleep(0.05)  # 模拟50ms的处理时间

        request_end = time.time()
        elapsed = request_end - request_start

        request_times.append({
            'index': i + 1,
            'start': request_start,
            'actual_send': actual_send_time,  # 实际发送时间（速率限制后）
            'end': request_end,
            'elapsed': elapsed
        })

        print(f"    完成 | 耗时: {elapsed:.3f}秒")
        print()

    test_end_time = time.time()

    print("-" * 60)
    print()

    # 分析请求间隔
    print("速率限制分析:")
    print("-" * 60)

    if len(request_times) >= 2:
        # 计算请求间隔（从一次实际发送到下一次实际发送）
        print("请求间隔详情（基于实际发送时间）:")
        intervals = []
        for i in range(1, len(request_times)):
            interval = request_times[i]['actual_send'] - request_times[i-1]['actual_send']
            intervals.append(interval)
            status = "✓" if interval >= 0.95 else "✗"
            print(f"  请求 #{i} -> #{i+1}: {interval:.3f}秒 {status}")

        print()
        total_test_time = test_end_time - test_start_time
        avg_interval = sum(intervals) / len(intervals) if intervals else 0

        print(f"总请求数: {len(request_times)}")
        print(f"总耗时: {total_test_time:.3f}秒")
        print(f"平均请求间隔: {avg_interval:.3f}秒")
        print(f"最小间隔: {min(intervals):.3f}秒" if intervals else "N/A")
        print(f"最大间隔: {max(intervals):.3f}秒" if intervals else "N/A")

        # 检查是否符合 1请求/秒 的限制
        min_expected_time = (len(request_times) - 1) * 1.0  # 除了第一次，每次至少间隔1秒
        print()
        print(f"预期最小总耗时: {min_expected_time:.1f}秒 (基于1请求/秒限制)")

        # 检查所有间隔是否都 >= 1秒（允许0.05秒误差）
        all_intervals_valid = all(interval >= 0.95 for interval in intervals)

        print()
        if total_test_time >= min_expected_time and all_intervals_valid:
            print(f"✓✓✓ 速率限制器工作正常！")
            print(f"  实际耗时: {total_test_time:.3f}秒 >= 预期: {min_expected_time:.1f}秒")
            print(f"  所有请求间隔 >= 1秒")
            result = True
        else:
            print(f"✗✗✗ 警告：速率限制器未正常工作")
            if total_test_time < min_expected_time:
                print(f"  实际耗时 ({total_test_time:.3f}s) < 预期最小耗时 ({min_expected_time:.1f}s)")
            if not all_intervals_valid:
                print(f"  部分请求间隔 < 1秒")
            result = False

    print("=" * 60)
    return result


if __name__ == "__main__":
    import sys
    success = test_rate_limiter()
    sys.exit(0 if success else 1)
