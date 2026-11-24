#!/usr/bin/env python3
"""
测试 115 API 速率限制器
验证请求间隔是否符合 1请求/秒 的限制
"""
import sys
import os
import time
from datetime import datetime

# 添加路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, 'app'))
sys.path.append(os.path.join(current_dir, 'app', 'utils'))
sys.path.append(os.path.join(current_dir, 'app', 'core'))

# 导入 API 类
import init
from app.core.open_115 import OpenAPI_115

def test_rate_limiter():
    """测试速率限制器"""
    print("=" * 60)
    print("测试 115 API 速率限制器 (1请求/秒)")
    print("=" * 60)

    try:
        # 初始化 API 客户端
        api = OpenAPI_115()
        print(f"✓ API 客户端初始化成功")
        print(f"  速率限制间隔: {api._rate_limit_interval}秒")
        print()

        # 执行5次连续请求，测试速率限制
        test_requests = 5
        request_times = []

        print(f"开始执行 {test_requests} 次连续请求测试...")
        print("-" * 60)

        test_start_time = time.time()

        for i in range(test_requests):
            request_start = time.time()
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            # 调用一个轻量级的API（获取用户信息）
            try:
                result = api.get_user_info()
                request_end = time.time()
                elapsed = request_end - request_start
                request_times.append({
                    'index': i + 1,
                    'timestamp': timestamp,
                    'start': request_start,
                    'end': request_end,
                    'elapsed': elapsed,
                    'success': 'code' not in result or result.get('code') == 0
                })

                status = "✓ 成功" if request_times[-1]['success'] else "✗ 失败"
                print(f"请求 #{i+1:02d} | {timestamp} | 耗时: {elapsed:.3f}s | {status}")

            except Exception as e:
                request_end = time.time()
                print(f"请求 #{i+1:02d} | {timestamp} | ✗ 异常: {e}")
                request_times.append({
                    'index': i + 1,
                    'timestamp': timestamp,
                    'start': request_start,
                    'end': request_end,
                    'elapsed': 0,
                    'success': False
                })

        test_end_time = time.time()

        print("-" * 60)
        print()

        # 分析请求间隔
        print("速率限制分析:")
        print("-" * 60)

        if len(request_times) >= 2:
            # 计算请求间隔（从一次请求开始到下一次请求开始）
            print("请求间隔详情:")
            intervals = []
            for i in range(1, len(request_times)):
                interval = request_times[i]['start'] - request_times[i-1]['start']
                intervals.append(interval)
                print(f"  请求 #{i} -> #{i+1}: {interval:.3f}秒")

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

            if total_test_time >= min_expected_time and all_intervals_valid:
                print(f"✓ 速率限制器工作正常！")
                print(f"  实际耗时: {total_test_time:.3f}秒 >= 预期: {min_expected_time:.1f}秒")
                print(f"  所有请求间隔 >= 1秒")
            else:
                print(f"✗ 警告：速率限制器可能未生效")
                if total_test_time < min_expected_time:
                    print(f"  实际耗时 ({total_test_time:.3f}s) < 预期最小耗时 ({min_expected_time:.1f}s)")
                if not all_intervals_valid:
                    print(f"  部分请求间隔 < 1秒")

        print("=" * 60)

    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True

if __name__ == "__main__":
    success = test_rate_limiter()
    sys.exit(0 if success else 1)
