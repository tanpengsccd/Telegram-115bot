
import init
import time
import os
from app.utils.sqlitelib import *
from app.utils.message_queue import add_task_to_queue
from telegram.helpers import escape_markdown
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def offline_task_retry():
    init.logger.info("开始涩花离线任务...")
    sehua_offline()
    init.logger.info("开始AV日更离线任务...")
    av_daily_offline()


def sehua_offline():
    save_path_list= []
    check_results = []
    sections = init.bot_config.get('sehua_spider', {}).get('sections', [])
    for section in sections:
        section_name = section.get('name', '')
        save_path = section.get('save_path', f'/AV/涩花/{section_name}')
        sql = "select * from sehua_data WHERE is_download=0 and section_name=? order by publish_date desc"
        with SqlLiteLib() as sqlite:
            results = sqlite.query_all(sql, (section_name,))
            if not results:
                init.logger.info(f"[涩花][{section_name}]板块，没有找到需要离线任务~")
                continue
            init.logger.info(f"[涩花][{section_name}]板块，找到 {len(results)} 个需要离线的任务")
            check_results.extend(results)
            # 分批处理，每100个任务一批
            offline_groups = create_offline_group_by_save_path(results)
            if offline_groups:
                for save_path, batches in offline_groups.items():
                    if save_path not in save_path_list:
                        save_path_list.append(save_path)
                    for batch_tasks in batches:
                        task_count = len(batch_tasks.split('\n')) 
                        offline2115(batch_tasks, task_count, save_path)
            else:
                init.logger.warn("涩花离线任务未执行，可能是115离线配额不足，请检查115账号状态！")
                add_task_to_queue(init.get_primary_user(), f"{init.IMAGE_PATH}/male023.png", "涩花离线任务未执行，可能是115离线配额不足，请检查115账号状态！")
                return

    # 等待离线完成
    time.sleep(300)
    domestic_original_count = 0
    domestic_original_success = 0
    asia_censored_count = 0
    asia_censored_success = 0
    asia_uncensored_count = 0
    asia_uncensored_success = 0
    hd_subtitle_count = 0
    hd_subtitle_success = 0
    
    # 创建一个共享的成功计数列表
    success_counters = [0, 0, 0, 0]  # [国产原创, 亚洲有码原创, 亚洲无码原创, 高清中文字幕]
    
    # 获取离线任务状态
    offline_task_status = init.openapi_115.get_offline_tasks()
    images = []
    for item in check_results:
        section_name = item['section_name']
        magnet = item['magnet']
        if section_name == '国产原创':
            domestic_original_count += 1
        elif section_name == '亚洲有码原创':
            asia_censored_count += 1
        elif section_name == '亚洲无码原创':
            asia_uncensored_count += 1
        elif section_name == '高清中文字幕':
            hd_subtitle_count += 1
        save_path = item['save_path']
        for task in offline_task_status:
            if task['url'] == magnet:
                if task['status'] == 2 and task['percentDone'] == 100:
                    sehua_success_proccesser(item, save_path, task, success_counters)
                    images.append(item['image_path'])
                else:
                    init.logger.warn(f"{item['title']} 离线下载失败或未完成。")
                    # 删除离线失败的文件
                    init.openapi_115.del_faild_offline_task(task['info_hash'])
                break
            
    # 等待消息队列处理完成，避免在消息发送期间删除图片文件
    from app.utils.message_queue import message_queue
    init.logger.info("等待涩花通知发送完成...")
    while not message_queue.empty():
        init.logger.debug(f"消息队列还有 {message_queue.qsize()} 个任务待处理，等待中...")
        time.sleep(5)  # 每5秒检查一次
    init.logger.info("所有通知已发送完成，开始清理流程")

    # 从共享列表中获取最终的成功计数
    domestic_original_success = success_counters[0]
    asia_censored_success = success_counters[1]
    asia_uncensored_success = success_counters[2] 
    hd_subtitle_success = success_counters[3]
    
    # 收集有任务的类别信息
    messages = []
    if domestic_original_count > 0:
        message_line = escape_markdown(f"[国产原创]离线任务完成情况: {domestic_original_success}/{domestic_original_count}", version=2)
        messages.append(message_line)
        init.logger.info(f"[国产原创]离线任务完成情况: {domestic_original_success}/{domestic_original_count}")
    
    if asia_censored_count > 0:
        message_line = escape_markdown(f"[亚洲有码原创]离线任务完成情况: {asia_censored_success}/{asia_censored_count}", version=2)
        messages.append(message_line)
        init.logger.info(f"[亚洲有码原创]离线任务完成情况: {asia_censored_success}/{asia_censored_count}")
    
    if asia_uncensored_count > 0:
        message_line = escape_markdown(f"[亚洲无码原创]离线任务完成情况: {asia_uncensored_success}/{asia_uncensored_count}", version=2)
        messages.append(message_line)
        init.logger.info(f"[亚洲无码原创]离线任务完成情况: {asia_uncensored_success}/{asia_uncensored_count}")
    
    if hd_subtitle_count > 0:
        message_line = escape_markdown(f"[高清中文字幕]离线任务完成情况: {hd_subtitle_success}/{hd_subtitle_count}", version=2)
        messages.append(message_line)
        init.logger.info(f"[高清中文字幕]离线任务完成情况: {hd_subtitle_success}/{hd_subtitle_count}")

    # 只有当有任务时才发送消息
    if messages:
        final_message = "**涩花离线任务完成情况:**\n" + "\n".join(messages)
        add_task_to_queue(init.get_primary_user(), f"{init.IMAGE_PATH}/sehua_daily_update.png", final_message)
    
    # 删除垃圾文件
    for path in save_path_list:
        init.openapi_115.auto_clean_all(path)
        time.sleep(10)
    # 清空离线任务
    init.openapi_115.clear_cloud_task()
    # 删除临时文件
    del_images(images)
    
    
