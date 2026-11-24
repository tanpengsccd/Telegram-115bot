#!/usr/bin/env python3
"""独立测试消息长度限制功能（不依赖外部模块）"""


def format_batch_progress_message(total_count, task_results, max_chars=4096):
    """
    格式化批量下载进度消息，限制字符数并优先显示错误

    Args:
        total_count: 总任务数
        task_results: 任务结果列表 [{"index": 1, "status": "success/failed", "name": "...", "link": "..."}]
        max_chars: 最大字符数（Telegram单条消息限制4096字符）

    Returns:
        格式化的消息文本
    """
    # 分离成功和失败的任务
    failed_tasks = [t for t in task_results if t.get("status") == "failed"]
    success_tasks = [t for t in task_results if t.get("status") == "success"]

    # 构建消息头
    header_lines = [f"正在进行的总 {total_count} 个链接的下载任务："]

    # 如果有失败任务，添加失败提示头
    if failed_tasks:
        header_lines.append("❌ 离线下载超时，已添加到重试列表：")

    header_text = "\n".join(header_lines)

    # 预留字符空间
    # - 头部文本
    # - 省略提示（如果需要）：约80字符 × 2条（失败+成功）
    # - 安全边距：100字符
    reserved_chars = len(header_text) + 80 * 2 + 100
    available_chars = max_chars - reserved_chars

    # 优先显示错误链接
    displayed_failed = []
    displayed_success = []
    omitted_failed_count = 0
    omitted_success_count = 0
    current_length = 0

    # 第一步：优先添加所有失败任务的链接
    for task in failed_tasks:
        # 完整显示失败链接，不截断（方便用户复制）
        display_link = task.get("link", "")
        line_length = len(display_link) + 1  # +1 for newline

        # 检查是否超出可用字符数
        if current_length + line_length > available_chars:
            # 超出限制，剩余的失败任务将被省略
            omitted_failed_count = len(failed_tasks) - len(displayed_failed)
            break

        displayed_failed.append(display_link)
        current_length += line_length

    # 第二步：如果还有空间，添加成功任务（显示最新的）
    remaining_chars = available_chars - current_length

    if remaining_chars > 100:  # 至少有100字符才显示成功任务
        # 倒序遍历最新的成功任务
        for task in reversed(success_tasks):
            line = f"✅ {task['index']}/{total_count} [{task.get('name', '未知')}]"
            line_length = len(line) + 1  # +1 for newline

            # 检查是否超出剩余字符数
            if current_length + line_length > available_chars:
                break

            displayed_success.insert(0, line)  # 插入到开头保持顺序
            current_length += line_length

        omitted_success_count = len(success_tasks) - len(displayed_success)
    else:
        omitted_success_count = len(success_tasks)

    # 第三步：组装最终消息
    message_parts = [header_text]

    # 添加失败任务省略提示
    if omitted_failed_count > 0:
        message_parts.append(f"...此处省略 {omitted_failed_count} 条失败详情，下载完成后将发送完整错误日志TXT文件")

    # 添加失败任务列表
    if displayed_failed:
        message_parts.extend(displayed_failed)

    # 添加成功任务省略提示
    if omitted_success_count > 0 and displayed_success:
        message_parts.append(f"...此处省略 {omitted_success_count} 条成功详情...")

    # 添加成功任务列表
    if displayed_success:
        message_parts.extend(displayed_success)

    final_message = "\n".join(message_parts)

    # 最终安全检查：如果仍然超出4096，强制截断
    if len(final_message) > max_chars:
        truncate_pos = max_chars - 50  # 预留空间添加截断提示
        final_message = final_message[:truncate_pos] + "\n...(消息过长已截断)"

    return final_message


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
    print(f"是否超出4096限制: {'❌ 是' if len(message) > 4096 else '✅ 否'}")
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
    print(f"是否超出4096限制: {'❌ 是' if len(message) > 4096 else '✅ 否'}")

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
    print(f"是否在4096限制内: {'✅ 是' if len(message) <= 4096 else '❌ 否'}")

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
