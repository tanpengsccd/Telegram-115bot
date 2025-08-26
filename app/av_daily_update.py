import requests
from sqlitelib import *
import datetime
from bs4 import BeautifulSoup
import init
import time
import re
from message_queue import add_task_to_queue
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_max_page(html_content):    
    """
    获取最新AV的最大页数
    :return: 最大页数
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    # 查找所有的页码链接
    page_links = soup.select('ul.pagination-list li a.pagination-link')
    page_numbers = []
    for link in page_links:
        href = link.get('href')
        if href and 'page=' in href:
            # 从href中提取页码数字
            page_num = int(href.split('page=')[1])
            page_numbers.append(page_num)
        elif link.text.isdigit():
            # 如果链接没有href但有数字文本
            page_numbers.append(int(link.text))
    max_page = 1
    if page_numbers:
        max_page = max(page_numbers)
    return max_page

def get_today_av():
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    url = f"https://javbee.vip/date/{date}"
    response = requests.get(url, verify=False)
    if response.status_code == 500:
        init.logger.warn(f"服务器响应错误，可能是[{date}]尚未更新。")
        return None
    if response.status_code != 200:
        init.logger.warn(f"获取日更信息错误，HTTP Code: {response.status_code}")
        return None
    max_page = get_max_page(response.text)
    if not max_page:
        return None
    else:
        results = []
        for page in range(1, max_page + 1):
            page_url = f"{url}?page={page}"
            response = requests.get(page_url, verify=False)
            soup = BeautifulSoup(response.text, 'html.parser')
            cards = soup.find_all('div', class_='card mb-3')
            for card in cards:
                # 1. 提取番号+标题（从<h5>中的<a>标签）
                title_tag = card.select_one('h5.title a')
                if title_tag:
                    pub_url = title_tag['href'] if title_tag.has_attr('href') else "N/A"
                    full_text = title_tag.get_text(strip=True)  # 获取纯文本
                    parts = full_text.strip().split(' ')  # 按空格分割
                    av_number, av_title = get_avnumber_title(parts)
                else:
                    av_number, av_title, pub_url = "N/A", "N/A", "N/A"

                # 2. 提取封面（从<img>的data-src属性）
                img_tag = card.find('img', class_='image lazy')
                cover_url = img_tag['data-src'] if img_tag and img_tag.has_attr('data-src') else "N/A"
                
                # 3. 提取磁力链接（关键新增部分）
                magnet_tag = card.find('a', title="Download Magnet")
                magnet_url = magnet_tag['href'] if magnet_tag and magnet_tag.has_attr('href') else "N/A"
                magnet_url = get_minimal_magnet(magnet_url)  # 获取最小化的磁力链接
    
                if av_number == "N/A" or av_title == "N/A" or magnet_url == "N/A":
                    break
                # 保存结果
                results.append({
                    'av_number': av_number,
                    'av_title': av_title,
                    'cover_url': cover_url,
                    'magnet_url': magnet_url,
                    'publish_date': date,
                    'pub_url': pub_url
                })
            time.sleep(3)  # 避免请求过快
        return results   
    
def save_av_daily_update2db(results):
    with SqlLiteLib() as sqlite:
        for item in results:
            av_number = item['av_number']
            publish_date = item['publish_date']
            title = item['av_title']
            post_url = item['cover_url']
            magnet = item['magnet_url']
            pub_url = item['pub_url']
            
            # 检查是否已存在相同的记录
            sql_check = "SELECT COUNT(*) FROM av_daily_update WHERE av_number=? AND publish_date=?"
            params_check = (av_number, publish_date)
            count = sqlite.query_one(sql_check, params_check)
            
            if count is not None and count > 0:
                init.logger.info(f"AV {av_number} 已存在，跳过保存。")
                continue
            
            # 插入新记录
            sql_insert = """
                INSERT INTO av_daily_update (av_number, publish_date, title, post_url, magnet, pub_url)
                VALUES (?, ?, ?, ?, ?, ?)
            """
            params_insert = (av_number, publish_date, title, post_url, magnet, pub_url)
            sqlite.execute_sql(sql_insert, params_insert)
            init.logger.info(f"AV日更 {av_number} 保存成功。")   
            
def av_daily_update():
    # 检查配置是否启用AV日更
    if not init.bot_config.get('av_daily_update', {}).get('enable', False):
        init.logger.info("AV日更功能未启用，跳过更新。")
        return
    # 获取今天的AV更新
    init.logger.info("开始获取今天的AV更新...")
    results = get_today_av()
    if results:
        # 保存到数据库
        save_av_daily_update2db(results)
        init.logger.info("AV日更数据保存成功。")
        # 离线到115
        offline2115()
    else:
        init.logger.info("没有找到最新的AV更新。")  
        
def av_daily_retry():
    # 检查配置是否启用AV日更
    if not init.bot_config.get('av_daily_update', {}).get('enable', False):
        init.logger.info("AV日更功能未启用，跳过更新。")
        return
    # 离线到115
    offline2115()
    
    
def repair_leak():
    if not init.bot_config.get('av_daily_update', {}).get('enable', False):
        return
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    need_update = False
    with SqlLiteLib() as sqlite:
        sql = "select COUNT(*) from av_daily_update where publish_date=?"
        count = sqlite.query_one(sql, (date,))
        if count is not None and count == 0:
            need_update = True
    if need_update:
        av_daily_update()
        

def offline2115():
    # 确保日更目录存在
    init.openapi_115.create_dir_recursive(init.bot_config['av_daily_update']['save_path'])
    update_list = []
    # 找到需要下载的AV
    with SqlLiteLib() as sqlite:
        sql = "SELECT av_number, magnet, publish_date, title, post_url, pub_url FROM av_daily_update WHERE is_download=0 ORDER BY publish_date DESC"
        need_offline_av = sqlite.query(sql)
        if not need_offline_av:
            init.logger.info("没有需要离线下载的日更")
            return
        for row in need_offline_av:
            av_number = row[0]
            magnet = row[1]
            publish_date = row[2]
            title = row[3]
            post_url = row[4]
            pub_url = row[5]
            update_list.append({
                "av_number": av_number,
                "magnet": magnet,
                "publish_date": publish_date,
                "title": title,
                "post_url": post_url,
                "pub_url": pub_url,
                "success": False  # 初始状态为未成功
            })
    
    # 清除离线任务避免重复下载
    init.openapi_115.clear_cloud_task()
    
    # 添加到离线下载
    offline_tasks = ""
    for item in update_list:
        offline_tasks += item['magnet'] + "\n"
    offline_tasks = offline_tasks[:-1]  # 去掉最后的换行符
    
    # 调用115的离线下载API
    offline_success = init.openapi_115.offline_download_specify_path(
        offline_tasks,
        init.bot_config['av_daily_update']['save_path'])
    if not offline_success: 
        init.logger.error(f"{item['av_number']}添加离线失败!")
    else:
        init.logger.info(f"{item['av_number']}添加离线成功!")
            
    # 等待离线完成
    time.sleep(300)  # 等待一段时间，确保离线成功
    
    # 检查离线下载状态     
    for item in update_list:
        download_success, resource_name = init.openapi_115.check_offline_download_success_no_waite(item['magnet'])
        if download_success:
            # 如果资源名与番号不一致，重命名
            if item['av_number'] != resource_name:
                old_name = f"{init.bot_config['av_daily_update']['save_path']}/{resource_name}"
                init.openapi_115.rename(old_name, item['av_number'])
            # 删除垃圾文件
            init.openapi_115.auto_clean(f"{init.bot_config['av_daily_update']['save_path']}/{item['av_number']}")
            # 更新数据库状态
            with SqlLiteLib() as sqlite:
                sql_update = "UPDATE av_daily_update SET is_download=1 WHERE av_number=?"
                params_update = (item['av_number'],)
                sqlite.execute_sql(sql_update, params_update)
            init.logger.info(f"{item['av_number']} 离线下载成功！")
            item['success'] = True
            # 发送通知
            if init.bot_config.get('av_daily_update', {}).get('notify_me', False):
                msg_title = init.escape_markdown_v2(item['title'])
                msg_date = init.escape_markdown_v2(item['publish_date'])
                pub_url = item['pub_url']
                message = f"""