def del_images(images):
    if not images:
        return
    for image_path in images:
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
                init.logger.debug(f"已删除临时图片文件: {image_path}")
            except Exception as e:
                init.logger.warn(f"删除临时图片文件失败: {image_path}, 错误: {e}")
    init.logger.info("所有临时图片文件已删除!")
    
                
def sehua_success_proccesser(item, save_path, task, success_list):
    id = item['id']
    section_name = item['section_name']
    av_number = item['av_number']
    title = item['title']
    movie_type = item['movie_type']
    size = item['size']
    magnet = item['magnet']
    post_url = item['post_url']
    publish_date = item['publish_date']
    pub_url = item['pub_url']
    image_path = item['image_path']

    # 更新数据库状态
    with SqlLiteLib() as sqlite:
        sql_update = "UPDATE sehua_data SET is_download=1 WHERE id=?"
        params_update = (id,)
        sqlite.execute_sql(sql_update, params_update)
    
    init.logger.info(f"{title} 离线下载成功！")
    
    # 更新成功计数器
    if section_name == '国产原创':
        success_list[0] += 1
    elif section_name == '亚洲有码原创':
        success_list[1] += 1
    elif section_name == '亚洲无码原创':
        success_list[2] += 1
    elif section_name == '高清中文字幕':
        success_list[3] += 1
    
    # 发送通知
    if init.bot_config.get('sehua_spider', {}).get('notify_me', False):
            msg_av_number = escape_markdown(f"#{av_number}", version=2)
            msg_title = escape_markdown(title, version=2)
            msg_date = escape_markdown(publish_date, version=2)
            msg_size = escape_markdown(size, version=2)
            msg_section = escape_markdown(section_name, version=2)
            msg_movie_type = escape_markdown(movie_type, version=2)
            if section_name == '国产原创':
                message = f"""
**涩花爬取通知**

**版块:**   {msg_section}
**标题:**   `{msg_title}`
**类型:**   {msg_movie_type}
**大小:**   {msg_size}
**发布日期:** {msg_date}
**下载链接:** `{magnet}`
**发布链接:** [点击查看详情]({pub_url})
            """
            else:
                message = f"""
**涩花爬取通知**

**版块:**    {msg_section}
**番号:**   `{msg_av_number.upper()}`
**标题:**   `{msg_title}`
**类型:**    {msg_movie_type}
**大小:**    {msg_size}
**发布日期:** {msg_date}
**下载链接:** `{magnet}`
**发布链接:** [点击查看详情]({pub_url})
                """
            if not init.aria2_client:
                add_task_to_queue(init.get_primary_user(), image_path, message)
            else:
                push2aria2(f"{save_path}/{task['name']}", init.get_primary_user(), image_path, message)
            


