import aria2p
import os 
import time
import sys
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
sys.path.append(current_dir)
import init


aria2 = None

def create_aria2_client(host, port, secret):
    global aria2
    if not host or not port or not secret:
        init.logger.warn("Aria2配置不完整，跳过Aria2实例化")
        return None
    try:
        aria2 = aria2p.API(
            aria2p.Client(
                host=host,
                port=port,
                secret=secret
            )
        )
        init.logger.info(f"Aria2客户端已配置，连接到 {host}:{port}")
        return aria2
    except Exception as e:
        init.logger.error(f"Aria2客户端配置失败: {e}")
        return aria2

def download_by_url(download_url, save_path=""):
    """通过下载链接添加任务"""
    if not aria2:
        init.logger.warn("Aria2客户端未配置")
        return None
    try:
        options = {
                "User-Agent": init.USER_AGENT
            }
        if save_path:
            options['dir'] = save_path
        download = aria2.add(download_url, options=options)
        init.logger.info(f"已添加下载任务: {download_url}")
        return download
    except Exception as e:
        init.logger.error(f"添加下载任务失败: {e}")
        return None


def check_status_by_url(download_url):
    """通过下载链接检查任务状态"""
    if not aria2:
        init.logger.warn("Aria2客户端未配置")
        return {"status": "error", "message": "Aria2客户端未配置"}
    
    try:
        # 获取所有下载任务（包括活动的、已完成的、出错的等）
        downloads = aria2.get_downloads()
        init.logger.debug(f"当前下载任务数量: {len(downloads)}")
        
        # 标准化输入URL用于比较
        target_url = download_url.strip()
        init.logger.debug(f"查找目标URL: {target_url}")
        
        # 遍历所有下载任务
        for i, download in enumerate(downloads):
            init.logger.debug(f"检查第{i+1}个任务 - GID: {download.gid}, 状态: {download.status}")
            
            # 获取下载任务的URL列表
            task_urls = _extract_download_urls(download)
            init.logger.debug(f"任务{download.gid}的URL列表: {task_urls}")
            
            # 检查URL是否匹配 - 使用多种匹配策略
            for task_url in task_urls:
                # 完全匹配
                if target_url == task_url:
                    init.logger.info(f"完全匹配找到下载任务: {download.gid}")
                    return get_status(download)
                
                # 忽略大小写匹配
                if target_url.lower() == task_url.lower():
                    init.logger.info(f"忽略大小写匹配找到下载任务: {download.gid}")
                    return get_status(download)
                
                # URL编码匹配（有时URL可能被编码）
                try:
                    from urllib.parse import unquote
                    if unquote(target_url).lower() == unquote(task_url).lower():
                        init.logger.info(f"URL解码匹配找到下载任务: {download.gid}")
                        return get_status(download)
                except Exception:
                    pass
        
        # 如果没有找到匹配的任务，输出调试信息
        init.logger.warn(f"未找到匹配的下载任务")
        init.logger.warn(f"目标URL: {target_url}")
        for download in downloads:
            urls = _extract_download_urls(download)
            init.logger.warn(f"任务{download.gid}的URLs: {urls}")
        
        return {"status": "not_found", "message": "未找到匹配的下载任务"}
        
    except Exception as e:
        init.logger.error(f"检查下载状态失败: {e}")
        import traceback
        init.logger.error(f"详细错误: {traceback.format_exc()}")
        return {"status": "error", "message": f"检查下载状态失败: {str(e)}"}


