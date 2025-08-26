# -*- coding: utf-8 -*-

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import init
import threading
from subscribe_movie import schedule_movie
from apscheduler.triggers.interval import IntervalTrigger
from av_daily_update import av_daily_update, av_daily_retry, repair_leak
from offline_task_handler import try_to_offline2115_again

scheduler = BlockingScheduler()

# 定义任务列表
tasks = [
    {"id": "subscribe_movie_task", "func": schedule_movie, "interval": 4 * 60 * 60, "task_type": "interval"},
    {"id": "av_daily_update_task", "func": av_daily_update, "hour": 20, "minute": 00, "task_type": "time"},
    {"id": "av_daily_repair_task", "func": repair_leak, "hour": 23, "minute": 00, "task_type": "time"},
    {"id": "av_daily_retry_task", "func": av_daily_retry, "interval": 6 * 60 * 60, "task_type": "interval"},
    {"id": "retry_failed_downloads", "func": try_to_offline2115_again, "interval": 12 * 60 * 60, "task_type": "interval"}
    
]

def subscribe_scheduler():
    for task in tasks:
        if not scheduler.get_job(task["id"]):
            if task['task_type'] == 'interval':
                scheduler.add_job(
                    task["func"],
                    IntervalTrigger(seconds=task["interval"]),
                    id=task["id"],
                )
            if task['task_type'] == 'time':
                scheduler.add_job(
                    task["func"],
                    CronTrigger(hour=task["hour"], minute=task["minute"]),
                    id=task["id"],
                )
    # 确保调度器是启动状态
    if not scheduler.running:
        scheduler.start()


def stop_all_subscriptions():
    for task in tasks:
        job = scheduler.get_job(task['id'])
        if job:
            scheduler.remove_job(task['id'])
            init.logger.info(f"任务 {task['id']} 已停止")
        else:
            init.logger.info(f"任务 {task['id']} 不存在")




def start_scheduler_in_thread():
    thread = threading.Thread(target=subscribe_scheduler)
    thread.daemon = True  # 设置为守护线程，主线程退出时自动结束
    thread.start()

