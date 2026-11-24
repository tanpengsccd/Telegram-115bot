#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 txt 文件处理逻辑"""

import re
from datetime import datetime

def sanitize_folder_name(text: str) -> str:
    """清理文件夹名称"""
    if not text:
        return ""
    text = text.replace(":", "")
    invalid_chars = ['/', '\\', '?', '*', '"', '<', '>', '|']
    for char in invalid_chars:
        text = text.replace(char, "-")
    return text.strip()

def is_valid_link(link: str) -> bool:
    """检查是否是有效链接"""
    patterns = [
        r'^magnet:\?xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})(?:&.+)?$',
        r'^ed2k://\|file\|.+\|[0-9]+\|[a-fA-F0-9]{32}\|',
        r'^thunder://[a-zA-Z0-9=]+'
    ]
    for pattern in patterns:
        if re.match(pattern, link):
            return True
    return False

def test_txt_file_processing(file_content: str, file_name: str):
    """测试 txt 文件处理逻辑"""
    print(f"\n{'='*60}")
    print(f"测试文件: {file_name}")
    print(f"{'='*60}")

    # 提取文件名（不含扩展名）
    file_base_name = file_name.rsplit('.txt', 1)[0]
    print(f"文件基础名: {file_base_name}")

    # 解析链接
    lines = file_content.split('\n')
    valid_links = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if is_valid_link(line):
            valid_links.append(line)
            continue

        fragments = line.split()
        for fragment in fragments:
            fragment = fragment.strip()
            if fragment and is_valid_link(fragment):
                valid_links.append(fragment)

    print(f"找到链接数: {len(valid_links)}")

    # 提取首行非链接内容
    first_non_link_line = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 检查是否是链接（整行检查）
        if is_valid_link(line):
            continue
        # 检查是否以已知链接前缀开头（避免误判格式不完整的链接）
        if line.startswith(('magnet:', 'ed2k://', 'ED2K://', 'thunder://')):
            continue
        # 检查行内是否包含链接
        is_link = False
        for fragment in line.split():
            if is_valid_link(fragment.strip()):
                is_link = True
                break
        if not is_link:
            first_non_link_line = line
            break

    print(f"首行非链接内容: '{first_non_link_line}'")

    # 构建文件夹名
    if first_non_link_line:
        custom_folder_name = f"{file_base_name}{first_non_link_line}"
    else:
        if len(file_base_name) < 8:
            timestamp = datetime.now().strftime("%Y%m%d%H%M")
            custom_folder_name = f"{file_base_name}{timestamp}"
        else:
            custom_folder_name = file_base_name

    custom_folder_name = sanitize_folder_name(custom_folder_name)
    print(f"生成文件夹名: '{custom_folder_name}'")

    return len(valid_links), custom_folder_name

# 测试用例1: 有首行和尾行
test1 = """坂道美琉合集
magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1
ed2k://|file|movie1.mp4|1234567890|abcdef1234567890abcdef123456|/
20251010
"""

# 测试用例2: 只有链接，文件名长度 >= 8
test2 = """magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1
ed2k://|file|movie2.mp4|1234567890|abcdef1234567890abcdef123456|/
"""

# 测试用例3: 只有链接，文件名长度 < 8
test3 = """magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1
ed2k://|file|movie3.mp4|1234567890|abcdef1234567890abcdef123456|/
"""

# 测试用例4: 有特殊字符
test4 = """麻豆/传媒*合集?
magnet:?xt=urn:btih:abc123def456abc123def456abc123def456abc1
"""

print("\n" + "="*60)
print("开始测试 txt 文件处理逻辑")
print("="*60)

test_txt_file_processing(test1, "麻豆合集.txt")
test_txt_file_processing(test2, "LongFileName.txt")
test_txt_file_processing(test3, "Short.txt")
test_txt_file_processing(test4, "特殊字符.txt")

print("\n" + "="*60)
print("测试完成！")
print("="*60)
