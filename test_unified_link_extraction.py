#!/usr/bin/env python3
"""
统一链接提取逻辑测试

测试场景：
1. 直接粘贴带前缀的 ed2k 链接
2. txt 文件中的带前缀链接
3. 文件夹名提取（应跳过带前缀的链接行）
4. 混合场景（magnet, ed2k, thunder）
"""

import re
from enum import Enum

class DownloadUrlType(Enum):
    UNKNOWN = 0
    MAGNET = 1
    ED2K = 2
    THUNDER = 3

def is_valid_link(link: str) -> DownloadUrlType:
    patterns = {
        DownloadUrlType.MAGNET: r'^magnet:\?xt=urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})(?:&.+)?$',
        DownloadUrlType.ED2K: r'^ed2k://\|file\|.+\|[0-9]+\|[a-fA-F0-9]{32}\|',
        DownloadUrlType.THUNDER: r'^thunder://[a-zA-Z0-9=]+'
    }
    for url_type, pattern in patterns.items():
        if re.match(pattern, link):
            return url_type
    return DownloadUrlType.UNKNOWN

def line_contains_link(line: str) -> bool:
    """检查一行文本是否包含下载链接（包括带前缀的链接）"""
    line = line.strip()
    if not line:
        return False

    if is_valid_link(line) != DownloadUrlType.UNKNOWN:
        return True

    link_detection_patterns = [
        r'ed2k://\|file\|[^\|]+\|[0-9]+\|[a-fA-F0-9]{32}\|/?',
        r'magnet:\?xt=urn:btih:[a-fA-F0-9]{40}(?:[&\?][^\s]*)?',
        r'thunder://[a-zA-Z0-9=]+',
    ]

    for pattern in link_detection_patterns:
        if re.search(pattern, line, re.IGNORECASE):
            return True

    for fragment in line.split():
        if is_valid_link(fragment.strip()) != DownloadUrlType.UNKNOWN:
            return True

    return False

