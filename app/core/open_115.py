import requests
import os
import base64
import hashlib
import re
import sys
import threading
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
sys.path.append(current_dir)
import init
import qrcode
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import wraps
from urllib.parse import unquote
from app.utils.message_queue import add_task_to_queue
from app.utils.alioss import upload_file_to_oss
from telegram.helpers import escape_markdown


_MAGNET_BTIH_RE = re.compile(r'xt=urn:btih:([a-zA-Z0-9]+)', re.IGNORECASE)
_ED2K_RAW_RE = re.compile(r'^ed2k://\|file\|[^|]+\|\d+\|([a-fA-F0-9]{32})(?:\||$)', re.IGNORECASE)
_ED2K_UNQUOTED_RE = re.compile(r'^ed2k://\|file\|.+\|\d+\|([a-fA-F0-9]{32})(?:\||$)', re.IGNORECASE)


class NonRetryableAPIError(Exception):
    """无需由 handle_token_expiry 重复重试的业务错误（如已处理过的 10008 重复任务错误）。"""

    def __init__(self, message: str, code=None, response=None):
        super().__init__(message)
        self.code = code
        self.response = response


def canonical_offline_key(url: str) -> str:
    """
    计算离线下载链接的规范化匹配键：
    - magnet: 提取 BTIH（32位 Base32 自动转为 40位小写十六进制）-> 'btih:<hash>'
    - ed2k: 提取 32位 MD4 哈希 -> 'ed2k:<md4>'
    - 其他链接: unquote(url).strip().lower()
    """
    if not url or not isinstance(url, str):
        return ""
    raw = url.strip()
    if not raw:
        return ""

    btih_match = _MAGNET_BTIH_RE.search(raw)
    if not btih_match:
        btih_match = _MAGNET_BTIH_RE.search(unquote(raw).strip())
    if btih_match:
        btih = btih_match.group(1).strip()
        if len(btih) == 32 and re.fullmatch(r'[A-Za-z2-7]{32}', btih):
            try:
                btih = base64.b32decode(btih.upper()).hex()
            except Exception:
                pass
        return f"btih:{btih.lower()}"

    ed2k_match = _ED2K_RAW_RE.match(raw)
    if not ed2k_match:
        ed2k_match = _ED2K_UNQUOTED_RE.match(unquote(raw).strip())
    if ed2k_match:
        return f"ed2k:{ed2k_match.group(1).lower()}"

    return unquote(raw).strip().lower()


_canonical_offline_key = canonical_offline_key


def build_offline_task_index(tasks) -> dict:
    """
    为云端离线任务列表建立多级查找索引（优先保留靠前/最新的任务）：
    exact -> unquoted -> canonical (btih / ed2k md4) -> info_hash (btih 兜底)
    """
    exact = {}
    unquoted = {}
    canonical = {}
    by_info_hash = {}
    for task in (tasks or []):
        if not isinstance(task, dict):
            continue
        url = task.get('url') or ''
        if url:
            exact.setdefault(url, task)
            uq = unquote(url).strip().lower()
            if uq:
                unquoted.setdefault(uq, task)
            ckey = canonical_offline_key(url)
            if ckey:
                canonical.setdefault(ckey, task)
        ih = (task.get('info_hash') or '').strip().lower()
        if ih:
            by_info_hash.setdefault(ih, task)
    return {
        "exact": exact,
        "unquoted": unquoted,
        "canonical": canonical,
        "info_hash": by_info_hash,
    }


def lookup_offline_task(index: dict, url: str):
    """按 exact -> unquoted -> canonical -> info_hash 顺序查找匹配的云端离线任务。"""
    if not index or not url or not isinstance(url, str):
        return None
    task = index["exact"].get(url)
    if task is not None:
        return task
    uq = unquote(url).strip().lower()
    if uq:
        task = index["unquoted"].get(uq)
        if task is not None:
            return task
    ckey = canonical_offline_key(url)
    if ckey:
        task = index["canonical"].get(ckey)
        if task is not None:
            return task
        if ckey.startswith("btih:"):
            task = index["info_hash"].get(ckey[5:])
            if task is not None:
                return task
    return None


def handle_token_expiry(func):
    """装饰器：统一处理API调用中的token过期情况"""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        max_retries = 2  # 最大重试次数
        for attempt in range(max_retries):
            try:
                # 调用原始函数，获取HTTP响应
                response = func(self, *args, **kwargs)
                
                # 检查响应是否是字典且包含错误码
                if isinstance(response, dict) and 'code' in response:
                    if response['code'] == 40140125:
                        # token需要刷新
                        if attempt < max_retries - 1:  # 还有重试机会
                            init.logger.info("Token需要刷新，正在重试...")
                            if self.refresh_access_token():
                                continue
                            init.logger.warn("Token刷新失败，请发送 /auth 重新扫码授权！")
                            return None
                        else:
                            init.logger.warn("Token刷新后仍然失败")
                            return None
                    elif response['code'] in [40140116, 40140119]:
                        # token已过期，需要重新授权
                        init.logger.warn("Access token 已过期，请重新授权！")
                        return None
                    elif response['code'] == 40140118:
                        init.logger.warn("开发者认证已过期，请到115开放平台重新授权！")
                        return None
                    elif response['code'] == 40140110:
                        init.logger.warn("应用已过期，请到115开放平台重新授权！")
                        return None
                    elif response['code'] == 40140109:
                        init.logger.warn("应用被停用，请到115开放平台查询详细信息！")
                        return None
                    elif response['code'] == 40140108:
                        init.logger.warn("应用审核未通过，请稍后再试！")
                        return None
                
                # 成功或其他情况，直接返回
                return response

            except NonRetryableAPIError:
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    init.logger.warn(f"API调用失败，正在重试: {e}")
                    continue
                else:
                    init.logger.warn(f"API调用最终失败: {e}")
                    raise
        
        return response
    return wrapper


