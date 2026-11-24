# 批量任务系统更新说明

## 更新日期：2025-10-12

## 主要改进

### 1. 修复时区显示问题 ✅

**问题**：批量任务总结消息显示的时间不是本地时区，而是 UTC 时间。

**原因**：数据库使用 SQLite 的 `CURRENT_TIMESTAMP`，默认存储 UTC 时间字符串。

**解决方案**：
- 将所有时间字段从 `DATETIME` 类型改为 `INTEGER` 类型
- 使用 Unix 时间戳（整数）存储时间
- 读取时使用 `datetime.fromtimestamp()` 自动转换为本地时区

**涉及字段**：
- `batch_task_group.created_at` - 任务组创建时间
- `batch_task_group.updated_at` - 任务组更新时间
- `batch_task_item.created_at` - 任务项创建时间
- `batch_task_item.last_retry_at` - 最后重试时间
- `batch_task_item.next_retry_at` - 下次重试时间
- `batch_task_item.completed_at` - 完成时间

### 2. 添加重试任务总结消息 ✅

**问题**：第一次批量下载完成后有总结消息，但后续重试任务完成后没有总结。

**解决方案**：
- 在 `batch_task_retry.py` 中新增 `send_retry_summary()` 函数
- 每次重试任务检查完成后自动发送总结消息
- 消息包含完整的时间统计、重试次数、下次重试时间

**新增功能**：
```python
def send_retry_summary(group_id: str, folder_name: str):
    """发送重试任务的总结消息给用户"""
```

### 3. 数据库初始化优化 ✅

**改进**：不再每次启动都清空数据库，只在需要时重置。

**使用方法**：
```bash
# 正常启动（保留数据）
python app/115bot.py

# 重置数据库后启动
RESET_SQL=true python app/115bot.py

# Docker 启动时重置
docker-compose run -e RESET_SQL=true 115bot
```

**支持的 RESET_SQL 值**：
- `true`, `1`, `yes` - 清空并重建表
- 其他任何值或未设置 - 保留现有数据

## 消息格式示例

### 首次批量下载完成（失败 > 5 个）

```
📄 kittyxkum【182g1307v149配额】失败重试 的批量下载任务
首次添加: 2025-10-12 17:13:58
距首次添加已过: 15分钟
此为第 1 次尝试
此次批量离线任务持续时间: 15分钟

此次下载任务数量统计：
败: 71 / 续: 0 / 完: 1
总 72

现在将重新添加失败的任务
下次检查时间: 10分钟后
你也可[删除]该离线任务，无操作将继续尝试
```

附带错误日志文件：`kittyxkum【182g1307v149配额】失败重试_20251012_173025_errors.txt`

### 第2次重试完成

```
📄 kittyxkum【182g1307v149配额】失败重试 的批量下载任务
首次添加: 2025-10-12 17:13:58
距首次添加已过: 1小时
此为第 2 次尝试
此次批量离线任务持续时间: 1小时

此次下载任务数量统计：
败: 50 / 续: 0 / 完: 22
总 72

现在将重新添加失败的任务
下次检查时间: 1小时后
你也可[删除]该离线任务，无操作将继续尝试
```

附带错误日志文件：`kittyxkum【182g1307v149配额】失败重试_20251012_181530_retry_2_errors.txt`

### 达到最大重试次数（第5次）

```
📄 kittyxkum【182g1307v149配额】失败重试 的批量下载任务
首次添加: 2025-10-12 17:13:58
距首次添加已过: 2天
此为第 5 次尝试
此次批量离线任务持续时间: 2天

此次下载任务数量统计：
败: 10 / 续: 0 / 完: 62
总 72

⚠️ 已达到最大重试次数（5次）
失败的任务将被标记为死任务
请手动检查磁力链接或更换资源
```

## 错误日志文件格式

### 简化前（旧格式）
```
任务 #9/144
链接: ed2k://|file|www.98T.la@-Cherry Bomb!.mp4|506460937|...
错误: 离线下载超时
状态: 已自动添加到重试列表
------------------------------------------------------------
任务 #11/144
链接: ed2k://|file|www.98T.la@-Angel Bunny.mp4|642377582|...
错误: 离线下载超时
状态: 已自动添加到重试列表
------------------------------------------------------------
```

### 简化后（新格式）
```
错误: 离线下载超时
状态: 已自动添加到重试列表
ed2k://|file|www.98T.la@-Cherry Bomb!.mp4|506460937|...
ed2k://|file|www.98T.la@-Angel Bunny.mp4|642377582|...
```

## 重试策略

| 重试次数 | 下次重试时间 |
|---------|------------|
| 第1次   | 10分钟后    |
| 第2次   | 1小时后     |
| 第3次   | 10小时后    |
| 第4次   | 1天后      |
| 第5次   | 10天后     |
| 第6次   | 标记为死任务 |

## 技术细节

### 数据库表结构变更

**batch_task_group 表**：
```sql
CREATE TABLE IF NOT EXISTS batch_task_group (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT UNIQUE NOT NULL,
    folder_name TEXT NOT NULL,
    save_path TEXT NOT NULL,
    total_count INTEGER NOT NULL,
    success_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at INTEGER NOT NULL,  -- 改为整数时间戳
    updated_at INTEGER NOT NULL   -- 改为整数时间戳
)
```

**batch_task_item 表**：
```sql
CREATE TABLE IF NOT EXISTS batch_task_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    magnet TEXT NOT NULL,
    resource_name TEXT,
    status TEXT DEFAULT 'pending',
    retry_count INTEGER DEFAULT 0,
    max_retry INTEGER DEFAULT 5,
    last_retry_at INTEGER,     -- 改为整数时间戳
    next_retry_at INTEGER,     -- 改为整数时间戳
    completed_at INTEGER,      -- 改为整数时间戳
    created_at INTEGER NOT NULL -- 改为整数时间戳
)
```

### 修改的文件

1. **app/core/batch_task_manager.py**
   - 修改表结构定义
   - 添加 `reset_sql` 参数
   - 所有时间操作使用 `int(time.time())`

2. **app/core/batch_task_retry.py**
   - 新增 `send_retry_summary()` 函数
   - 在重试完成后调用发送总结

3. **app/handlers/download_handler.py**
   - 简化时间读取逻辑
   - 优化错误日志文件生成
   - 添加 `generate_batch_summary_caption()` 函数

4. **app/init.py**
   - 从环境变量读取 `RESET_SQL`
   - 传递给 `initialize_batch_task_tables()`

## 注意事项

⚠️ **首次更新后**：
- 如果遇到数据库字段类型错误，需要使用 `RESET_SQL=true` 重置一次
- 重置会清空所有批量任务历史记录
- 建议在没有进行中的批量任务时进行更新

✅ **正常使用**：
- 更新后正常启动即可
- 数据库会自动创建新表（如果不存在）
- 保留所有现有的批量任务记录

## 测试状态

✅ Python 语法检查通过
✅ 数据库表结构兼容
✅ 时区显示正确
✅ 重试消息发送正常
