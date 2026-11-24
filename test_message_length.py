#!/usr/bin/env python3
"""测试消息长度限制功能"""

import sys
sys.path.insert(0, '/root/Telegram-115bot/app')
sys.path.insert(0, '/root/Telegram-115bot/app/handlers')

from download_handler import format_batch_progress_message


def test_small_batch():
    """测试小批量任务（所有内容都能显示）"""
    print("=" * 60)
    print("测试1: 小批量任务（5个成功，2个失败）")
    print("=" * 60)

    task_results = [
        {"index": 1, "status": "success", "name": "文件1.mp4", "link": "ed2k://..."},
        {"index": 2, "status": "success", "name": "文件2.mp4", "link": "ed2k://..."},
        {"index": 3, "status": "failed", "name": "文件3.mp4", "link": "ed2k://|file|文件3.mp4|123456789|ABCDEF123456|/"},
        {"index": 4, "status": "success", "name": "文件4.mp4", "link": "ed2k://..."},
        {"index": 5, "status": "success", "name": "文件5.mp4", "link": "ed2k://..."},
        {"index": 6, "status": "failed", "name": "文件6.mp4", "link": "magnet:?xt=urn:btih:1234567890abcdef1234567890abcdef12345678&dn=test"},
        {"index": 7, "status": "success", "name": "文件7.mp4", "link": "ed2k://..."},
    ]

    message = format_batch_progress_message(7, task_results)
    print(message)
    print(f"\n消息长度: {len(message)} 字符")
    print()


def test_large_batch_with_many_failures():
    """测试大批量任务，包含很多失败"""
    print("=" * 60)
    print("测试2: 大批量任务（100个任务，50个失败）")
    print("=" * 60)

    task_results = []
    for i in range(1, 101):
        if i % 2 == 0:  # 偶数失败
            task_results.append({
                "index": i,
                "status": "failed",
                "name": f"非常长的文件名称_{i}_这是一个超级长的文件名用来测试消息长度限制.mp4",
                "link": f"ed2k://|file|非常长的文件名称_{i}_这是一个超级长的文件名用来测试消息长度限制.mp4|{i*1000000}|{'A'*32}|/"
            })
        else:  # 奇数成功
            task_results.append({
                "index": i,
                "status": "success",
                "name": f"文件_{i}.mp4",
                "link": f"ed2k://|file|文件_{i}.mp4|{i*1000000}|{'B'*32}|/"
            })

    message = format_batch_progress_message(100, task_results)
    print(message[:500] + "\n...[中间内容省略]...\n" + message[-500:])
    print(f"\n消息长度: {len(message)} 字符")
    print(f"是否超出4096限制: {'是' if len(message) > 4096 else '否'}")
    print()


def test_extreme_case():
    """测试极端情况：200个任务，全部失败，每个链接都很长"""
    print("=" * 60)
    print("测试3: 极端情况（200个任务，全部失败，超长链接）")
    print("=" * 60)

    task_results = []
    for i in range(1, 201):
        # 生成超长链接（300+字符）
        long_link = f"ed2k://|file|这是一个超级超级超级超级超级超级超级超级超级长的文件名称_{i}_用来测试极端情况下的消息长度限制功能是否正常工作.mp4|{i*10000000}|{'C'*32}|h={'D'*32}|/"
        task_results.append({
            "index": i,
            "status": "failed",
            "name": f"超长文件_{i}.mp4",
            "link": long_link
        })

    message = format_batch_progress_message(200, task_results)
    print(message[:500] + "\n...[中间内容省略]...\n" + message[-500:])
    print(f"\n消息长度: {len(message)} 字符")
    print(f"是否超出4096限制: {'是' if len(message) > 4096 else '否'}")

    # 统计实际显示的失败任务数量
    failed_lines = [line for line in message.split('\n') if line.startswith('ed2k://') or line.startswith('magnet:')]
    print(f"实际显示的失败任务数量: {len(failed_lines)}/{200}")
    print()


def test_character_count_accuracy():
    """测试字符数计算的准确性"""
    print("=" * 60)
    print("测试4: 验证字符数计算准确性")
    print("=" * 60)

    # 创建一个会导致超长消息的任务列表
    task_results = []
    for i in range(1, 151):
        task_results.append({
            "index": i,
            "status": "failed",
            "name": f"文件_{i}.mp4",
            "link": f"ed2k://|file|文件名称_{i}_测试字符数计算.mp4|{i*1000000}|{'E'*32}|h={'F'*32}|/".ljust(200, 'X')  # 每个链接固定200字符
        })

    message = format_batch_progress_message(150, task_results)
    print(f"消息长度: {len(message)} 字符")
    print(f"是否在4096限制内: {'是' if len(message) <= 4096 else '否'}")

    if len(message) > 4096:
        print(f"⚠️ 警告：消息超出限制 {len(message) - 4096} 字符！")
    else:
        print("✅ 通过：消息长度在4096字符限制内")
    print()


if __name__ == "__main__":
    print("开始测试消息长度限制功能\n")

    test_small_batch()
    test_large_batch_with_many_failures()
    test_extreme_case()
    test_character_count_accuracy()

    print("=" * 60)
    print("所有测试完成！")
    print("=" * 60)