class OpenAPI_115:
    def __init__(self):
        self.access_token = ""
        self.refresh_token = ""
        self.base_url = "https://proapi.115.com"
        # 速率限制器：1请求/秒（加锁确保多线程安全）
        self._rate_limit_lock = threading.Lock()
        self._last_request_time = 0
        self._rate_limit_interval = 1.0  # 秒
        # 第1页离线任务短时共享缓存（避免多任务并发轮询重复拉取）
        self._offline_page1_cache_lock = threading.Lock()
        self._offline_page1_cache_ts = 0.0
        self._offline_page1_cache_data = None
        self._offline_page1_cache_ttl = 3.0  # 秒
        self.get_token()  # 初始化时获取token
        
    def get_token(self):
        if not self.refresh_token or not self.access_token:
            if not os.path.exists(init.TOKEN_FILE):
                app_id = init.bot_config.get('115_app_id')
                if app_id and str(app_id).lower() != "your_115_app_id":
                    init.logger.info("正在进入PKCE授权流程，获取refresh_token...")
                    self.auth_pkce(init.get_primary_user(), app_id)
                else:
                    _access_token = init.bot_config.get('access_token', '')
                    _refresh_token = init.bot_config.get('refresh_token', '')
                    if _access_token and _refresh_token and \
                       _access_token.lower() != "your_access_token" and \
                       _refresh_token.lower() != "your_refresh_token":
                        self.access_token = _access_token
                        self.refresh_token = _refresh_token
                        init.logger.info("使用配置文件中的access_token和refresh_token")
                        self.save_token_to_file(self.access_token, self.refresh_token, init.TOKEN_FILE)
            with open(init.TOKEN_FILE, 'r', encoding='utf-8') as f:
                tokens = json.load(f)
                # 从文件中读取access_token和refresh_token
                self.access_token = tokens.get('access_token', '')
                self.refresh_token = tokens.get('refresh_token', '')
        
        
    def auth_pkce(self, sub_user, app_id):
        header = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": init.USER_AGENT
        }
        verifier, challenge = self.get_challenge()
        data = {
            "client_id": app_id,
            "code_challenge": challenge,
            "code_challenge_method": "sha256"
        }
        response = requests.post(f"https://passportapi.115.com/open/authDeviceCode", headers=header, data=data)
        res = response.json()
        if response.status_code == 200:
            uid = res['data']['uid']
            check_time = res['data']['time']
            qr_data = res['data']['qrcode']
            sign = res['data']['sign']
        else:
            init.logger.warn(f"获取二维码失败: {response.status_code} - {response.text}")
            raise Exception(f"Error: {response.status_code} - {response.text}")
        
        # 2. 创建QRCode对象并生成图片
        qr = qrcode.QRCode(
            version=1,               # 控制大小（1~40，默认为自动）
            error_correction=qrcode.constants.ERROR_CORRECT_L,  # 容错率（L/M/Q/H）
            box_size=10,             # 每个模块的像素大小
            border=4,                # 边框宽度（模块数）
        )
        qr.add_data(qr_data)        # 添加文本数据
        qr.make(fit=True)           # 自动调整版本

        # 3. 生成图片并保存为文件
        img = qr.make_image(fill_color="black", back_color="white")
        save_path= f"{init.IMAGE_PATH}/qrcode.png"
        if os.path.exists(save_path):
            os.remove(save_path)
        img.save(save_path)      # 保存为PNG
        
        add_task_to_queue(sub_user, save_path, "请用115APP扫码授权！")
        
        time.sleep(5)
        params = {
            "uid": uid,
            "time": check_time,
            "sign": sign
        }
        while True:
            response = requests.get(f"https://qrcodeapi.115.com/get/status/", params=params)
            if response.status_code == 200:
                res = response.json()
                if res['state'] == 0:
                    init.logger.info("二维码已失效...")
                    break
                else:
                    status_data = res.get('data') if isinstance(res, dict) else None
                    if not isinstance(status_data, dict) or status_data.get('status') is None:
                        init.logger.info("等待扫码...")
                        time.sleep(2)
                        continue
                    # 1.扫码成功，等待确认
                    if status_data['status'] == 1:
                        time.sleep(1)
                        continue
                    elif status_data['status'] == 2:
                        # 2.扫码成功，获取access_token
                        init.logger.info("二维码扫码成功，正在获取access_token...")
                        time.sleep(1)
                        response = requests.post("https://passportapi.115.com/open/deviceCodeToToken", headers=header, data={
                            "uid": uid,
                            "code_verifier": verifier
                        })
                        res = response.json()
                        if response.status_code == 200 and 'data' in res:
                            self.access_token = res['data']['access_token']
                            self.refresh_token = res['data']['refresh_token']
                            self.expires_in = res['data']['expires_in']
                            init.logger.info("access_token获取成功！")
                            self.save_token_to_file(self.access_token, self.refresh_token, init.TOKEN_FILE)
                            break
              
                        
    def _load_token_from_file(self):
        if os.path.exists(init.TOKEN_FILE):
            try:
                with open(init.TOKEN_FILE, 'r', encoding='utf-8') as f:
                    tokens = json.load(f)
                    return tokens.get('access_token', ''), tokens.get('refresh_token', '')
            except Exception as e:
                init.logger.warn(f"读取Token文件失败: {e}")
        return "", ""

    def refresh_access_token(self):
        # 1. 尝试从文件加载最新Token，避免多线程/重新授权后重复刷新旧Token
        file_access_token, file_refresh_token = self._load_token_from_file()
        if file_refresh_token and file_refresh_token != self.refresh_token:
            init.logger.info("发现本地Token文件已更新，加载新Token...")
            self.access_token = file_access_token
            self.refresh_token = file_refresh_token
            return True

        if not self.refresh_token:
            if not file_refresh_token:
                init.logger.warn("请先进行授权，获取refresh_token！")
                add_task_to_queue(init.get_primary_user(), f"{init.IMAGE_PATH}/male023.png", "请先进行授权，获取refresh_token！")
                return False
            self.access_token = file_access_token
            self.refresh_token = file_refresh_token
        
        header = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": init.USER_AGENT
        }
        
        url = "https://passportapi.115.com/open/refreshToken"
        data = {
            "refresh_token": self.refresh_token
        }
        try:
            response = requests.post(url, headers=header, data=data, timeout=15)
            res = response.json()
        except Exception as e:
            init.logger.warn(f"Access token 刷新请求失败: {e}")
            return False
        token_data = res.get('data') if isinstance(res, dict) else None
        if (
            response.status_code == 200
            and isinstance(token_data, dict)
            and token_data.get('access_token')
            and token_data.get('refresh_token')
        ):
            self.access_token = token_data['access_token']
            self.refresh_token = token_data['refresh_token']
            self.save_token_to_file(self.access_token, self.refresh_token, init.TOKEN_FILE)
            init.logger.info("Access token 更新成功.")
            return True
        else:
            init.logger.warn(f"Access token 更新失败: {res}")
            return False
        

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": init.USER_AGENT
        }

    def _make_api_request(self, method: str, url: str, params=None, data=None, headers=None):
        """统一的API请求方法"""
        import json
        if headers is None:
            headers = self._get_headers()

        # 速率限制：确保请求间隔至少1秒（线程安全）
        with self._rate_limit_lock:
            current_time = time.time()
            if self._last_request_time > 0:  # 不是第一次请求
                time_since_last_request = current_time - self._last_request_time
                if time_since_last_request < self._rate_limit_interval:
                    sleep_time = self._rate_limit_interval - time_since_last_request
                    init.logger.debug(f"[Rate Limit] Waiting {sleep_time:.2f}s to respect 1 req/s limit")
                    time.sleep(sleep_time)

            # 更新最后请求时间（在请求发出之前更新）
            self._last_request_time = time.time()

        # 提取 URL 路径（用于简洁日志）
        url_path = url.replace(self.base_url, '')

        # INFO 级别：输出简洁的请求信息
        if params:
            param_str = '&'.join([f"{k}={v}" for k, v in params.items()])
            init.logger.info(f"[API] {method} {url_path}?{param_str}")
        else:
            init.logger.info(f"[API] {method} {url_path}")

        # DEBUG 级别：输出详细的请求信息
        if init.logger.logger.level <= 10:  # DEBUG = 10
            init.logger.debug("═" * 50)
            init.logger.debug(f"➤ {method.upper()} {url}")
            if params:
                param_lines = [f"{k}={v}" for k, v in params.items()]
                init.logger.debug(f"  Query: {', '.join(param_lines)}")
            if data:
                init.logger.debug(f"  Data: {data}")

        if method.upper() == 'GET':
            response = requests.get(url, headers=headers, params=params)
        elif method.upper() == 'POST':
            response = requests.post(url, headers=headers, data=data)
        else:
            raise ValueError(f"不支持的HTTP方法: {method}")

        # 计算耗时
        elapsed_ms = int(response.elapsed.total_seconds() * 1000)

        # INFO 级别：输出简洁的响应信息
        status_icon = "✓" if response.status_code == 200 else "✗"
        init.logger.info(f"[API] {status_icon} {response.status_code} ({elapsed_ms}ms)")

        # DEBUG 级别：输出详细的响应信息
        if init.logger.logger.level <= 10:  # DEBUG = 10
            init.logger.debug(f"✓ Status: {response.status_code} {response.reason} ({elapsed_ms}ms)")

        if response.status_code == 200:
            result = response.json()

            # DEBUG 级别：美化输出响应内容
            if init.logger.logger.level <= 10:  # DEBUG = 10
                try:
                    # 尝试美化 JSON 输出
                    formatted_json = json.dumps(result, indent=2, ensure_ascii=False)
                    # 限制输出长度，避免日志过大
                    if len(formatted_json) > 2000:
                        formatted_json = formatted_json[:2000] + "\n  ... (truncated)"
                    init.logger.debug(f"  Response:\n{formatted_json}")
                except:
                    init.logger.debug(f"  Response: {result}")
                init.logger.debug("═" * 50)

            return result
        else:
            init.logger.warn(f"API请求失败: {response.status_code} - {response.text}")
            if init.logger.logger.level <= 10:  # DEBUG = 10
                init.logger.debug("═" * 50)
            return {"code": response.status_code, "message": response.text}
    
    @handle_token_expiry
    def get_file_info(self, path: str):
        url = f"{self.base_url}/open/folder/get_info"
        params = {"path": path}
        response = self._make_api_request('GET', url, params=params)
        
        # 如果成功获取文件信息，记录日志
        if isinstance(response, dict) and response.get('code') == 0:
            init.logger.debug(f"获取文件信息成功: {response}")
            return response['data']
        else:
            init.logger.warn(f"获取文件信息失败: {response}")
            if response['code'] == 40140125:
                return response
            return None
        
    @handle_token_expiry
    def get_file_info_by_id(self, file_id: str):
        url = f"{self.base_url}/open/folder/get_info"
        params = {"file_id": file_id}
        response = self._make_api_request('GET', url, params=params)
        
        # 如果成功获取文件信息，记录日志
        if isinstance(response, dict) and response.get('code') == 0:
            init.logger.debug(f"获取文件信息成功: {response}")
            return response['data']
        else:
            init.logger.warn(f"获取文件信息失败: {response}")
            if response['code'] == 40140125:
                return response
            return None
    
    @handle_token_expiry
    def offline_download(self, download_url):
        url = f"{self.base_url}/open/offline/add_task_urls"
        file_info = self.get_file_info(init.bot_config['offline_path'])
        if not file_info:
            init.logger.warn(f"获取离线下载目录信息失败: {file_info}")
            return False
        
        data = {
            "urls": download_url,
            "wp_path_id": file_info['file_id']
        }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        if response['state'] == True:
            self.invalidate_offline_page1_cache()
            init.logger.info(f"离线下载任务添加成功: {response['message']}")
            return True
        else:
            init.logger.warn(f"离线下载任务添加失败: {response['message']}")
            if response['code'] == 40140125:
                return response
            return None
    
    @staticmethod
    def _dedupe_offline_urls(urls) -> list:
        """按规范化键去重离线链接（保序），避免单次批量请求内自身重复触发 10008。"""
        seen = set()
        result = []
        for raw in (urls or []):
            u = str(raw or "").strip()
            if not u:
                continue
            key = canonical_offline_key(u) or u
            if key in seen:
                continue
            seen.add(key)
            result.append(u)
        return result

    @staticmethod
    def _is_offline_task_completed(task) -> bool:
        if not isinstance(task, dict):
            return False
        status = task.get('status')
        percent = task.get('percentDone') or 0
        return status == 2 or percent >= 100

    @staticmethod
    def _is_offline_task_active(task) -> bool:
        if not isinstance(task, dict):
            return False
        if OpenAPI_115._is_offline_task_completed(task):
            return False
        return task.get('status') in (0, 1)

    @staticmethod
    def _extract_duplicate_info_hashes(response) -> list:
        """从 10008 响应体中提取直接返回的 (info_hash, url) 列表（如有）。"""
        entries = []
        if not isinstance(response, dict):
            return entries
        top_ih = (response.get('info_hash') or '').strip()
        if top_ih:
            entries.append((top_ih, response.get('url') or ''))
        data = response.get('data')
        if isinstance(data, dict):
            ih = (data.get('info_hash') or '').strip()
            if ih:
                entries.append((ih, data.get('url') or ''))
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                ih = (item.get('info_hash') or '').strip()
                if ih and (item.get('code') == 10008 or item.get('state') is False or not item.get('state')):
                    entries.append((ih, item.get('url') or ''))
        # 按 info_hash 去重保序
        deduped = []
        seen_ih = set()
        for ih, u in entries:
            if ih not in seen_ih:
                seen_ih.add(ih)
                deduped.append((ih, u))
        return deduped

    def find_offline_tasks_by_urls(self, target_urls) -> dict:
        """
        深度分页查找指定链接在 115 云端离线列表中的对应任务（支持规范化 URL / BTIH / ed2k MD4 匹配）。
        全部目标命中后立即停止翻页。

        Returns:
            dict[str, dict]: {原始目标URL: 匹配到的云端任务字典}
        """
        urls = [str(u).strip() for u in (target_urls or []) if str(u or '').strip()]
        if not urls:
            return {}
        cloud_tasks = self.get_offline_tasks_for_links(urls, max_pages=999, use_cache=False) or []
        index = build_offline_task_index(cloud_tasks)
        matched = {}
        for u in urls:
            task = lookup_offline_task(index, u)
            if task is not None:
                matched[u] = task
        return matched

    def _handle_duplicate_offline_submit(self, submitted_urls, api_url, wp_path_id, response):
        """
        统一处理 115 添加离线任务返回的 code == 10008（任务已存在）错误：
        1. 若响应携带 info_hash，仅清理任务记录（保留源文件）后重提一次；
        2. 否则调用 find_offline_tasks_by_urls() 深度定位重复任务：
           - 单条链接（或整批全部链接）已完成/下载中：保留源文件与记录，直接返回 True；
           - 存在失败/停滞任务或批量中仍有未提交的新链接：
             * 失败/停滞任务 delete_source_file=True；
             * 已完成/下载中任务仅删任务记录 delete_source_file=False；
             等待 1 秒后重新提交一次；
        3. 若仍失败，抛出 NonRetryableAPIError，防止 @handle_token_expiry 重复空转。
        """
        urls = [u for u in (submitted_urls or []) if u]
        data = {
            "urls": "\n".join(urls),
            "wp_path_id": wp_path_id,
        }

        direct_entries = self._extract_duplicate_info_hashes(response)
        if direct_entries:
            init.logger.warn(
                f"检测到重复任务（code: 10008），响应包含 {len(direct_entries)} 个 info_hash，清理记录后重新提交"
            )
            deleted_any = False
            for ih, dup_url in direct_entries:
                init.logger.info(f"删除重复任务记录（保留源文件）info_hash={ih}, url={dup_url[:80]}")
                if self.del_faild_offline_task(ih, delete_source_file=False):
                    deleted_any = True
            if not deleted_any:
                raise NonRetryableAPIError(
                    f"任务已存在且删除失败: {response.get('message', '任务已存在')}",
                    code=10008,
                    response=response,
                )
            time.sleep(1)
            retry_response = self._make_api_request('POST', api_url, data=data, headers=self._get_headers())
            if isinstance(retry_response, dict) and retry_response.get('state') == True:
                self.invalidate_offline_page1_cache()
                init.logger.info(f"清理重复任务后重新提交成功（共 {len(urls)} 个）")
                return True
            if isinstance(retry_response, dict) and retry_response.get('code') == 40140125:
                return retry_response
            err_msg = retry_response.get('message', str(retry_response)) if isinstance(retry_response, dict) else str(retry_response)
            err_code = retry_response.get('code') if isinstance(retry_response, dict) else None
            init.logger.error(f"重新提交失败: {err_msg}")
            raise NonRetryableAPIError(f"重新提交失败: {err_msg}", code=err_code, response=retry_response)

        init.logger.warn(
            f"检测到重复任务（code: 10008）但响应无 info_hash，深度查找 {len(urls)} 个链接对应的云端任务..."
        )
        matched_map = self.find_offline_tasks_by_urls(urls)
        if not matched_map:
            init.logger.warn(f"code 10008 但未在云端离线列表中找到匹配任务: {response}")
            raise NonRetryableAPIError(
                response.get('message', '任务已存在'),
                code=10008,
                response=response,
            )

        completed_or_active = []
        failed_or_stalled = []
        unmatched = []
        for u in urls:
            task = matched_map.get(u)
            if task is None:
                unmatched.append(u)
            elif self._is_offline_task_completed(task) or self._is_offline_task_active(task):
                completed_or_active.append((u, task))
            else:
                failed_or_stalled.append((u, task))

        # 单链接或整批所有链接在云端均已完成/下载中：直接保留源文件并返回 True，让上层状态检查接管
        if not failed_or_stalled and not unmatched:
            init.logger.info(
                f"重复任务（共 {len(completed_or_active)} 个）在云端均已完成或正在下载，保留源文件并直接返回成功"
            )
            return True

        # 需要重新提交（失败/停滞任务，或批量中还有未提交的新链接）
        delete_plan = {}  # {info_hash: delete_source_file}
        for _, task in completed_or_active:
            ih = (task.get('info_hash') or '').strip()
            if ih:
                delete_plan[ih] = False  # 已完成/下载中：仅删任务记录，严禁删源文件
        for _, task in failed_or_stalled:
            ih = (task.get('info_hash') or '').strip()
            if ih and ih not in delete_plan:
                delete_plan[ih] = True   # 失败/停滞：清理残留任务与临时文件

        if not delete_plan:
            init.logger.warn(f"匹配到重复任务但均缺少 info_hash，无法清理重提: {response}")
            raise NonRetryableAPIError(
                response.get('message', '任务已存在'),
                code=10008,
                response=response,
            )

        deleted_any = False
        for ih, del_source in delete_plan.items():
            init.logger.info(
                f"清理云端重复离线任务 info_hash={ih}, delete_source_file={del_source}"
            )
            if self.del_faild_offline_task(ih, delete_source_file=del_source):
                deleted_any = True

        if not deleted_any:
            init.logger.error("清理重复离线任务失败，放弃重新提交")
            raise NonRetryableAPIError(
                f"任务已存在且删除失败: {response.get('message', '任务已存在')}",
                code=10008,
                response=response,
            )

        init.logger.info("已清理重复离线任务，等待 1 秒后重新提交...")
        time.sleep(1)
        retry_response = self._make_api_request('POST', api_url, data=data, headers=self._get_headers())
        if isinstance(retry_response, dict) and retry_response.get('state') == True:
            self.invalidate_offline_page1_cache()
            init.logger.info(f"清理重复任务后重新提交成功（共 {len(urls)} 个）")
            return True
        if isinstance(retry_response, dict) and retry_response.get('code') == 40140125:
            return retry_response

        err_msg = retry_response.get('message', str(retry_response)) if isinstance(retry_response, dict) else str(retry_response)
        err_code = retry_response.get('code') if isinstance(retry_response, dict) else None
        init.logger.error(f"清理重复任务后重新提交失败: {err_msg}")
        raise NonRetryableAPIError(f"重新提交失败: {err_msg}", code=err_code, response=retry_response)

    @handle_token_expiry
    def offline_download_specify_path(self, download_url, save_path):
        url = f"{self.base_url}/open/offline/add_task_urls"
        file_info = self.get_file_info(save_path)
        if not file_info:
            self.create_dir_recursive(save_path)
            file_info = self.get_file_info(save_path)
        if not file_info:
            raise Exception(f"获取保存目录信息失败: {save_path}")

        raw_urls = [u.strip() for u in str(download_url or "").splitlines() if u.strip()]
        submitted_urls = self._dedupe_offline_urls(raw_urls)
        urls_string = "\n".join(submitted_urls) if submitted_urls else str(download_url or "")

        data = {
            "urls": urls_string,
            "wp_path_id": file_info['file_id']
        }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        if isinstance(response, dict) and response.get('state') == True:
            self.invalidate_offline_page1_cache()
            init.logger.info(f"离线下载任务添加成功: {response}")
            return True
        if isinstance(response, dict) and response.get('code') == 40140125:
            return response
        if isinstance(response, dict) and response.get('code') == 10008:
            return self._handle_duplicate_offline_submit(
                submitted_urls or [str(download_url or "")],
                url,
                file_info['file_id'],
                response,
            )
        err_msg = response.get('message', str(response)) if isinstance(response, dict) else str(response)
        init.logger.warn(f"离线下载任务添加失败: {err_msg}")
        raise Exception(err_msg)

    @handle_token_expiry
    def offline_download_specify_path_batch(self, download_urls, save_path):
        """
        批量添加离线下载任务（使用换行符分割多个URL）

        Args:
            download_urls: 下载链接列表
            save_path: 保存路径

        Returns:
            成功返回 True，失败抛出异常
        """
        url = f"{self.base_url}/open/offline/add_task_urls"
        file_info = self.get_file_info(save_path)
        if not file_info:
            self.create_dir_recursive(save_path)
            file_info = self.get_file_info(save_path)
        if not file_info:
            raise Exception(f"获取保存目录信息失败: {save_path}")

        # 按规范化键去重并拼接成一个字符串，避免批内自重复触发 10008
        submitted_urls = self._dedupe_offline_urls(download_urls)
        urls_string = "\n".join(submitted_urls)

        data = {
            "urls": urls_string,
            "wp_path_id": file_info['file_id']
        }

        init.logger.info(f"批量提交 {len(submitted_urls)} 个离线下载任务")
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())

        if isinstance(response, dict) and response.get('state') == True:
            self.invalidate_offline_page1_cache()
            init.logger.info(f"批量离线下载任务添加成功: {response}")
            return True
        if isinstance(response, dict) and response.get('code') == 40140125:
            return response
        if isinstance(response, dict) and response.get('code') == 10008:
            return self._handle_duplicate_offline_submit(
                submitted_urls,
                url,
                file_info['file_id'],
                response,
            )
        err_msg = response.get('message', str(response)) if isinstance(response, dict) else str(response)
        init.logger.warn(f"批量离线下载任务添加失败: {err_msg}")
        raise Exception(err_msg)

    def invalidate_offline_page1_cache(self):
        """主动失效第1页离线任务缓存（提交/删除/清理后调用）"""
        lock = getattr(self, "_offline_page1_cache_lock", None)
        if lock is not None:
            with lock:
                self._offline_page1_cache_ts = 0.0
                self._offline_page1_cache_data = None
        else:
            self._offline_page1_cache_ts = 0.0
            self._offline_page1_cache_data = None

    @staticmethod
    def _normalize_offline_task(task: dict) -> dict:
        return {
            'name': task.get('name', ''),
            'url': task.get('url', ''),
            'status': task.get('status'),
            'percentDone': task.get('percentDone', 0),
            'info_hash': task.get('info_hash', ''),
            'file_id': task.get('file_id', ''),               # 最终目录id
            'wp_path_id': task.get('wp_path_id', ''),         # 下载目录id
            'delete_file_id': task.get('delete_file_id', ''), # 同file_id
        }

    def get_offline_tasks_for_links(self, target_urls, max_pages=None, use_cache=True):
        """
        按需分页获取离线任务状态：从第1页开始拉取，一旦 target_urls 全部命中（支持规范化 URL/BTIH/ed2k MD4 匹配）
        或达到合理页数上限立即停止，避免全量翻遍几十页历史记录，同时通过3秒第1页共享缓存避免并发任务重复调用API。
        """
        remaining = {canonical_offline_key(u) for u in (target_urls or []) if u}
        remaining.discard("")
        page1_data = None

        lock = getattr(self, "_offline_page1_cache_lock", None)
        ttl = getattr(self, "_offline_page1_cache_ttl", 3.0)
        if use_cache and lock is not None:
            with lock:
                if (
                    self._offline_page1_cache_data is not None
                    and (time.monotonic() - self._offline_page1_cache_ts) < ttl
                ):
                    page1_data = self._offline_page1_cache_data

        if page1_data is None:
            page1_data = self.get_offline_tasks_by_page(page=1)
            if isinstance(page1_data, dict) and lock is not None:
                with lock:
                    self._offline_page1_cache_data = page1_data
                    self._offline_page1_cache_ts = time.monotonic()

        if not isinstance(page1_data, dict) or 'tasks' not in page1_data:
            return None

        def _discard_matched(norm_task: dict):
            task_key = canonical_offline_key(norm_task.get('url', ''))
            if task_key:
                remaining.discard(task_key)
            task_ih = (norm_task.get('info_hash') or '').strip().lower()
            if task_ih:
                remaining.discard(f"btih:{task_ih}")

        task_list = []
        raw_page1_tasks = page1_data.get('tasks') or []
        for task in raw_page1_tasks:
            if isinstance(task, dict):
                norm = self._normalize_offline_task(task)
                task_list.append(norm)
                _discard_matched(norm)

        page_count = int(page1_data.get('page_count') or 1)
        if not remaining or page_count <= 1:
            return task_list

        # 计算合理翻页上限（115每页约30条）：避免因某条已被清理而把全部历史页翻完
        if max_pages is None:
            per_page = max(len(raw_page1_tasks), 30)
            max_pages = min(page_count, max(3, (len(target_urls or []) + per_page - 1) // per_page + 2))
        else:
            max_pages = min(page_count, max(1, int(max_pages)))

        for page in range(2, max_pages + 1):
            if not remaining:
                break
            page_data = self.get_offline_tasks_by_page(page=page)
            if not isinstance(page_data, dict) or 'tasks' not in page_data:
                break
            for task in (page_data.get('tasks') or []):
                if isinstance(task, dict):
                    norm = self._normalize_offline_task(task)
                    task_list.append(norm)
                    _discard_matched(norm)

        return task_list

    @handle_token_expiry
    def get_offline_tasks_by_page(self, page=1):
        url = f"{self.base_url}/open/offline/get_task_list"
        params = {"page": page}
        response = self._make_api_request('GET', url, params=params)
        if isinstance(response, dict) and response.get('code') == 0 and 'data' in response:
            return response['data']
        else:
            init.logger.warn(f"获取离线下载任务列表失败: {response}")
            if isinstance(response, dict) and response.get('code') == 40140125:
                return response
            return None
    
    @handle_token_expiry
    def get_offline_tasks(self):
        url = f"{self.base_url}/open/offline/get_task_list"
        response = self._make_api_request('GET', url)
        task_list = []
        if isinstance(response, dict) and response.get('code') == 0 and 'data' in response:
            page_count = response['data'].get('page_count', 1)
            for i in range(1, page_count + 1):
                tasks = self.get_offline_tasks_by_page(i)
                if tasks and 'tasks' in tasks:
                    for task in tasks['tasks']:
                        task_list.append({
                            'name': task['name'],
                            'url': task['url'],
                            'status': task['status'],
                            'percentDone': task['percentDone'],
                            'info_hash': task['info_hash'],
                            'file_id': task['file_id'],               # 最终目录id
                            'wp_path_id': task['wp_path_id'],         # 下载目录id
                            'delete_file_id': task['delete_file_id']  # 同file_id
                        })
                time.sleep(1)  # 避免请求过快
            return task_list  
        else:
            init.logger.warn(f"获取离线下载任务列表失败: {response}")
            if isinstance(response, dict) and response.get('code') == 40140125:
                if response['code'] == 40140125:
                    return response
            return None
    
    @handle_token_expiry
    def del_faild_offline_task(self, info_hash, delete_source_file=True):
        """
        删除离线任务

        Args:
            info_hash (str): 离线任务的 info_hash
            delete_source_file (bool): 是否同时删除源文件，默认删除（匹配旧行为）
        """
        url = f"{self.base_url}/open/offline/del_task"
        data = {
            "info_hash": info_hash,
            "del_source_file": 1 if delete_source_file else 0
        }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        if response['state'] == True:
            self.invalidate_offline_page1_cache()
            init.logger.info(f"清理失败的离线下载任务成功!")
            return True
        else:
            init.logger.warn(f"清理失败的离线下载任务失败: {response['message']}")
            if response['code'] == 40140125:
                return response
            return None

    @handle_token_expiry
    def clear_cloud_task(self, flag=1):
        url = f"{self.base_url}/open/offline/clear_task"
        # 1清空全部、2清空失败、3清空进行中、4清空已完成、5清空全部任务并清空对应源文件
        data = {
            "flag": flag 
        }
        response = self._make_api_request('POST', url, data=data)
        if response['state'] == True:
            self.invalidate_offline_page1_cache()
            init.logger.info(f"清理云端任务成功！")
            return True
        else:
            init.logger.warn(f"清理云端任务失败: {response['message']}")
            if response['code'] == 40140125:
                return response
            return None

    @handle_token_expiry
    def move_file(self, source_path, target_path):
        """移动文件或目录"""
        src_file_info = self.get_file_info(source_path)
        if not src_file_info:
            init.logger.warn(f"获取源文件信息失败: {src_file_info}")
            return False
        
        dst_file_info = self.get_file_info(target_path)
        if not dst_file_info:
            init.logger.warn(f"获取目标文件信息失败: {dst_file_info}")
            return False
        
        file_id = src_file_info['file_id']
        to_cid = dst_file_info['file_id']
        url = f"{self.base_url}/open/ufile/move"
        data = {
            "file_ids": file_id,
            "to_cid": to_cid
        }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        if response['state'] == True:
            init.logger.info(f"文件移动成功: [{source_path}] -> [{target_path}]")
            return True
        else:
            init.logger.warn(f"文件移动失败: {response['message']}")
            if response['code'] == 40140125:
                return response
            return None
    
    @handle_token_expiry      
    def rename(self, old_name, new_name):
        """重命名文件或目录"""
        file_info = self.get_file_info(old_name)
        if not file_info:
            init.logger.warn(f"获取文件信息失败: {file_info}")
            return False
        
        file_id = file_info['file_id']
        url = f"{self.base_url}/open/ufile/update"
        data = {
            "file_id": file_id,
            "file_name": new_name
        }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        if response['state'] == True:
            init.logger.info(f"文件重命名成功: [{old_name}] -> [{new_name}]")
            return True
        else:
            init.logger.warn(f"文件重命名失败: {response['message']}")
            if response['code'] == 40140125:
                return response
            return None
        
    @handle_token_expiry
    def rename_by_id(self, file_id, old_name, new_name):
        """重命名文件或目录"""
        url = f"{self.base_url}/open/ufile/update"
        data = {
            "file_id": file_id,
            "file_name": new_name
        }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        if response['state'] == True:
            init.logger.info(f"文件重命名成功: [{old_name}] -> [{new_name}]")
            return True
        else:
            init.logger.warn(f"文件重命名失败: {response['message']}")
            if response['code'] == 40140125:
                return response
            return None
            
    @handle_token_expiry
    def get_file_list(self, params):
        """获取指定目录下的所有文件"""
        url = f"{self.base_url}/open/ufile/files"
        response = self._make_api_request('GET', url, params=params, headers=self._get_headers())
        
        if isinstance(response, dict) and response.get('code') == 0:
            init.logger.debug(f"获取文件列表成功: {response}")
            return response['data']
        else:
            init.logger.warn(f"获取文件列表失败: {response}")
            if response['code'] == 40140125:
                return response
            return None
        
    @handle_token_expiry
    def create_directory(self, pid, file_name):
        """创建目录"""
        url = f"{self.base_url}/open/folder/add"
        data = {
            "pid": pid,
            "file_name": file_name,
        }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        
        if isinstance(response, dict) and response.get('code') == 0:
            init.logger.info(f"目录创建成功: {file_name}")
            return True
        elif response.get('code') == 20004:
            init.logger.info(f"目录已存在: {file_name}")
            return True
        else:
            init.logger.warn(f"目录创建失败: {response}")
            if response['code'] == 40140125:
                return response
            return None
        
    @handle_token_expiry
    def delet_file(self, file_ids):
        """删除文件或目录"""
        url = f"{self.base_url}/open/ufile/delete"
        data = {
            "file_ids": file_ids
        }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        if isinstance(response, dict) and response.get('state') == True:
            init.logger.info(f"文件或目录删除成功: {file_ids}")
            return True
        else:
            init.logger.warn(f"文件或目录删除失败: {response}")
            if isinstance(response, dict) and response.get('code') == 40140125:
                return response
            return False

    def _batch_delete_files(self, fid_list, batch_size=100):
        """分批删除文件，避免单次请求过长，支持失败重试"""
        if not fid_list:
            return

        # 去重并保持顺序
        fid_list = list(dict.fromkeys(str(fid) for fid in fid_list if fid))
        total_files = len(fid_list)
        init.logger.info(f"准备分批删除 {total_files} 个文件，每批 {batch_size} 个")

        for i in range(0, total_files, batch_size):
            batch = fid_list[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total_files + batch_size - 1) // batch_size

            init.logger.info(f"正在执行第 {batch_num}/{total_batches} 批删除操作，共 {len(batch)} 个文件")
            file_ids = ",".join(batch)

            max_retries = 5
            for attempt in range(max_retries):
                result = self.delet_file(file_ids)
                if result is True:
                    init.logger.info(f"第 {batch_num} 批删除成功")
                    break
                if attempt < max_retries - 1:
                    sleep_time = (attempt + 1) * 3
                    init.logger.warn(f"第 {batch_num} 批删除失败，{sleep_time}秒后进行第 {attempt + 2} 次重试...")
                    time.sleep(sleep_time)
                else:
                    init.logger.error(f"第 {batch_num} 批删除在 {max_retries} 次尝试后最终失败: {result}")

            if i + batch_size < total_files:
                time.sleep(2)
        
    @handle_token_expiry
    def delete_single_file(self, path):
        """删除单个文件"""
        file_info = self.get_file_info(path)
        if not file_info:
            return None
        url = f"{self.base_url}/open/ufile/delete"
        data = {
            "file_ids": [file_info['file_id']]
        }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        if response['state'] == True:
            init.logger.info(f"文件(夹)删除成功: {path}")
            return True
        else:
            init.logger.warn(f"文件(夹)删除失败: {response['message']}")
            if response['code'] == 40140125:
                return response
            return None

    @handle_token_expiry
    def upload_file(self, **kwargs):
        """上传文件"""
        target = kwargs.get('target') 
        file_info = self.get_file_info(target)
        if not file_info:
            init.logger.warn(f"获取目标目录信息失败: {file_info}")
            return False, False
        target = f"U_1_{file_info['file_id']}"
        url = f"{self.base_url}/open/upload/init"
        if not kwargs.get('sign_key') and not kwargs.get('sign_val'):
            # 如果没有提供sign_key和sign_val，则直接使用文件名和大小
            data = {
                "file_name": kwargs.get('file_name', ''),
                "file_size": kwargs.get('file_size', 0),    
                "target": target,  # 0: 根目录, 1: 指定目录
                "fileid": kwargs.get('fileid', '')
            }
        else:
            # 如果提供了sign_key和sign_val，则使用它们进行二次认证
            data = {
                "file_name": kwargs.get('file_name', ''),
                "file_size": kwargs.get('file_size', 0),    
                "target": target,  # 0: 根目录, 1: 指定目录
                "fileid": kwargs.get('fileid', ''),
                "sign_key": kwargs.get('sign_key'),
                "sign_val": kwargs.get('sign_val')
            }
        response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
        if isinstance(response, dict) and response.get('code') == 0:
            init.logger.info(response['data'])
            # 需要二次认证
            if response['data'].get('sign_key') and response['data'].get('sign_check') and kwargs.get('request_times') == 1:
                sign_check = response['data']['sign_check'].split('-')
                sign_val = file_sha1_by_range(kwargs.get('file_path', ''), int(sign_check[0]), int(sign_check[1])).upper()
                return self.upload_file(
                    file_name=kwargs.get('file_name', ''),
                    file_size=kwargs.get('file_size', 0),    
                    target=kwargs.get('target'),
                    fileid=kwargs.get('fileid', ''),
                    file_path=kwargs.get('file_path', ''),  # 添加这个参数
                    sign_key=response['data']['sign_key'],
                    sign_val=sign_val,
                    request_times=2)
            if response['data'].get('status') != 2:
                # 秒传失败，需要上传到阿里服务器时
                callback_params = response['data'].get('callback', {})
                if callback_params:
                    # 获取上传token
                    token_info = self.get_upload_token()
                    if not token_info:
                        init.logger.warn("获取上传token失败")
                        return False, False
                    # 准备上传参数
                    access_key_id = token_info['AccessKeyId']
                    access_key_secret = token_info['AccessKeySecret']
                    security_token = token_info['SecurityToken']
                    endpoint = token_info['endpoint']
                    bucket = response['data']['bucket']
                    object_key = response['data']['object']
                    pick_code = response['data']['pick_code']
                    region = 'cn-shenzhen'
                    callback_body_str = callback_params.get('callback', '{}')
                    callback_vars_str = callback_params.get('callback_var', '{}')

                    # 构造回调参数（callback）：指定回调地址和回调请求体，使用 Base64 编码
                    callback=base64.b64encode(callback_body_str.encode()).decode()
                    # 构造自定义变量（callback-var），使用 Base64 编码
                    callback_var=base64.b64encode(callback_vars_str.encode()).decode()
                    
                    # 上传文件到阿里云OSS
                    try:
                        init.logger.info(f"开始上传文件: {kwargs.get('file_name', '')}")
                        upload_result = upload_file_to_oss(
                            access_key_id=access_key_id,
                            access_key_secret=access_key_secret,
                            security_token=security_token,
                            endpoint=endpoint,
                            bucket=bucket,
                            file_path=kwargs.get('file_path', ''),
                            key=object_key,
                            region=region,
                            callback=callback,
                            callback_var=callback_var
                        )
                        
                        if upload_result:
                            init.logger.info(f"[{kwargs.get('file_name', '')}]上传成功！")
                            return True, False
                        else:
                            init.logger.warn(f"[{kwargs.get('file_name', '')}]上传失败!")
                            return False, False
                    except Exception as e:
                        init.logger.warn(f"上传文件到OSS时出错: {e}")
                        return False, False
            else:
                init.logger.info(f"[{kwargs.get('file_name', '')}]秒传成功！")
                return True, True
        else:
            if isinstance(response, dict) and response.get('code') == 40140125:
                return response
            err_msg = response.get('message', str(response)) if isinstance(response, dict) else str(response)
            init.logger.warn(f"文件上传初始化失败: {err_msg}")
            return False, False
    
    
    @handle_token_expiry
    def get_upload_token(self):
        """获取上传文件的token"""
        url = f"{self.base_url}/open/upload/get_token"
        response = self._make_api_request('GET', url)
        
        if isinstance(response, dict) and response.get('code') == 0:
            init.logger.info(f"获取上传token成功: {response}")
            return response['data']
        else:
            init.logger.warn(f"获取上传token失败: {response}")
            if response['code'] == 40140125:
                    return response
        return None
    
        
    @handle_token_expiry
    def get_user_info(self):
        """获取用户信息"""
        url = f"{self.base_url}/open/user/info"
        response = self._make_api_request('GET', url)
        
        if isinstance(response, dict) and response.get('code') == 0:
            init.logger.info(f"获取用户信息成功: {response}")
            return response['data']
        else:
            init.logger.warn(f"获取用户信息失败: {response}")
            if isinstance(response, dict) and response.get('code') == 40140125:
                return response
            return None
        
    @handle_token_expiry
    def get_quota_info(self):
        """获取配额信息"""
        url = f"{self.base_url}/open/offline/get_quota_info"
        response = self._make_api_request('GET', url)
        
        if isinstance(response, dict) and response.get('code') == 0:
            init.logger.info(f"获取配额信息成功: {response}")
            return response['data']
        else:
            init.logger.warn(f"获取配额信息失败: {response}")
            if isinstance(response, dict) and response.get('code') == 40140125:
                return response
            return None
        
    @handle_token_expiry
    def get_file_play_url(self, file_path):
        file_info = self.get_file_info(file_path)
        if not file_info:
            return None
        params = {
            "cid": file_info['file_id'],
            "type": 4,
            "limit": 1000
        }
        file_list = self.get_file_list(params)
        if not file_list:
            return None
        video_name = file_list[0]['fn']
        video_info = self.get_file_info(f"{file_path}/{video_name}")
        pick_code = video_info.get('pick_code', '')
        url = f"{self.base_url}/open/video/play"
        params = {
            "pick_code": pick_code
        }
        response = self._make_api_request('GET', url, params=params)
        if isinstance(response, dict) and response.get('code') == 0:
            init.logger.info(f"获取视频播放链接成功: {response}")
            return response['data']['video_url'][0]['url']
        else:
            init.logger.warn(f"获取视频播放链接失败: {response}")
            if response['code'] == 40140125:
                return response
        return None
    
    @handle_token_expiry
    def get_file_download_url(self, file_path):
        """获取文件下载链接"""
        file_info = self.get_file_info(file_path)
        file_id = file_info['file_id']
        videos = self.get_file_list({
            "cid": file_id,
            "type": 4,
            "limit": 1,
            "asc": 0,
            "o": "file_size",
            "custom_order": 1
        })
        url = f"{self.base_url}/open/ufile/downurl"
        download_urls = []
        for i in range(len(videos)):
            data = {  
                "pick_code": videos[0]['pc']
            }
            response = self._make_api_request('POST', url, data=data, headers=self._get_headers())
            if response['state'] == True:
                init.logger.info(f"获取文件下载链接成功: {response}")
                download_urls.append(response['data'][videos[i]['fid']]['url']['url'])
                time.sleep(3)  # 避免请求过快
            else:
                init.logger.warn(f"获取文件下载链接失败: {response}")
                if response['code'] == 40140125:
                    return response
        return download_urls
        
        

    def welcome_message(self):
        """欢迎消息"""
        user_info = self.get_user_info()
        if not isinstance(user_info, dict) or 'rt_space_info' not in user_info:
            return "", "", "", ""
        quota_info = self.get_quota_info()
        user_name = user_info.get('user_name', '')
        rt_space = user_info.get('rt_space_info', {})
        total_space = rt_space.get('all_total', {}).get('size_format', '未知')
        used_space = rt_space.get('all_use', {}).get('size_format', '未知')
        remaining_space = rt_space.get('all_remain', {}).get('size_format', '未知')
        vip_info = user_info.get('vip_info', {})
        expire_date = datetime.fromtimestamp(vip_info.get('expire', 0), tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        line1 = escape_markdown(f"👋 [{user_name}]您好， 欢迎使用Telegram-115Bot！", version=2)
        line2 = escape_markdown(f"会员等级：{vip_info.get('level_name', '')}；到期时间：{expire_date}", version=2)
        line3 = escape_markdown(f"总空间：{total_space} 已用：{used_space} 剩余：{remaining_space}", version=2)
        if isinstance(quota_info, dict) and 'used' in quota_info and 'count' in quota_info:
            quota_text = f"{quota_info['used']}/{quota_info['count']}"
        else:
            quota_text = "未知"
        line4 = escape_markdown(f"离线配额：{quota_text}", version=2)
        return line1, line2, line3, line4


    def check_offline_download_success(self, url, offline_timeout=300):
        time_out = 0
        task_name = ""
        while time_out < offline_timeout:
            tasks = self.get_offline_tasks()
            if not tasks:
                return False, ""

            task = lookup_offline_task(build_offline_task_index(tasks), url)
            if task is not None:
                task_name = task.get('name', '')
                if self._is_offline_task_completed(task):
                    init.logger.info(f"[{task_name}]离线下载任务成功！")
                    return True, task_name
                time.sleep(10)
                time_out += 10
            else:
                time.sleep(10)
                time_out += 10
        init.logger.warn(f"[{task_name}]离线下载超时!")
        return False, task_name

    def check_offline_download_success_no_waite(self, url):
        task_name = ""
        tasks = self.get_offline_tasks()
        if not tasks:
            return False, ""

        task = lookup_offline_task(build_offline_task_index(tasks), url)
        if task is not None:
            task_name = task.get('name', '')
            if self._is_offline_task_completed(task):
                init.logger.info(f"[{task_name}]离线下载任务成功！")
                return True, task_name
            init.logger.warn(f"[{task_name}]离线下载任务未完成!")
            return False, task_name

        init.logger.warn(f"未找到匹配的离线下载任务: {url}")
        return False, task_name

    def clear_failed_task(self, url):
        tasks = self.get_offline_tasks()
        if not tasks:
            return
        task = lookup_offline_task(build_offline_task_index(tasks), url)
        if isinstance(task, dict) and task.get('info_hash'):
            self.del_faild_offline_task(task['info_hash'])

        
    def get_files_from_dir(self, path, file_type=4):
        """获取指定目录下的所有文件"""
        video_list = []
        file_info = self.get_file_info(path)
        if not file_info:
            init.logger.warn(f"获取目录信息失败: {file_info}")
            return video_list
        
        # 文件类型；1.文档；2.图片；3.音乐；4.视频；5.压缩；6.应用；7.书籍
        params = {
            "cid": file_info['file_id'],
            "type": 4,
            "limit": 1000
        }
        file_list = self.get_file_list(params)
        for file in file_list:
            video_list.append(file['fn'])
        return video_list
    
    def get_sync_dir(self, path, file_type=4):
        """获取指定目录下的所有文件"""
        video_list = []
        file_info = self.get_file_info(path)
        if not file_info:
            init.logger.warn(f"获取目录信息失败: {file_info}")
            return video_list
        
        # 文件类型；1.文档；2.图片；3.音乐；4.视频；5.压缩；6.应用；7.书籍
        params = {
            "cid": file_info['file_id'],
            "type": file_type,
            "limit": 1000
        }
        file_list = self.get_file_list(params)
        if not file_list:
            init.logger.warn(f"目录 {path} 中没有找到视频文件")
            return video_list
            
        for file in file_list:
            file_info = self.get_file_info_by_id(file['pid'])
            folder_name = file_info['file_name']
            video_list.append(f"{folder_name}/{file['fn']}")

        return video_list
    
    def is_directory(self, path):
        """检查路径是否为目录"""
        file_info = self.get_file_info(path)
        if not file_info:
            init.logger.warn(f"获取文件信息失败: {file_info}")
            return False
        
        if file_info['file_category'] == '0':
            return True
        return False
    
    def create_dir_for_file(self, path, floder_name):
        file_info = self.get_file_info(path)
        if not file_info:
            init.logger.warn(f"获取目录信息失败: {file_info}")
            return False
        
        # 创建文件夹
        self.create_directory(file_info['file_id'], floder_name)
        return True
        
    
    def auto_clean(self, path):
        # 开关关闭直接返回
        if str(init.bot_config['clean_policy']['switch']).lower() == "off":
            return
        
        file_info = self.get_file_info(path)
        if not file_info:
            init.logger.warn(f"获取目录信息失败: {file_info}")
            return
        params = {
            "cid": file_info['file_id'],
            "limit": 1000,
            "show_dir": 1
        }
        file_list = self.get_file_list(params)
        
        # 换算字节大小
        byte_size = 0
        less_than = init.bot_config['clean_policy']['less_than']
        if less_than is not None:
            if str(less_than).upper().endswith("M"):
                byte_size = int(less_than[:-1]) * 1024 * 1024
            elif str(less_than).upper().endswith("K"):
                byte_size = int(less_than[:-1]) * 1024
            elif str(less_than).upper().endswith("G"):
                byte_size = int(less_than[:-1]) * 1024 * 1024 * 1024
                
        fid_list = []
        for file in file_list:
            # 删除小于指定大小的文件
            if file['fc'] == '1':
                if file['fs'] < byte_size:
                    fid_list.append(file['fid'])
                    init.logger.info(f"[{file['fn']}]已添加到清理列表")
            # 目录直接删除
            else:
                fid_list.append(file['fid'])
                init.logger.info(f"[{file['fn']}]已添加到清理列表")
        
        if fid_list:
            self._batch_delete_files(fid_list)
            
            
    def auto_clean_by_id(self, file_id):
        # 开关关闭直接返回
        if str(init.bot_config['clean_policy']['switch']).lower() == "off":
            return
        params = {
            "cid": file_id,
            "limit": 1000,
            "show_dir": 1
        }
        file_list = self.get_file_list(params)
        
        # 换算字节大小
        byte_size = 0
        less_than = init.bot_config['clean_policy']['less_than']
        if less_than is not None:
            if str(less_than).upper().endswith("M"):
                byte_size = int(less_than[:-1]) * 1024 * 1024
            elif str(less_than).upper().endswith("K"):
                byte_size = int(less_than[:-1]) * 1024
            elif str(less_than).upper().endswith("G"):
                byte_size = int(less_than[:-1]) * 1024 * 1024 * 1024
                
        fid_list = []
        for file in file_list:
            # 删除小于指定大小的文件
            if file['fc'] == '1':
                if file['fs'] < byte_size:
                    fid_list.append(file['fid'])
                    init.logger.info(f"[{file['fn']}]已添加到清理列表")
            # 目录直接删除
            else:
                fid_list.append(file['fid'])
                init.logger.info(f"[{file['fn']}]已添加到清理列表")
        
        if fid_list:
            self._batch_delete_files(fid_list)
            
    
    def auto_clean_all(self, path):
         # 开关关闭直接返回
        if str(init.bot_config['clean_policy']['switch']).lower() == "off":
            return
        
        file_info = self.get_file_info(path)
        if not file_info:
            init.logger.warn(f"获取目录信息失败: {file_info}")
            return

        # 换算字节大小
        byte_size = 0
        less_than = init.bot_config['clean_policy']['less_than']
        if less_than is not None:
            if str(less_than).upper().endswith("M"):
                byte_size = int(less_than[:-1]) * 1024 * 1024
            elif str(less_than).upper().endswith("K"):
                byte_size = int(less_than[:-1]) * 1024
            elif str(less_than).upper().endswith("G"):
                byte_size = int(less_than[:-1]) * 1024 * 1024 * 1024
        
        # 找到所有垃圾文件
        junk_file_list = self.find_all_junk_files(file_info['file_id'], 0, byte_size)
        if not junk_file_list:
            init.logger.info(f"[{path}]下没有找到需要清理的垃圾文件！")
            return
                
        fid_list = []
        for file in junk_file_list:
            fid_list.append(file['fid'])
            init.logger.info(f"[{file['fn']}]已添加到清理列表")
        
        if fid_list:
            self._batch_delete_files(fid_list)

    def batch_process_downloads(self, download_path, resource_names):
        """
        批量处理下载完成的文件，优化API调用次数

        Args:
            download_path: 下载目录路径
            resource_names: 资源名称列表

        Returns:
            dict: {
                "resource_name": {
                    "is_directory": bool,
                    "files": list,  # 该资源下的文件列表
                    "file_id": str,
                    "path": str
                }
            }
        """
        result = {}

        # 第一步：获取下载目录的file_id（只调用一次）
        parent_info = self.get_file_info(download_path)
        if not parent_info:
            init.logger.warn(f"获取下载目录信息失败: {download_path}")
            return result

        parent_cid = parent_info['file_id']

        # 第二步：获取下载目录下所有文件和子目录信息（只调用一次）
        params = {
            "cid": parent_cid,
            "limit": 1000,
            "show_dir": 1  # 同时显示文件和目录
        }
        all_items = self.get_file_list(params)

        if not all_items:
            init.logger.warn(f"获取目录列表失败: {download_path}")
            return result

        # 第三步：为每个资源建立索引（在内存中处理，无需额外API调用）
        items_map = {}
        for item in all_items:
            item_name = item.get('fn', '')
            items_map[item_name] = {
                "is_directory": item.get('fc') == '0',  # fc=0表示目录
                "file_id": item.get('fid'),
                "size": item.get('fs', 0),
                "category": item.get('fc')
            }

        # 第四步：处理每个资源
        for resource_name in resource_names:
            if resource_name not in items_map:
                init.logger.warn(f"资源不存在: {resource_name}")
                continue

            item_info = items_map[resource_name]
            resource_path = f"{download_path}/{resource_name}"

            result[resource_name] = {
                "is_directory": item_info["is_directory"],
                "files": [],
                "file_id": item_info["file_id"],
                "path": resource_path
            }

            # 如果是目录，获取目录下的文件列表
            if item_info["is_directory"]:
                dir_params = {
                    "cid": item_info["file_id"],
                    "type": 4,  # 视频文件
                    "limit": 1000
                }
                file_list = self.get_file_list(dir_params)
                if file_list:
                    result[resource_name]["files"] = [f['fn'] for f in file_list]
            else:
                # 如果是单个文件，文件列表就是它自己
                result[resource_name]["files"] = [resource_name]

        init.logger.info(f"批量处理完成，共处理 {len(result)} 个资源")
        return result

    @staticmethod
    def _parse_clean_policy_byte_size(clean_policy) -> int:
        """安全解析 clean_policy.less_than（支持 K/M/G，不区分大小写）"""
        if not isinstance(clean_policy, dict):
            return 0
        less_than = clean_policy.get('less_than')
        if less_than is None:
            return 0
        raw = str(less_than).strip().upper()
        if not raw:
            return 0
        try:
            if raw.endswith("G"):
                return int(float(raw[:-1]) * 1024 * 1024 * 1024)
            elif raw.endswith("M"):
                return int(float(raw[:-1]) * 1024 * 1024)
            elif raw.endswith("K"):
                return int(float(raw[:-1]) * 1024)
            elif raw.isdigit():
                return int(raw)
        except (TypeError, ValueError):
            init.logger.warn(f"无效的 clean_policy.less_than 配置: {less_than}")
        return 0

    def batch_auto_clean(self, download_path, resource_names, *, is_dedicated_folder=False, exclude_filenames=None):
        """
        批量清理多个下载资源的垃圾文件

        Args:
            download_path: 下载目录路径
            resource_names: 资源名称列表
            is_dedicated_folder: 是否为本次批量任务新建的独立子目录。
                - True: 使用 115 扁平递归接口 find_all_junk_files 一次性跨所有子目录扫描并批量删除（~2-3 次 API）
                - False: 严格限定在 resource_names 对应子目录内逐个扫描（共享目录/单链接安全模式）
            exclude_filenames: 需要排除、不予删除的文件名集合/列表（大小写不敏感）
        """
        clean_policy = (init.bot_config or {}).get('clean_policy') or {}
        switch_val = clean_policy.get('switch', 'off')
        if switch_val is False or str(switch_val).strip().lower() in ("off", "false", "0", ""):
            return

        byte_size = self._parse_clean_policy_byte_size(clean_policy)
        if byte_size <= 0:
            return

        excluded_lower = {str(name).strip().lower() for name in (exclude_filenames or []) if name}

        # 快速路径：独立批次子目录 -> 扁平递归列出全部小于阈值的文件（~3 次 API 完成 100+ 子目录清理）
        if is_dedicated_folder:
            parent_info = self.get_file_info(download_path)
            if not parent_info:
                init.logger.warn(f"获取下载目录信息失败: {download_path}")
                return

            junk_files = self.find_all_junk_files(parent_info['file_id'], 0, byte_size, limit=1150)
            if not junk_files:
                init.logger.info(f"[{download_path}]下没有找到需要清理的垃圾文件")
                return

            all_fid_to_delete = []
            for file in junk_files:
                fn = str(file.get('fn') or '')
                if excluded_lower and fn.lower() in excluded_lower:
                    init.logger.debug(f"[{fn}]在排除列表中，跳过清理")
                    continue
                fid = file.get('fid')
                if fid:
                    all_fid_to_delete.append(fid)
                    init.logger.info(f"[{fn}]已添加到清理列表")

            if all_fid_to_delete:
                self._batch_delete_files(all_fid_to_delete, batch_size=100)
                init.logger.info(f"批量快速清理完成，共删除 {len(all_fid_to_delete)} 个垃圾文件")
            return

        # 安全限定路径（共享目录或单链接）：严格仅清理 resource_names 对应的子目录
        if not resource_names:
            return

        # 获取下载目录信息（只调用一次）
        parent_info = self.get_file_info(download_path)
        if not parent_info:
            init.logger.warn(f"获取下载目录信息失败: {download_path}")
            return

        parent_cid = parent_info['file_id']

        # 获取下载目录下所有文件（只调用一次）
        params = {
            "cid": parent_cid,
            "limit": 1000,
            "show_dir": 1
        }
        all_items = self.get_file_list(params)

        if not all_items:
            return

        # 收集所有需要清理的目录ID
        resource_name_set = set(resource_names)
        dir_ids = []
        for item in all_items:
            if item.get('fn') in resource_name_set and item.get('fc') == '0':  # 是目录
                if item.get('fid'):
                    dir_ids.append(item.get('fid'))

        if not dir_ids:
            return

        # 批量清理每个目录（减少API调用）
        all_fid_to_delete = []

        for dir_id in dir_ids:
            dir_params = {
                "cid": dir_id,
                "limit": 1000,
                "show_dir": 1
            }
            file_list = self.get_file_list(dir_params)

            if not file_list:
                continue

            for file in file_list:
                # 只删除小于指定大小的文件
                if file.get('fc') == '1':  # 是文件
                    fn = str(file.get('fn') or '')
                    if excluded_lower and fn.lower() in excluded_lower:
                        init.logger.debug(f"[{fn}]在排除列表中，跳过清理")
                        continue
                    if (file.get('fs') or 0) < byte_size and file.get('fid'):
                        all_fid_to_delete.append(file['fid'])
                        init.logger.info(f"[{fn}]已添加到清理列表")
                # 跳过目录，不要删除子目录（可能包含有效内容）
                # 如果需要清理空目录，应该在确认目录为空后再删除

        # 分批删除所有垃圾文件
        if all_fid_to_delete:
            self._batch_delete_files(all_fid_to_delete, batch_size=100)
            init.logger.info(f"批量清理完成，共删除 {len(all_fid_to_delete)} 个文件/目录")

    def find_all_junk_files(self, cid, offset, byte_size, file_list=None, limit=1150):
        """
        递归查找所有小于指定大小的垃圾文件

        使用分页查询和文件大小排序优化，当本页已满且最后一个文件仍小于目标大小时继续递归查找，
        否则停止查询并过滤返回小于目标大小的文件。

        Args:
            cid: 目录ID
            offset: 偏移量，用于分页
            byte_size: 目标文件大小（字节），小于此大小的文件被视为垃圾文件
            file_list: 已找到的文件列表，用于递归累积
            limit: 每页查询的文件数量，默认1150

        Returns:
            list: 所有小于目标大小的文件列表，包含文件的fid、fn、fs等信息
        """
        if file_list is None:
            file_list = []

        if byte_size <= 0:
            return []

        params = {
            "cid": cid,
            "limit": limit,
            "show_dir": 0,
            "custom_order": 1,
            "asc": 1,
            "o": "file_size",
            "offset": offset
        }

        # 获取当前页的文件列表
        current_files = self.get_file_list(params)

        # 如果API调用失败或没有获取到文件，说明已经到末尾或出现错误
        if not current_files:
            # 过滤掉大于等于目标大小的文件，只返回垃圾文件
            return [f for f in file_list if f.get('fc') != '0' and (f.get('fs') or 0) < byte_size]

        # 将当前页的文件添加到结果列表
        file_list.extend(current_files)

        # 检查最后一个文件的大小
        last_file_size = current_files[-1].get('fs') or 0

        # 仅当本页返回数达到 limit（可能还有下一页）且最后一个文件仍小于目标大小时才继续翻页
        if len(current_files) >= limit and last_file_size < byte_size:
            offset += limit
            time.sleep(1)  # _make_api_request 已限速 1 req/s，温和间隔避免突发
            return self.find_all_junk_files(cid, offset, byte_size, file_list, limit=limit)
        else:
            # 已经找到所有小于目标大小的文件，过滤掉大于等于目标大小的文件
            return [f for f in file_list if f.get('fc') != '0' and (f.get('fs') or 0) < byte_size]


    def create_dir_recursive(self, path):
        """递归创建目录"""
        res = self.get_file_info(path)
        if res:
            init.logger.info(f"[{path}]目录已存在！")
            return
        path_list= get_parent_paths(path)
        last_path = ""
        for index, item in enumerate(path_list):
            res = self.get_file_info(item)  # 确保目录存在
            if res:
                last_path = item
            else:
                if index == 0:
                    if item.startswith("/"):
                        self.create_directory(0, item[1:])
                    else:
                        self.create_directory(0, item)
                    time.sleep(1)  # 等待目录创建完成
                    last_path = item
                if index > 0:
                    file_info = self.get_file_info(last_path)
                    self.create_directory(file_info['file_id'], os.path.basename(item))
                    time.sleep(1)
                    last_path = item
                    
        init.logger.info(f"目录[{path}]创建成功！")

        
            
    @staticmethod
    def save_token_to_file(access_token: str, refresh_token: str, file_path: str):
        """将access_token和refresh_token保存到文件"""
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)
        init.logger.info(f"Tokens saved to {file_path}")
        
    @staticmethod
    def get_challenge() -> str:
        # 生成随机字节（避免直接使用 ASCII 字符以确保安全随机性）
        random_bytes = os.urandom(64)
        # 转换为 URL-safe Base64，并移除填充字符（=）
        verifier = base64.urlsafe_b64encode(random_bytes).rstrip(b'=').decode('utf-8')
        # 确保符合规范（虽然 urlsafe_b64encode 已满足要求，此处做二次验证）
        verifier = re.sub(r'[^A-Za-z0-9\-._~]', '', verifier)[:64]  # 限制长度为64字符
        sha256_hash = hashlib.sha256(verifier.encode('utf-8')).digest()
        # Base64 URL 安全编码并移除填充字符
        challenge = base64.urlsafe_b64encode(sha256_hash).rstrip(b'=').decode('utf-8')
        return verifier, challenge
    
def file_sha1(file_path):
    with open(file_path, 'rb') as f:
        return hashlib.sha1(f.read()).hexdigest()
    
def sha1_digest(file_path):
    h = hashlib.sha1()
    with Path(file_path).open('rb') as f:
        for chunk in iter(lambda: f.read(128), b''):
            h.update(chunk)
            break
    return h.hexdigest()


def calculate_sha1(file_path):
    """计算文件的SHA1哈希值"""
    sha1 = hashlib.sha1()
    try:
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                sha1.update(chunk)
        return sha1.hexdigest()
    except FileNotFoundError:
        init.logger.error(f"错误：文件未找到 -> {file_path}")
        return None
    
def file_sha1_by_range(file_path, start, end):
    """计算文件从start到end（含end）的SHA1"""
    size = end - start + 1
    sha1 = hashlib.sha1()
    with open(file_path, "rb") as f:
        f.seek(start)
        data = f.read(size)
        sha1.update(data)
    return sha1.hexdigest()


def get_parent_paths(path):
    """
    获取路径的所有父级路径列表
    :param path: 输入路径，如 "/AV/rigeng/111/222"
    :return: 父级路径列表，如 ["/AV", "/AV/rigeng", "/AV/rigeng/111"]
    """
    # 规范化路径（处理多余的斜杠等问题）
    normalized_path = os.path.normpath(path)
    
    # 分割路径
    parts = normalized_path.split(os.sep)
    
    # 处理Unix系统的根目录情况
    if parts[0] == '':
        parts[0] = os.sep
    
    # 逐步构建路径
    result = []
    current_path = parts[0] if parts[0] == os.sep else ""
    
    for part in parts[1:]:
        current_path = os.path.join(current_path, part)
        result.append(current_path)
    
    return result


if __name__ == "__main__":
    init.init_log()
    init.load_yaml_config()
    app = OpenAPI_115()
    app.offline_download_specify_path("magnet:?xt=urn:btih:56D89F7BD61BE24E2E1374E5E3AFF9E256BA3D7", "/test")
    # app.offline_download_specify_path("magnet:?xt=urn:btih:2A93EFB4E2E8ED96B52207D9C5AA4FF2F7E8D9DF", "/test")
    # time.sleep(10)
    # dl_flg, resource_name = app.check_offline_download_success_no_waite("magnet:?xt=urn:btih:2A93EFB4E2E8ED96B52207D9C5AA4FF2F7E8D9DF")
    # print(dl_flg, resource_name)
    # quota_info = app.get_quota_info()
    # print(f"离线下载配额: {quota_info['used']}/{quota_info['count']}")

    # app.auto_clean(f"{init.bot_config['offline_path']}/nyoshin-n1996")
    # app.clear_failed_task("magnet:?xt=urn:btih:C506443C77A1F7EC3D18718F0DAC6AAA2BCE1FB6&dn=nyoshin-n1996")  # 示例URL
    # if app.is_directory(f"{init.bot_config['offline_path']}"):
    #     init.logger.info("这是一个目录")
    # else:
    #     init.logger.info("这不是一个目录")
    # app.create_dir_for_video_file(f"{init.bot_config['offline_path']}/gc2048.com-agnes-sss.mp4")
    # file_list = app.get_files_from_dir(f"{init.bot_config['offline_path']}/极品眼镜妹~【agnes-sss】清纯外表~长腿黑丝~白领装~全裸跳蛋")
    # for file in file_list:
    #     init.logger.info(f"找到视频文件: {file}")
    # app.rename(f"{init.bot_config['offline_path']}/temp", "1111")
    # if app.offline_download("magnet:?xt=urn:btih:C506443C77A1F7EC3D18718F0DAC6AAA2BCE1FB6&dn=nyoshin-n1996"):
    #     init.logger.info("离线下载任务添加成功")
    #     if app.check_offline_download_success("magnet:?xt=urn:btih:C506443C77A1F7EC3D18718F0DAC6AAA2BCE1FB6&dn=nyoshin-n1996"):
    #         init.logger.info("离线下载任务成功")
    #     else:
    #         init.logger.error("离线下载任务失败或超时")
    #         app.clear_failed_task("magnet:?xt=urn:btih:C506443C77A1F7EC3D18718F0DAC6AAA2BCE1FB6&dn=nyoshin-n1996")
    # file_path = f"{init.TEMP}/20250713174710.mp4"
    # file_size = os.path.getsize(file_path)
    # file_name = os.path.basename(file_path)
    # sha1_value = file_sha1(file_path)
    # up_flg, bingo = app.upload_file(
    #     target="/AV/国产直播精选",
    #     file_name=file_name,
    #     file_size=file_size,
    #     fileid=sha1_value,
    #     file_path=file_path,
    #     request_times=1  # 第一次请求
    # )
    # if up_flg and bingo:
    #     init.logger.info(f"秒传成功")
    # elif up_flg and not bingo:
    #     init.logger.error("文件上传成功")
    # elif not up_flg and not bingo:
    #     init.logger.error("文件上传失败")
    # welcome_text = app.welcome_message()
    # init.logger.info(welcome_text)
    # app.clear_cloud_task()  # 清理云端任务