def extract_links_from_lines(lines: list) -> list:
    """统一的链接提取函数"""
    valid_links = []

    link_extraction_patterns = [
        (DownloadUrlType.ED2K, r'ed2k://\|file\|[^\|]+\|[0-9]+\|[a-fA-F0-9]{32}\|/?'),
        (DownloadUrlType.MAGNET, r'magnet:\?xt=urn:btih:[a-fA-F0-9]{40}(?:[&\?][^\s]*)?'),
        (DownloadUrlType.THUNDER, r'thunder://[a-zA-Z0-9=]+'),
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue

        dl_url_type = is_valid_link(line)
        if dl_url_type != DownloadUrlType.UNKNOWN:
            valid_links.append({"link": line, "type": dl_url_type})
            continue

        found_in_line = False
        for link_type, pattern in link_extraction_patterns:
            matches = re.findall(pattern, line, re.IGNORECASE)
            for match in matches:
                if is_valid_link(match) != DownloadUrlType.UNKNOWN:
                    valid_links.append({"link": match, "type": link_type})
                    found_in_line = True

        if found_in_line:
            continue

        fragments = line.split()
        for fragment in fragments:
            fragment = fragment.strip()
            if not fragment:
                continue
            dl_url_type = is_valid_link(fragment)
            if dl_url_type != DownloadUrlType.UNKNOWN:
                valid_links.append({"link": fragment, "type": dl_url_type})

    return valid_links

def sanitize_folder_name(text: str) -> str:
    """清理文件夹名称"""
    text = text.replace(':', '')
    for char in r'/\?*"<>|':
        text = text.replace(char, '-')
    return text.strip()

def extract_folder_name_from_text(message_text: str) -> str:
    """从文本中提取首行+尾行非链接文字作为文件夹名"""
    lines = message_text.strip().split('\n')

    non_link_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line_contains_link(line):
            continue

        non_link_lines.append(line)

    if not non_link_lines:
        return ""

    if len(non_link_lines) == 1:
        folder_name = non_link_lines[0]
    else:
        folder_name = non_link_lines[0] + non_link_lines[-1]

    return sanitize_folder_name(folder_name)


# ============== 测试用例 ==============

def test_case_1_paste_with_prefix():
    """测试场景1：直接粘贴带前缀的 ed2k 链接"""
    print("="*70)
    print("测试场景1：直接粘贴带前缀的 ed2k 链接")
    print("="*70)

    message = """miru(坂道みる/坂道美琉)原档合集
链接: ed2k://|file|www.98T.la@-#kittyxkum#绯红小猫 3.17号户外自慰.mp4|425612533|7B2D6FE880E4B7BAB939F5D6C7030619|/
链接: ed2k://|file|www.98T.la@-Cherry Bomb!.mp4|506460937|F76717019B1F807AAC695191384973CE|/
链接: ed2k://|file|www.98T.la@-Angel Bunny.mp4|642377582|8C1647679EFAA6DDD7B5B8E7E8BCA58F|/
20251008"""

    lines = message.split('\n')
    links = extract_links_from_lines(lines)
    folder_name = extract_folder_name_from_text(message)

    print(f"\n✅ 提取到 {len(links)} 个链接:")
    for i, link_info in enumerate(links, 1):
        link = link_info['link']
        if link.startswith('ed2k://'):
            parts = link.split('|')
            filename = parts[2] if len(parts) >= 3 else "未知"
            print(f"  {i}. [{link_info['type'].name}] {filename}")
        else:
            print(f"  {i}. [{link_info['type'].name}] {link[:60]}...")

    print(f"\n✅ 文件夹名: {folder_name}")
    print(f"   预期: miru(坂道みる-坂道美琉)原档合集20251008")
    print(f"   匹配: {'✅' if folder_name == 'miru(坂道みる-坂道美琉)原档合集20251008' else '❌'}")


def test_case_2_txt_file():
    """测试场景2：txt 文件内容"""
    print("\n" + "="*70)
    print("测试场景2：txt 文件内容")
    print("="*70)

    txt_content = """坂道美琉合集
链接: ed2k://|file|file1.mp4|123456|ABCD1234567890ABCDEF1234567890AB|/
magnet:?xt=urn:btih:1234567890ABCDEF1234567890ABCDEF12345678
链接: thunder://QUFodHRwOi8vZXhhbXBsZS5jb20vZmlsZS56aXBaWg=="""

    lines = txt_content.split('\n')
    links = extract_links_from_lines(lines)
    folder_name = extract_folder_name_from_text(txt_content)

    print(f"\n✅ 提取到 {len(links)} 个链接:")
    for i, link_info in enumerate(links, 1):
        print(f"  {i}. [{link_info['type'].name}] {link_info['link'][:60]}...")

    print(f"\n✅ 文件夹名: {folder_name}")
    print(f"   预期: 坂道美琉合集")
    print(f"   匹配: {'✅' if folder_name == '坂道美琉合集' else '❌'}")


def test_case_3_mixed_formats():
    """测试场景3：混合格式（纯链接 + 带前缀链接）"""
    print("\n" + "="*70)
    print("测试场景3：混合格式")
    print("="*70)

    message = """测试标题
ed2k://|file|test1.mp4|123456|ABCD1234567890ABCDEF1234567890AB|/
链接: ed2k://|file|test2.mp4|654321|FEDCBA0987654321FEDCBA0987654321|/
magnet:?xt=urn:btih:1234567890ABCDEF1234567890ABCDEF12345678
底部说明"""

    lines = message.split('\n')
    links = extract_links_from_lines(lines)
    folder_name = extract_folder_name_from_text(message)

    print(f"\n✅ 提取到 {len(links)} 个链接:")
    for i, link_info in enumerate(links, 1):
        print(f"  {i}. [{link_info['type'].name}]")

    print(f"\n✅ 文件夹名: {folder_name}")
    print(f"   预期: 测试标题底部说明")
    print(f"   匹配: {'✅' if folder_name == '测试标题底部说明' else '❌'}")


def test_case_4_only_links():
    """测试场景4：纯链接（无文字）"""
    print("\n" + "="*70)
    print("测试场景4：纯链接（无文字）")
    print("="*70)

    message = """链接: ed2k://|file|file1.mp4|123456|ABCD1234567890ABCDEF1234567890AB|/
链接: ed2k://|file|file2.mp4|654321|FEDCBA0987654321FEDCBA0987654321|/"""

    lines = message.split('\n')
    links = extract_links_from_lines(lines)
    folder_name = extract_folder_name_from_text(message)

    print(f"\n✅ 提取到 {len(links)} 个链接")
    print(f"✅ 文件夹名: '{folder_name}'")
    print(f"   预期: 空字符串")
    print(f"   匹配: {'✅' if folder_name == '' else '❌'}")


def test_case_5_line_contains_link():
    """测试场景5：line_contains_link 函数"""
    print("\n" + "="*70)
    print("测试场景5：line_contains_link 函数")
    print("="*70)

    test_lines = [
        ("纯文本行", False),
        ("ed2k://|file|test.mp4|123456|ABCD1234567890ABCDEF1234567890AB|/", True),
        ("链接: ed2k://|file|test.mp4|123456|ABCD1234567890ABCDEF1234567890AB|/", True),
        ("magnet:?xt=urn:btih:1234567890ABCDEF1234567890ABCDEF12345678", True),
        ("下载链接: magnet:?xt=urn:btih:1234567890ABCDEF1234567890ABCDEF12345678", True),
        ("标题文字 无链接", False),
    ]

    print("\n测试结果:")
    all_pass = True
    for line, expected in test_lines:
        result = line_contains_link(line)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_pass = False
        print(f"  {status} {line[:50]:50s} => {result} (预期: {expected})")

    print(f"\n总体结果: {'✅ 全部通过' if all_pass else '❌ 存在失败'}")


# 运行所有测试
if __name__ == "__main__":
    test_case_1_paste_with_prefix()
    test_case_2_txt_file()
    test_case_3_mixed_formats()
    test_case_4_only_links()
    test_case_5_line_contains_link()

    print("\n" + "="*70)
    print("🎉 所有测试完成！")
    print("="*70)
