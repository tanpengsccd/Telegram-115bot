# -*- coding: utf-8 -*-

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import init
import threading
from app.core.batch_task_retry import execute_batch_retry

scheduler = BlockingScheduler()

# 定义任务列表：统一任务队列的自动重试（每 2 分钟仅查询本地数据库，到期项才会调用 115 接口）
tasks = [
    {"id": "batch_task_retry", "func": execute_batch_retry, "interval": 2 * 60, "task_type": "interval"},
]

def run_scheduler():
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


def stop_all_scheduled_tasks():
    for task in tasks:
        job = scheduler.get_job(task['id'])
        if job:
            scheduler.remove_job(task['id'])
            init.logger.info(f"任务 {task['id']} 已停止")
        else:
            init.logger.info(f"任务 {task['id']} 不存在")


def start_scheduler_in_thread():
    thread = threading.Thread(target=run_scheduler)
    thread.daemon = True  # 设置为守护线程，主线程退出时自动结束
    thread.start()