def _extract_download_urls(download):
    """从下载任务中提取所有URL"""
    urls = []
    
    try:
        # 方法1: 从files中获取URL
        if hasattr(download, 'files') and download.files:
            for file in download.files:
                if hasattr(file, 'uris') and file.uris:
                    for uri in file.uris:
                        if hasattr(uri, 'uri'):
                            urls.append(uri.uri)
        
        # 方法2: 从following中获取URL（适用于磁力链接等）
        if hasattr(download, 'following') and download.following:
            if hasattr(download.following, 'files') and download.following.files:
                for file in download.following.files:
                    if hasattr(file, 'uris') and file.uris:
                        for uri in file.uris:
                            if hasattr(uri, 'uri'):
                                urls.append(uri.uri)
        
        # 方法3: 尝试从原始请求中获取URL（aria2p可能存储原始URL）
        if hasattr(download, '_struct') and download._struct:
            # 检查原始结构体中是否有URL信息
            struct = download._struct
            if 'files' in struct:
                for file in struct['files']:
                    if 'uris' in file:
                        for uri in file['uris']:
                            if 'uri' in uri:
                                urls.append(uri['uri'])
        
        # 方法4: 尝试直接访问内部属性
        for attr in ['url', 'uri', 'source_url']:
            if hasattr(download, attr):
                value = getattr(download, attr)
                if value and isinstance(value, str):
                    urls.append(value)
        
        # 去重
        urls = list(set(urls))
            
    except Exception as e:
        init.logger.debug(f"提取下载URL时出错: {e}")
    
    return urls


def check_status_by_gid(gid):
    """通过GID检查任务状态"""
    if not aria2:
        init.logger.warn("Aria2客户端未配置")
        return {"status": "error", "message": "Aria2客户端未配置"}
    
    try:
        downloads = aria2.get_downloads()
        for download in downloads:
            if download.gid == gid:
                return get_status(download)
        return {"status": "not_found", "message": f"未找到GID为{gid}的下载任务"}
    except Exception as e:
        init.logger.error(f"通过GID检查下载状态失败: {e}")
        return {"status": "error", "message": f"检查下载状态失败: {str(e)}"}


def get_status(download):
    """获取下载状态详情
    status: active, waiting, paused, error, complete
    """
    return {
        "gid": download.gid,
        "status": download.status,
        "name": download.name,
        "completed": download.completed_length,
        "total": download.total_length,
        "progress": download.progress,
        "speed": download.download_speed,
        "error": download.error_message if download.status == "error" else None
    }
    

if __name__ == "__main__":
    init.create_logger()
    create_aria2_client("https://aria2.qiqiandfei.fun", 8843, "emp89aW0MhYUogku")
    
    test_url = "https://cdnfhnfile.115cdn.net/68a941a4bbd6418ecd5a8f7bf9a135fdf9db7479/%E6%9E%81%E9%80%9F%E5%A5%B3%E7%A5%9E.Eenie.Meanie.2025.WEB-DL.1080p.X264.mkv?t=1758082642&u=12104725&s=524288000&d=vip-1928424613-eyy3kyc3amj7rzfpd-1-100197303&c=2&f=1&k=6997e6a0a8f0d06482d129a565b14fda&us=5242880000&uc=10&v=1"
    
    print("添加下载任务...")
    download_task = download_by_url(test_url)
    
    if download_task:
        print(f"任务添加成功，GID: {download_task.gid}")
        # 等待任务加载
        print("等待3秒让任务加载...")
        time.sleep(3)
    else:
        print("任务添加失败")
    
    # 先尝试列出所有任务
    print("\n=== 当前所有下载任务 ===")
    try:
        downloads = aria2.get_downloads()
        for i, download in enumerate(downloads):
            print(f"任务{i+1}: GID={download.gid}, 状态={download.status}, 名称={download.name}")
            urls = _extract_download_urls(download)
            print(f"  URLs: {urls}")
    except Exception as e:
        print(f"获取任务列表失败: {e}")
    
    print(f"\n=== 开始监控下载状态 ===")
    while True:
        status = check_status_by_url(test_url)
        print(f"状态检查结果: {status}")
        time.sleep(5)
        if status['status'] == "complete":
            print("下载完成，停止检查")
            break
        elif status['status'] == "error":
            print("下载出错，停止检查")
            break
        elif status['status'] == "not_found":
            print("仍未找到匹配任务，继续等待...")
            # 尝试通过GID检查（如果有的话）
            if download_task and hasattr(download_task, 'gid'):
                gid_status = check_status_by_gid(download_task.gid)
                print(f"通过GID检查: {gid_status}")
        
        # 防止无限循环，设置最大检查次数
        if hasattr(check_status_by_url, '_check_count'):
            check_status_by_url._check_count += 1
        else:
            check_status_by_url._check_count = 1
            
        if check_status_by_url._check_count > 20:  # 最多检查20次
            print("达到最大检查次数，停止监控")
            break
        