def av_daily_offline():
    update_list = []
    # 找到需要下载的AV
    with SqlLiteLib() as sqlite:
        sql = "SELECT av_number, magnet, publish_date, title, post_url, pub_url, id FROM av_daily_update WHERE is_download=0 ORDER BY publish_date DESC"
        need_offline_av = sqlite.query(sql)
        if not need_offline_av:
            init.logger.info("没有需要离线下载的日更")
            return
        for row in need_offline_av:
            update_list.append({
                "av_number": row[0],
                "magnet": row[1],
                "publish_date": row[2],
                "title": row[3],
                "post_url": row[4],
                "pub_url": row[5],
                "id": row[6],
                "success": False  # 初始状态为未成功
            })
    
    # 分批处理，每100个任务一批
    create_offline_url_list = create_offline_url(update_list)
    if create_offline_url_list:
        for offline_tasks in create_offline_url_list:
            # 离线到115
            offline2115(offline_tasks, len(update_list), init.bot_config.get('av_daily_update', {}).get('save_path', '/AV/日更'))
    else:
        init.logger.warn("AV日更离线任务未执行，可能是115离线配额不足，请检查115账号状态！")
        add_task_to_queue(init.get_primary_user(), f"{init.IMAGE_PATH}/male023.png", "AV日更离线任务未执行，可能是115离线配额不足，请检查115账号状态！")
        return
    
    # 等待离线完成
    time.sleep(300)
    # 获取离线任务状态
    offline_task_status = init.openapi_115.get_offline_tasks()
    # 检查离线下载状态     
    for item in update_list:
        for task in offline_task_status:
            if task['url'] == item['magnet']:
                if task['status'] == 2 and task['percentDone'] == 100:
                    av_daily_success_proccesser(item, task)
                    item['success'] = True
                else:
                    init.logger.warn(f"{item['av_number']} 离线下载失败或未完成。")
                    # 删除离线失败的文件
                    init.openapi_115.del_faild_offline_task(task['info_hash'])
                break
            
    # 等待消息队列处理完成，避免在消息发送期间进行清理操作
    from app.utils.message_queue import message_queue
    init.logger.info("等待AV日更通知发送完成...")
    while not message_queue.empty():
        init.logger.debug(f"消息队列还有 {message_queue.qsize()} 个任务待处理，等待中...")
        time.sleep(5)  # 每5秒检查一次
    init.logger.info("所有通知已发送完成，开始清理流程")
            
    # 发送总结消息
    total_count = len(update_list)
    success_count = sum(1 for item in update_list if item['success'])
    message = f"本次AV日更结束！总计离线：{total_count}， 成功：{success_count}， 失败：{total_count - success_count}"
    init.logger.info(message) 
    if total_count != success_count:
        init.logger.info("失败的任务会在下次自动重试，请检查日志。")
        message += "\n失败的任务会在下次自动重试，请留意日志或通知！"

    add_task_to_queue(init.get_primary_user(), f"{init.IMAGE_PATH}/av_daily_update.png", message)
    
    # 删除垃圾文件
    init.openapi_115.auto_clean_all(init.bot_config.get('av_daily_update', {}).get('save_path', '/AV/日更'))
    # 清空离线任务
    init.openapi_115.clear_cloud_task()
    
    
