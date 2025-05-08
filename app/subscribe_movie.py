# -*- coding: utf-8 -*-

import requests
import init
from bs4 import BeautifulSoup
from sqlitelib import *
from download_handler import create_strm_file, notice_emby_scan_library
from message_queue import add_task_to_queue
from cover_capture import get_movie_cover


def get_tmdb_id(movie_name):
    """
    从TMDB获取电影ID
    :param movie_name: 电影名称
    :return: (tmdb_id, title) 或 (None, None)
    """
    base_url = "https://www.themoviedb.org"
    search_url = f"{base_url}/search?query={movie_name}"

    headers = {
        "user-agent": init.USER_AGENT,
        "accept-language": "zh-CN"
    }

    try:
        response = requests.get(url=search_url, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, features="html.parser")

        # 检查是否有搜索结果
        no_results = soup.find('p', class_='zero-results')
        if no_results and "没有找到相关结果" in no_results.text:
            return None

        # 查找第一个电影结果
        movie_card = soup.find('div', class_='card')
        if not movie_card:
            return None

        # 获取链接和标题
        link = movie_card.find('a', class_='result')
        if not link or 'href' not in link.attrs:
            return None

        # 从URL中提取TMDB ID
        href = link['href']
        if '/movie/' not in href:
            return None

        tmdb_id = href.split('/movie/')[-1].split('-')[0]
        if not tmdb_id.isdigit():
            return None

        return int(tmdb_id)

    except requests.exceptions.RequestException as e:
        init.logger.error(f"获取TMDB ID失败: {str(e)}")
        return None
    except Exception as e:
        init.logger.error(f"解析TMDB页面失败: {str(e)}")
        return None
    

def schedule_movie():
    with SqlLiteLib() as sqlite:
        try:
            # 查询需要处理的数据
            query = "SELECT tmdb_id, movie_name, category_folder FROM sub_movie WHERE is_download = 0"
            rows = sqlite.query(query)
            for row in rows:
                tmdb_id, movie_name, category_folder = row
                download_url = search_update(tmdb_id)
                if download_url:
                    init.logger.info(f"电影[{movie_name}]已更新，下载链接为[{download_url}], 正在添加到离线下载...")
                    # 添加到离线下载
                    if download_from_link(download_url, movie_name, category_folder):
                        # 更新下载状态
                        update_download_sql = "UPDATE sub_movie SET is_download = 1 WHERE tmdb_id = ?"
                        sqlite.execute_sql(update_download_sql, (tmdb_id,))
                        # 发送消息给用户
                        send_message2usr(tmdb_id, sqlite)
                        init.logger.info(f"订阅电影[{movie_name}]下载成功！")
        except Exception as e:
            init.logger.error(f"执行电影定时更新任务失败: {str(e)}")
            return
        
        
def search_update(tmdb_id):
    # 优先ed2k
    url = f"https://api.nullbr.eu.org/movie/{tmdb_id}/ed2k"
    res = get_response_from_api(url)
    highest_score_item = check_condition(res, "ed2k")
    if highest_score_item:
        # 更新数据库
        update_sub_movie(tmdb_id, highest_score_item)
        return highest_score_item['download_url']
    # 找不到ed2k就找磁力
    url = f"https://api.nullbr.eu.org/movie/{tmdb_id}/magnet"
    res = get_response_from_api(url)
    highest_score_item = check_condition(res, "magnet")
    if highest_score_item:
        # 更新数据库
        update_sub_movie(tmdb_id, highest_score_item)
        return highest_score_item['download_url']
    return None


def update_sub_movie(tmdb_id, highest_score_item):
    movie_name = get_moive_name(tmdb_id)
    post_url = get_movie_cover(movie_name)
    with SqlLiteLib() as sqlite:
        sql = "update sub_movie set download_url=?, post_url=?, size=? where tmdb_id=?"
        params = (highest_score_item['download_url'], post_url, highest_score_item['size'], tmdb_id)
        sqlite.execute_sql(sql, params)
        
        
def get_moive_name(tmdb_id):
    with SqlLiteLib() as sqlite:
        sql = "select movie_name from sub_movie where tmdb_id=?"
        params = (tmdb_id,)
        result = sqlite.query_one(sql, params)
        if result:
            return result
        else:
            return None

def check_condition(res, key):
    download_url = ""
    res_list = []
    for item in res[key]:
        score = 0
        movie_name = item['name']
        zh_sub = item['zh_sub']
        resolution = item['resolution']
        download_url = item[key]
        size = item['size']
        quality = item['quality']
        is_dolby_vision = False
        if quality:
            if isinstance(quality, list):
                if "Dolby Vision" in quality:
                    is_dolby_vision = True
            if isinstance(quality, str):
                if "Dolby Vision" == quality or "dolby vision" == quality.lower():
                    is_dolby_vision = True
        if init.bot_config['sub_condition']['dolby_vision'] and is_dolby_vision:
            score += 10
        if zh_sub == 1:
             score += 10
        for index, cfg_resolution in enumerate(init.bot_config['sub_condition']['resolution_priority'], 0):
            if resolution:
                if str(cfg_resolution) in resolution or str(cfg_resolution) in movie_name:
                    score += len(init.bot_config['sub_condition']['resolution_priority']) - index
            else:
                if str(cfg_resolution) in movie_name:
                    score += len(init.bot_config['sub_condition']['resolution_priority']) - index
        res_list.append({'score': score, 'download_url': download_url, 'size': size, 'zh_sub': zh_sub, 'is_dolby_vision': is_dolby_vision})
    if res_list:
        # 按分数从高到低排序
        sorted_res_list = sorted(res_list, key=lambda x: x['score'], reverse=True)
        for item in sorted_res_list:
            if init.bot_config['sub_condition']['dolby_vision']:
                # 必须同时满足杜比卫视和中字
                if item['zh_sub'] == 0 or item['is_dolby_vision'] == False:
                    continue
            else:
                if item['zh_sub'] == 0 or item['is_dolby_vision'] == True:
                    continue
            highest_score_item = item
            break
        return highest_score_item
    return None


