# -*- coding: utf-8 -*-

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import init
import threading
from message_queue import add_task_to_queue
from subscribe import schedule_actor, schedule_number
from subscribe_movie import schedule_movie
from apscheduler.triggers.interval import IntervalTrigger

scheduler = BlockingScheduler()
    

# 定义任务列表
tasks = [
    # {"id": "actor_task", "func": schedule_actor, "hour": 1, "minute": 0},
    # {"id": "number_task", "func": schedule_number, "hour": 3, "minute": 0},
    # {"id": "cookie_check_task", "func": check_cookie, "interval": 4 * 60 * 60},
    {"id": "subscribe_movie_task", "func": schedule_movie, "interval": 4 * 60 * 60},
]

def subscribe_scheduler():
    for task in tasks:
        if not scheduler.get_job(task["id"]):
            if "cookie_check" in task["id"] or "subscribe_movie" in task["id"]:
                scheduler.add_job(
                    task["func"],
                    IntervalTrigger(seconds=task["interval"]),
                    id=task["id"],
                )
            else:
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