def av_daily_success_proccesser(item, task):
    save_path = init.bot_config.get('av_daily_update', {}).get('save_path', '/AV/日更')
    
    # 更新数据库状态
    with SqlLiteLib() as sqlite:
        sql_update = "UPDATE av_daily_update SET is_download=1 WHERE id=?"
        params_update = (item['id'],)
        sqlite.execute_sql(sql_update, params_update)
    
    init.logger.info(f"{item['av_number'].upper()} 离线下载完成！")
    
    # 发送通知
    if init.bot_config.get('av_daily_update', {}).get('notify_me', False):
        msg_av_number = escape_markdown(f"#{item['av_number'].upper()}", version=2)
        msg_title = escape_markdown(item['title'], version=2)
        msg_date = escape_markdown(item['publish_date'], version=2)
        msg_magnet = escape_markdown(item['magnet'], version=2)
        pub_url = escape_markdown(item['pub_url'], version=2)
        message = f"""
**AV日更通知**

**番号:**   `{msg_av_number}`
**标题:**   `{msg_title}`
**发布日期:** {msg_date}
**下载链接:** `{msg_magnet}`
**发布链接:** [点击查看详情]({pub_url})
"""     
        if not init.aria2_client:
            add_task_to_queue(init.get_primary_user(), item['post_url'], message)
        else:
            push2aria2(f"{save_path}/{task['name']}", init.get_primary_user(), item['post_url'], message)


def offline2115(offline_tasks, task_count, save_path):
    
    # 调用115的离线下载API
    offline_success = init.openapi_115.offline_download_specify_path(
        offline_tasks,
        save_path)
    if not offline_success: 
        init.logger.error(f"{task_count}个离线任务添加离线失败!")
    else:
        init.logger.info(f"{task_count}个离线任务添加离线成功!")

    time.sleep(2)

def create_offline_url(res_list):
    offline_tasks = ""
    offline_tasks_list = []
    index = 0
    quota_info = init.openapi_115.get_quota_info()
    left_offline_quota = quota_info['count'] - quota_info['used']
    # 离线配额不足
    if left_offline_quota < len(res_list):
        return None
    for item in res_list:
        if not item['magnet']:
            init.logger.warn(f"跳过无效的离线任务，标题: {item['title']}，下载链接为空")
            continue
        offline_tasks += item['magnet'] + "\n"
        index += 1
        if index == 100:
            offline_tasks_list.append(offline_tasks[:-1])  # 去掉最后的换行符
            offline_tasks = ""
            index = 0
    if offline_tasks:
        offline_tasks_list.append(offline_tasks[:-1])  # 去掉最后的换行符
    return offline_tasks_list


def create_offline_group_by_save_path(res_list):
    """
    根据保存路径分组离线任务，每个路径下的任务不超过100个
    """
    quota_info = init.openapi_115.get_quota_info()
    left_offline_quota = quota_info['count'] - quota_info['used']
    
    # 离线配额不足
    if left_offline_quota < len(res_list):
        return None
    
    # 按保存路径分组
    path_groups = {}
    for item in res_list:
        if not item.get('magnet'):
            init.logger.warn(f"跳过无效的离线任务，标题: {item.get('title', 'Unknown')}，下载链接为空")
            continue
            
        save_path = item.get('save_path')
        if save_path not in path_groups:
            path_groups[save_path] = []
        path_groups[save_path].append(item['magnet'])
    
    # 每个路径下的任务分批处理，每批最多100个
    result = {}
    for save_path, magnets in path_groups.items():
        batches = []
        current_batch = ""
        count = 0
        
        for magnet in magnets:
            current_batch += magnet + "\n"
            count += 1
            
            if count == 100:
                batches.append(current_batch[:-1])  # 去掉最后的换行符
                current_batch = ""
                count = 0
        
        # 添加剩余的任务
        if current_batch:
            batches.append(current_batch[:-1])  # 去掉最后的换行符
            
        result[save_path] = batches
    
    return result

def push2aria2(save_path, user_id, cover_image, message):
    # 为Aria2推送创建任务ID系统
    import uuid
    push_task_id = str(uuid.uuid4())[:8]
    
    # 初始化pending_push_tasks（如果不存在）
    if not hasattr(init, 'pending_push_tasks'):
        init.pending_push_tasks = {}
    
    # 存储推送任务数据
    init.pending_push_tasks[push_task_id] = {
        'path': save_path
    }
    
    device_name = init.bot_config.get('aria2', {}).get('device_name', 'Aria2') or 'Aria2'
    
    keyboard = [
        [InlineKeyboardButton(f"推送到{device_name}", callback_data=f"push2aria2_{push_task_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    add_task_to_queue(user_id, cover_image, message, reply_markup)


if __name__ == '__main__':
    init.load_yaml_config()
    init.create_logger()
    av_daily_offline()