# 统一链接提取逻辑 - 修复总结

## 📋 问题背景

用户发现 bot 无法识别带前缀的 ed2k 链接，例如：

```
链接: ed2k://|file|www.98T.la@-Cherry Bomb!.mp4|506460937|F76717019B1F807AAC695191384973CE|/
```

**问题原因**：
1. **直接粘贴消息**和 **txt 文件上传**使用了不同的链接提取逻辑
2. 原有逻辑只能处理纯链接行，无法识别带前缀的格式
3. ed2k 链接的文件名中包含空格（如 `"Cherry Bomb!.mp4"`），按空格分割后链接不完整

## ✅ 解决方案

### 1. 创建统一的链接提取函数

新增了两个核心函数，统一处理所有链接识别场景：

#### `line_contains_link(line: str) -> bool`
- 检查一行文本是否包含下载链接（包括带前缀的链接）
- 使用正则表达式检测链接，支持：
  - 纯链接行
  - 带前缀的链接（`链接:`、`磁力:` 等）
  - 文件名包含空格的 ed2k 链接

**位置**: [download_handler.py:491-526](app/handlers/download_handler.py#L491-L526)

#### `extract_links_from_lines(lines: list) -> list`
- 统一的链接提取函数，从文本行中提取所有有效的下载链接
- 支持三种提取模式（按优先级）：
  1. **整行验证**：检查整行是否是完整链接
  2. **正则提取**：使用正则表达式从文本中提取链接（新增）
  3. **按空格分割**：兜底方案，处理同行多个链接的情况

**位置**: [download_handler.py:529-588](app/handlers/download_handler.py#L529-L588)

### 2. 重构三个函数使用统一逻辑

#### ✅ `start_d_command()` - 直接粘贴消息处理
**修改位置**: [download_handler.py:213-215](app/handlers/download_handler.py#L213-L215)

**修改前**：
```python
# 47 行重复的链接提取逻辑
lines = message_text.split('\n')
valid_links = []
for line in lines:
    # ... 大量重复代码 ...
```

**修改后**：
```python
# 使用统一的链接提取函数
lines = message_text.split('\n')
valid_links = extract_links_from_lines(lines)
```

#### ✅ `handle_txt_file()` - txt 文件上传处理
**修改位置**: [download_handler.py:1891-1893](app/handlers/download_handler.py#L1891-L1893)

**修改前**：
```python
# 旧逻辑：只能识别纯链接
for line in lines:
    if is_valid_link(line) != DownloadUrlType.UNKNOWN:
        valid_links.append(...)
    # 无法识别带前缀的链接
```

**修改后**：
```python
# 使用统一的链接提取函数
lines = file_content.split('\n')
valid_links = extract_links_from_lines(lines)
```

#### ✅ `extract_folder_name_from_text()` - 文件夹名提取
**修改位置**: [download_handler.py:71-84](app/handlers/download_handler.py#L71-L84)

**修改前**：
```python
# 复杂的链接检测逻辑（28 行代码）
for line in lines:
    if is_valid_link(line) != DownloadUrlType.UNKNOWN:
        continue
    contains_link = False
    for fragment in line.split():
        if is_valid_link(fragment.strip()) != DownloadUrlType.UNKNOWN:
            contains_link = True
            break
    # ... 更多代码 ...
```

**修改后**：
```python
# 使用统一的链接检测函数（简洁明了）
for line in lines:
    if line_contains_link(line):
        continue
    non_link_lines.append(line)
```

#### ✅ txt 文件首行提取逻辑
**修改位置**: [download_handler.py:1926-1935](app/handlers/download_handler.py#L1926-L1935)

**修改前**：
```python
# 18 行代码检测链接
for line in lines:
    if is_valid_link(line) != DownloadUrlType.UNKNOWN:
        continue
    if line.startswith(('magnet:', 'ed2k://', ...)):
        continue
    is_link = False
    for fragment in line.split():
        # ... 更多检查 ...
```

**修改后**：
```python
# 使用统一的链接检测函数（5 行代码）
for line in lines:
    if not line_contains_link(line):
        first_non_link_line = line
        break
```

## 🎯 支持的链接格式

修复后，以下所有格式都能被正确识别：

### 直接粘贴消息
```
miru(坂道みる/坂道美琉)原档合集
链接: ed2k://|file|www.98T.la@-#kittyxkum#绯红小猫 3.17号户外自慰.mp4|425612533|7B2D6FE880E4B7BAB939F5D6C7030619|/
链接: ed2k://|file|www.98T.la@-Cherry Bomb!.mp4|506460937|F76717019B1F807AAC695191384973CE|/
磁力: magnet:?xt=urn:btih:1234567890ABCDEF1234567890ABCDEF12345678
Thunder: thunder://QUFodHRwOi8vZXhhbXBsZS5jb20vZmlsZS56aXBaWg==
20251008
```

**结果**：
- ✅ 识别 4 个链接（ed2k × 2, magnet × 1, thunder × 1）
- ✅ 文件夹名：`miru(坂道みる-坂道美琉)原档合集20251008`

### txt 文件内容
```
坂道美琉合集
链接: ed2k://|file|file1.mp4|123456|ABCD1234567890ABCDEF1234567890AB|/
magnet:?xt=urn:btih:1234567890ABCDEF1234567890ABCDEF12345678
链接: thunder://QUFodHRwOi8vZXhhbXBsZS5jb20vZmlsZS56aXBaWg==
```

**结果**：
- ✅ 识别 3 个链接（ed2k × 1, magnet × 1, thunder × 1）
- ✅ 文件夹名：`坂道美琉合集`

### 混合格式
```
测试标题
ed2k://|file|test1.mp4|123456|ABCD1234567890ABCDEF1234567890AB|/
链接: ed2k://|file|test2.mp4|654321|FEDCBA0987654321FEDCBA0987654321|/
magnet:?xt=urn:btih:1234567890ABCDEF1234567890ABCDEF12345678
底部说明
```

**结果**：
- ✅ 识别 3 个链接（ed2k × 2, magnet × 1）
- ✅ 文件夹名：`测试标题底部说明`

## 📊 测试验证

运行完整测试：
```bash
python3 test_unified_link_extraction.py
```

**测试场景**：
1. ✅ 直接粘贴带前缀的 ed2k 链接
2. ✅ txt 文件内容（混合三种链接类型）
3. ✅ 混合格式（纯链接 + 带前缀链接）
4. ✅ 纯链接（无文字）
5. ✅ `line_contains_link()` 函数单元测试

**测试结果**：✅ 所有测试通过

## 🔧 技术细节

### 正则表达式模式

```python
link_extraction_patterns = [
    # ed2k 链接：支持文件名中的任意非 | 字符（包括空格）
    (DownloadUrlType.ED2K, r'ed2k://\|file\|[^\|]+\|[0-9]+\|[a-fA-F0-9]{32}\|/?'),

    # magnet 链接：40位十六进制哈希值
    (DownloadUrlType.MAGNET, r'magnet:\?xt=urn:btih:[a-fA-F0-9]{40}(?:[&\?][^\s]*)?'),

    # thunder 链接：base64 编码
    (DownloadUrlType.THUNDER, r'thunder://[a-zA-Z0-9=]+'),
]
```

### 关键优化

1. **正则提取优先**：在按空格分割之前，先使用正则表达式提取链接
   - 解决了文件名包含空格导致的链接不完整问题
   - 支持 `"链接: ed2k://..."`、`"磁力: magnet:..."` 等带前缀格式

2. **统一检测逻辑**：`line_contains_link()` 函数
   - 避免代码重复
   - 确保所有链接检测使用相同的标准
   - 易于维护和扩展

3. **代码简化**：
   - `start_d_command()`: 减少 44 行代码
   - `handle_txt_file()`: 减少 21 行代码
   - `extract_folder_name_from_text()`: 减少 26 行代码
   - txt 首行提取: 减少 13 行代码
   - **总计减少 104 行重复代码**

## 📝 相关文件

- [app/handlers/download_handler.py](app/handlers/download_handler.py) - 主要修改
  - `line_contains_link()` 函数 (L491-526)
  - `extract_links_from_lines()` 函数 (L529-588)
  - `start_d_command()` 修改 (L213-215)
  - `handle_txt_file()` 修改 (L1891-1893, L1926-1935)
  - `extract_folder_name_from_text()` 修改 (L71-84)
- [test_unified_link_extraction.py](test_unified_link_extraction.py) - 完整测试用例
- [CLAUDE.md](CLAUDE.md) - 项目文档

## 🚀 使用说明

### 重启 bot
```bash
# Docker 环境
docker-compose restart

# 直接运行
python app/115bot.py
```

### 测试方法

**方法 1：直接粘贴**
1. 向 bot 发送包含 `"链接: ed2k://..."` 格式的消息
2. Bot 应该识别所有链接并提示选择保存分类

**方法 2：上传 txt 文件**
1. 创建包含带前缀链接的 txt 文件
2. 上传到 bot
3. Bot 应该识别所有链接并自动生成文件夹名

## ✅ 完成状态

- [x] 创建统一的链接提取函数 `extract_links_from_lines()`
- [x] 创建链接检测函数 `line_contains_link()`
- [x] 更新 `start_d_command()` 使用统一逻辑
- [x] 更新 `handle_txt_file()` 使用统一逻辑
- [x] 更新 `extract_folder_name_from_text()` 使用统一逻辑
- [x] 更新 txt 文件首行提取逻辑
- [x] 创建完整的测试用例（5 个场景）
- [x] 验证所有测试通过
- [x] 代码语法验证
- [x] 文档编写

**最后更新**: 2025-10-11
**修复人员**: Claude Code
**测试状态**: ✅ 所有测试通过