def get_response_from_api(url):
    headers = {
        "User-Agent": init.USER_AGENT,
        "X-APP-ID": init.bot_config['x_app_id'],
        "X-API-KEY": init.bot_config['x_api_key']
    }
    response = requests.get(url, headers=headers)
    return response.json()


def download_from_link(download_url, movie_name, save_path):
    try: 
        if not init.initialize_115client():
            init.logger.error(f"💀115Cookie已过期，请重新设置！")
            return False
        response, resource_name = init.client_115.offline_download(download_url)
        if response.get('errno') is not None:
            init.logger.error(f"❌离线遇到错误！error_type: {response.get('errtype')}！")
        else:
            init.logger.info(f"✅[{resource_name}]添加离线成功")
            download_success = init.client_115.check_offline_download_success(download_url, resource_name)
            if download_success:
                init.logger.info(f"✅[{resource_name}]离线下载完成")
                if init.client_115.is_directory(f"{init.bot_config['offline_path']}/{resource_name}"):
                    # 清除垃圾文件
                    init.client_115.auto_clean(f"{init.bot_config['offline_path']}/{resource_name}")
                    # 重名名资源
                    init.client_115.rename(f"{init.bot_config['offline_path']}/{resource_name}", f"{init.bot_config['offline_path']}/{movie_name}")
                    # 移动文件
                    init.client_115.move_file(f"{init.bot_config['offline_path']}/{resource_name}", save_path)
                else:
                    # 创建文件夹
                    init.client_115.create_folder(f"{init.bot_config['offline_path']}/{movie_name}")
                    # 移动文件到番号文件夹
                    init.client_115.move_file(f"{init.bot_config['offline_path']}/{resource_name}", f"{init.bot_config['offline_path']}/{movie_name}")
                    # 移动番号文件夹到指定目录
                    init.client_115.move_file(f"{init.bot_config['offline_path']}/{movie_name}", save_path)
                
                # 读取目录下所有文件
                file_list = init.client_115.get_files_from_dir(f"{save_path}/{movie_name}")
                # 创建软链
                create_strm_file(f"{save_path}/{movie_name}", file_list)
                # 通知Emby扫库
                notice_emby_scan_library()
                return True
            else:
                # 下载超时删除任务
                init.client_115.clear_failed_task(download_url, resource_name)
                return False
    except Exception as e:
        init.logger.error(f"💀下载遇到错误: {str(e)}")
        return False
    
    
def send_message2usr(tmdb_id, sqlite):
    try:
        query = "select sub_user,download_url,size,movie_name,post_url,category_folder from sub_movie where tmdb_id=?"
        params = (tmdb_id,)
        row = sqlite.query_row(query, params)
        if not row:
            init.logger.warn(f"未找到TMDB编号为[{tmdb_id}]的记录!")
            return
        sub_user, download_url, size, movie_name, post_url, category_folder = row
        msg_title = escape_markdown_v2(f"{movie_name}[{tmdb_id}]订阅已下载!")
        msg_category_folder = escape_markdown_v2(category_folder)
        msg_size = escape_markdown_v2(str(size))
        message = f"""
                **{msg_title}**

                **大小:** {msg_size}  
                **保存目录:** {msg_category_folder}
                **下载链接:** `{download_url}`  
                """
        add_task_to_queue(sub_user, post_url, message)
        init.logger.info(f"[{movie_name}] 加入队列成功！")

    except Exception as e:
        init.logger.error(f"电影[{movie_name}] 添加到队列失败: {e}")
        

def escape_markdown_v2(text: str) -> str:
    """
    转义字符串以符合 Telegram MarkdownV2 的要求。
    如果字符串被反引号包裹，则内部内容不转义。
    :param text: 原始字符串
    :return: 转义后的字符串
    """
    # 需要转义的字符
    escape_chars = r"\_*[]()~`>#+-=|{}.!"

    # 判断是否被反引号包裹
    if text.startswith("`") and text.endswith("`"):
        # 反引号包裹的内容不转义
        return text
    else:
        # 转义特殊字符
        escaped_text = "".join(f"\\{char}" if char in escape_chars else char for char in text)
        return escaped_text
    
    
def is_subscribe(tmdb_id):
    with SqlLiteLib() as sqlite:
        sql = "select movie_name from sub_movie where tmdb_id=?"
        params = (tmdb_id,)
        result = sqlite.query_one(sql, params)
        if result:
            return True
        else:
            return False

def update_subscribe(movie_name, post_url, download_url):
    tmdb_id = get_tmdb_id(movie_name)
    if tmdb_id:
        with SqlLiteLib() as sqlite:
            update_download_sql = "UPDATE sub_movie SET is_download = 1, post_url = ?, download_url = ? WHERE tmdb_id = ?"
            sqlite.execute_sql(update_download_sql, (post_url, download_url, tmdb_id,))
            init.logger.info(f"订阅影片[{movie_name}]已手动完成下载!")
            


# if __name__ == '__main__':
#     init.init_log()
#     movie_name = get_moive_name(1195506)
#     post_url = get_movie_cover(movie_name)
#     print(post_url)