**AV日更通知**

**番号:**   `{item['av_number']}`
**标题:**   {msg_title}
**发布日期:** {msg_date}
**下载链接:** `{item['magnet']}`
**发布链接:** [点击查看详情]({pub_url})
                """
                add_task_to_queue(init.bot_config['allowed_user'], item['post_url'], message)
            time.sleep(10)  # 避免发送过快
        else:
            init.logger.warn(f"{item['av_number']} 离线下载失败或未完成。")
            # 删除离线失败的文件
            init.openapi_115.clear_failed_task(item['magnet'])

    total_count = len(update_list)
    success_count = sum(1 for item in update_list if item['success'])
    message = f"本次AV日更结束！总计离线：{total_count}， 成功：{success_count}， 失败：{total_count - success_count}"
    init.logger.info(message) 
    if total_count != success_count:
        init.logger.info("失败的任务会在下次自动重试，请检查日志。")
        message += "\n失败的任务会在下次自动重试，请留意日志或通知！"
    
    add_task_to_queue(init.bot_config['allowed_user'], f"{init.IMAGE_PATH}/male022.png", message)   
        
def get_minimal_magnet(magnet_link):
    return re.sub(r"(&dn=.*|&tr=.*)", "", magnet_link)

def has_cjk_chars(text):
    """检查字符串是否包含中文或日文字符"""
    pattern = re.compile(
        r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff\uff65-\uff9f]'
    )
    return bool(pattern.search(text))

def is_pure_number(s):
    """检查是否为纯数字"""
    return bool(re.fullmatch(r'^\d+$', s)) 

def has_letters_and_digits(s):
    """检查是否同时包含英文和数字（允许-和_）"""
    return bool(re.fullmatch(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d\-_]+$', s))

def get_avnumber_title(parts):
    longest = 0
    longest_index = -1
    av_number = "N/A"
    av_title = "N/A"
    for i, part in enumerate(parts):
        if len(part) > longest and has_cjk_chars(part):
            longest = len(part)
            longest_index = i
        if longest_index != -1:
            av_title = parts[longest_index] 
        if (is_pure_number(part) or has_letters_and_digits(part)) and i != len(parts) - 1:
            av_number = part
    return av_number, av_title


if __name__ == "__main__":
    tmp = " FC2-PPV-4747086 モ無/フェラ/□り/ 18歳ユウカちゃん・ふとした瞬間に大胆になる普段は大人しい□りの欲求不満爆発サイン ".strip()
    parts = tmp.split(' ')
    av_number, av_title = get_avnumber_title(parts)
    print(f"番号: {av_number}, 标题: {av_title}")
    tmp = " [FHD] ABF-260 神テクたったの10分間我慢することが出来れば…ご褒美なま中出し 八掛うみ【限定特典映像35分付き】".strip()
    parts = tmp.split(' ')
    av_number, av_title = get_avnumber_title(parts)
    print(f"番号: {av_number}, 标题: {av_title}")
    tmp = " FC2-PPV-4739066 【素人・玲】 ".strip()
    parts = tmp.split(' ')
    av_number, av_title = get_avnumber_title(parts)
    print(f"番号: {av_number}, 标题: {av_title}")
    tmp = " FC2-PPV-4735332 撮影に協*してくれた小柄な新卒OLをおもちゃ責めしちゃいました IMEDAMAFC2 ".strip()
    parts = tmp.split(' ')
    av_number, av_title = get_avnumber_title(parts)
    print(f"番号: {av_number}, 标题: {av_title}")

               
    