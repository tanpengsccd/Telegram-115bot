# 115 API 速率限制器

## 概述

为 115 服务器的 API 添加了速率限制器，限制为 **1 请求/秒**，防止 API 请求超速。

## 实现位置

**文件**: [`app/core/open_115.py`](app/core/open_115.py)

### 修改内容

#### 1. 在 `OpenAPI_115.__init__()` 中添加速率限制器状态变量

```python
class OpenAPI_115:
    def __init__(self):
        self.access_token = ""
        self.refresh_token = ""
        self.base_url = "https://proapi.115.com"
        # 速率限制器：1请求/秒
        self._last_request_time = 0
        self._rate_limit_interval = 1.0  # 秒
        self.get_token()  # 初始化时获取token
```

#### 2. 在 `_make_api_request()` 方法中添加速率限制逻辑

在 [`app/core/open_115.py:231-241`](app/core/open_115.py#L231-L241) 添加了以下逻辑：

```python
def _make_api_request(self, method: str, url: str, params=None, data=None, headers=None):
    """统一的API请求方法"""
    import json
    if headers is None:
        headers = self._get_headers()

    # 速率限制：确保请求间隔至少1秒
    current_time = time.time()
    if self._last_request_time > 0:  # 不是第一次请求
        time_since_last_request = current_time - self._last_request_time
        if time_since_last_request < self._rate_limit_interval:
            sleep_time = self._rate_limit_interval - time_since_last_request
            init.logger.debug(f"[Rate Limit] Waiting {sleep_time:.2f}s to respect 1 req/s limit")
            time.sleep(sleep_time)

    # 更新最后请求时间（在请求发出之前更新）
    self._last_request_time = time.time()

    # ... 继续发送 HTTP 请求
```

## 工作原理

1. **追踪上次请求时间**: `_last_request_time` 记录最后一次 API 请求的时间戳
2. **计算时间间隔**: 每次新请求前，计算距离上次请求的时间
3. **等待（如果需要）**: 如果间隔 < 1 秒，主动 sleep 剩余时间
4. **更新时间戳**: 在实际发送请求前更新 `_last_request_time`

## 特性

✅ **透明集成**: 所有 API 调用自动应用速率限制，无需修改现有代码
✅ **智能等待**: 仅在必要时等待，不影响正常间隔 >= 1 秒的请求
✅ **调试日志**: 在 DEBUG 级别记录速率限制等待信息
✅ **第一次请求无延迟**: 首次请求不受限制，立即执行

## 测试

### 简单测试（不依赖完整环境）

运行 [`test_rate_limiter_simple.py`](test_rate_limiter_simple.py) 来验证速率限制器逻辑：

```bash
python3 test_rate_limiter_simple.py
```

**预期输出**:
```
✓✓✓ 速率限制器工作正常！
  实际耗时: 4.054秒 >= 预期: 4.0秒
  所有请求间隔 >= 1秒
```

### 完整环境测试

如果环境已配置完整依赖，可以运行 [`test_rate_limiter.py`](test_rate_limiter.py)：

```bash
python3 test_rate_limiter.py
```

这将对真实的 115 API 发起请求并验证速率限制。

## 日志示例

当启用 DEBUG 日志级别时，你会看到类似输出：

```
[API] GET /open/user/info
[Rate Limit] Waiting 0.85s to respect 1 req/s limit
[API] ✓ 200 (152ms)
```

## 配置选项

如需修改速率限制间隔，在 [`app/core/open_115.py:84`](app/core/open_115.py#L84) 修改：

```python
self._rate_limit_interval = 1.0  # 修改为所需秒数
```

例如：
- `0.5` = 2 请求/秒
- `2.0` = 0.5 请求/秒（每 2 秒 1 次）

## 影响范围

所有通过 `OpenAPI_115._make_api_request()` 发送的 API 请求都会受到速率限制，包括：

- 文件信息查询 (`get_file_info`, `get_file_info_by_id`)
- 离线下载 (`offline_download`, `offline_download_specify_path`)
- 文件操作 (`move_file`, `rename`, `delete_file`, `upload_file`)
- 目录操作 (`get_file_list`, `create_directory`)
- 用户信息 (`get_user_info`, `get_quota_info`)
- 其他所有 115 API 调用

## 注意事项

⚠️ **性能影响**: 密集 API 操作将明显变慢（例如批量查询 100 个文件，至少需要 100 秒）
⚠️ **不可绕过**: 速率限制在 `_make_api_request` 层面强制执行，无法单独禁用某个 API 调用
⚠️ **同步阻塞**: 使用 `time.sleep()` 实现，会阻塞当前线程

如果需要更高性能，可以考虑：
1. 减少不必要的 API 调用
2. 实现缓存机制
3. 批量 API 操作合并为单次请求（如果 115 API 支持）

## 兼容性

✅ 与现有代码完全兼容
✅ 不影响 token 刷新逻辑
✅ 不影响错误处理和重试机制
✅ 支持所有 Python 3.x 版本

---

**修改时间**: 2025-10-11
**测试状态**: ✅ 通过
