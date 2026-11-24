# -*- coding: utf-8 -*-

"""
任务自动重试模块（2 倍指数退避）

规则：
- 对话内第 1 次尝试最多等待到「10 分钟无进度」后转入后台；
- 之后每次重新添加磁力离线任务，等待时间都是上一次等待时间的 2 倍（20分→40分→80分→…）；
- 当等待时间翻倍达到「重试最长等待上限」（面板可配置，默认 24 小时，可选 48h/96h/192h/384h=16天）
  并且这一轮仍未完成时，标记为失败（☠️）并引用原消息通知用户。
"""

import os
import time
from datetime import datetime
from typing import Dict, List

import init
from telegram.helpers import escape_markdown
from app.utils.message_queue import add_task_to_queue, send_document_via_bot
from app.utils.task_tracker import format_retry_task_name
from app.utils.bot_settings import (
    BASE_RETRY_WAIT_SECONDS,
    get_retry_max_wait_seconds,
    format_retry_cap_label,
    format_wait_duration,
)
from app.core.batch_task_manager import (
    get_pending_retry_tasks,
    schedule_retry_attempt,
    defer_retry_attempt,
    mark_task_as_dead,
    update_task_item_status,
    update_task_group_counts,
    get_batch_task_group_info,
    get_batch_task_items,
    get_group_actual_path,
    get_group_display_name,
    summarize_group_items,
)

QUICK_PROBE_SECONDS = 10          # 重新提交后快速探测一次，秒传任务可立即判定成功
SUBMIT_FAIL_DEFER_SECONDS = 600   # 提交失败（配额/网络）时 10 分钟后再试，不计入尝试次数


def compute_next_wait(current_wait, cap_seconds: int) -> int:
    """下一轮等待时间 = 上一轮 × 2，且不超过上限。"""
    base = max(int(current_wait or BASE_RETRY_WAIT_SECONDS), BASE_RETRY_WAIT_SECONDS)
    return int(min(base * 2, cap_seconds))


def _is_cloud_done(cloud_task) -> bool:
    return bool(cloud_task) and (
        cloud_task.get('status') == 2 or (cloud_task.get('percentDone') or 0) >= 100
    )


def _cloud_map(magnets: List[str]) -> Dict[str, Dict]:
    if not magnets or init.openapi_115 is None:
        return {}
    try:
        cloud_list = init.openapi_115.get_offline_tasks_for_links(magnets, use_cache=False) or []
    except Exception as e:
        init.logger.warn(f"⚠️ 获取离线任务状态失败: {e}")
        return {}
    return {t.get('url'): t for t in cloud_list if isinstance(t, dict) and t.get('url')}


def _safe_del_cloud(cloud_task, delete_source: bool) -> None:
    info_hash = (cloud_task or {}).get('info_hash')
    if not info_hash:
        return
    try:
        init.openapi_115.del_faild_offline_task(info_hash, delete_source_file=delete_source)
    except Exception as e:
        init.logger.warn(f"清理离线任务记录失败: {e}")


def retry_submit_to_115(tasks: List[Dict], save_path: str) -> bool:
    """将重试任务重新提交到 115 离线下载"""
    try:
        quota_info = init.openapi_115.get_quota_info()
        if isinstance(quota_info, dict) and 'count' in quota_info and 'used' in quota_info:
            left_offline_quota = quota_info['count'] - quota_info['used']
            if left_offline_quota < len(tasks):
                init.logger.warn(f"⚠️ 离线配额不足: 剩余 {left_offline_quota}，需要 {len(tasks)}")
                return False

        offline_urls = "\n".join([task['magnet'] for task in tasks])
        offline_success = init.openapi_115.offline_download_specify_path(offline_urls, save_path)
        if offline_success:
            init.logger.info(f"✅ 成功重新提交 {len(tasks)} 个重试任务到115")
            return True
        init.logger.error("❌ 提交重试任务到115失败")
        return False
    except Exception as e:
        init.logger.error(f"❌ 提交重试任务异常: {e}")
        return False


def _process_retry_group(group_id: str, tasks: List[Dict], *, manual: bool = False) -> Dict:
    """
    处理一个任务组中到期的重试项：
    1. 先检查上一轮提交是否已完成 → 成功则标记完成；
    2. 未完成且本轮等待已达上限 → 标记失败；
    3. 其余重新提交磁力，并把等待时间翻倍（手动立即重试不翻倍）。
    """
    group_info = get_batch_task_group_info(group_id)
    if not group_info:
        return {"ok": False, "submitted": 0, "newly_succeeded": 0, "newly_dead": 0, "remaining": 0,
                "message": "未找到该任务组记录"}

    actual_path = get_group_actual_path(group_info)
    cap = get_retry_max_wait_seconds()
    cloud = _cloud_map([t['magnet'] for t in tasks if t.get('magnet')])

    newly_succeeded = 0
    newly_dead = 0
    to_resubmit: List[Dict] = []

    for task in tasks:
        magnet = task.get('magnet')
        cloud_task = cloud.get(magnet)
        if _is_cloud_done(cloud_task):
            update_task_item_status(magnet, group_id, 'success', cloud_task.get('name') or task.get('resource_name') or '')
            _safe_del_cloud(cloud_task, False)
            newly_succeeded += 1
            continue

        current_wait = int(task.get('current_wait') or BASE_RETRY_WAIT_SECONDS)
        if not manual and task.get('last_retry_at') and current_wait >= cap:
            mark_task_as_dead(task['id'])
            _safe_del_cloud(cloud_task, True)
            newly_dead += 1
            continue

        if cloud_task:
            _safe_del_cloud(cloud_task, True)  # 删除上一轮未完成的残留任务，避免重复提交冲突
        to_resubmit.append(task)

    submitted = 0
    if to_resubmit:
        if retry_submit_to_115(to_resubmit, actual_path):
            now = int(time.time())
            for task in to_resubmit:
                current_wait = int(task.get('current_wait') or BASE_RETRY_WAIT_SECONDS)
                if manual:
                    new_wait = min(max(current_wait, BASE_RETRY_WAIT_SECONDS), cap)
                else:
                    new_wait = compute_next_wait(current_wait, cap)
                schedule_retry_attempt(task['id'], int(task.get('retry_count') or 0) + 1, new_wait, now)
                init.logger.info(
                    f"🔁 任务组 {group_id} 第 {int(task.get('retry_count') or 0) + 2} 次尝试已提交，"
                    f"等待 {format_wait_duration(new_wait)} 后检查"
                )
            submitted = len(to_resubmit)

            # 快速探测：秒传/热种资源可立即判定成功
            time.sleep(QUICK_PROBE_SECONDS)
            probe = _cloud_map([t['magnet'] for t in to_resubmit])
            for task in to_resubmit:
                cloud_task = probe.get(task['magnet'])
                if _is_cloud_done(cloud_task):
                    update_task_item_status(task['magnet'], group_id, 'success', cloud_task.get('name') or '')
                    _safe_del_cloud(cloud_task, False)
                    newly_succeeded += 1
        else:
            for task in to_resubmit:
                defer_retry_attempt(task['id'], SUBMIT_FAIL_DEFER_SECONDS)
            init.logger.warn(f"⚠️ 任务组 {group_id} 提交115失败，{SUBMIT_FAIL_DEFER_SECONDS // 60} 分钟后再试")

    update_task_group_counts(group_id)
    send_retry_summary(group_id, group_info.get('folder_name') or "", newly_succeeded=newly_succeeded, newly_dead=newly_dead)

    summary = summarize_group_items(get_batch_task_items(group_id))
    remaining = summary["failed"] + summary["dead"]
    ok = bool(submitted) or newly_succeeded > 0 or newly_dead > 0 or not to_resubmit
    return {
        "ok": ok,
        "submitted": submitted,
        "newly_succeeded": newly_succeeded,
        "newly_dead": newly_dead,
        "remaining": remaining,
        "message": (
            f"本次重新提交 {submitted} 条，成功补全 {newly_succeeded} 条，剩余未完成 {remaining} 条"
            if ok else "提交 115 离线任务失败（可能是配额不足或授权异常），10 分钟后自动再试"
        ),
    }


def execute_batch_retry():
    """调度器定期调用：只处理到期（next_retry_at <= now）的重试项，未到期不会调用 115 接口。"""
    retry_tasks = get_pending_retry_tasks()
    if not retry_tasks:
        init.logger.debug("✨ 当前没有到期的重试任务")
        return

    grouped: Dict[str, List[Dict]] = {}
    for task in retry_tasks:
        grouped.setdefault(task['group_id'], []).append(task)

    init.logger.info(f"🔄 本轮有 {len(retry_tasks)} 条到期重试任务，涉及 {len(grouped)} 个任务组")
    for group_id, tasks in grouped.items():
        try:
            _process_retry_group(group_id, tasks)
        except Exception as e:
            init.logger.error(f"❌ 处理任务组 {group_id} 重试失败: {e}")
    init.logger.info("✅ 本轮到期重试处理完成")


def retry_batch_group_now(group_id: str) -> Dict:
    """面板手动「立即重试本组」：对所有未完成（待重试 / 已失败）的磁力立即重新提交。"""
    items = [it for it in get_batch_task_items(group_id) if it.get('status') in ('failed', 'dead', 'pending')]
    if not items:
        return {"ok": True, "submitted": 0, "newly_succeeded": 0, "newly_dead": 0, "remaining": 0,
                "message": "该任务组没有需要重试的未完成项"}
    return _process_retry_group(group_id, items, manual=True)


def send_retry_summary(group_id: str, folder_name: str, newly_succeeded: int = 0, newly_dead: int = 0):
    """
    发送重试结果通知（全部引用原消息）：
    - 全部补全 → 完成通知；
    - 仍有待重试 → 仅在本轮有新增补全时发送进展；
    - 已无待重试但存在失败项（本轮刚结束）→ 发送最终失败通知。
    """
    try:
        group_info = get_batch_task_group_info(group_id)
        if not group_info:
            init.logger.warn(f"任务组 {group_id} 信息不存在，无法发送总结")
            return

        items = get_batch_task_items(group_id)
        summary = summarize_group_items(items)
        total_count = max(int(group_info.get('total_count') or 0), len(items))
        success_count = summary["success"]
        pending_retry = summary["failed"]
        dead_count = summary["dead"]
        actual_path = get_group_actual_path(group_info)
        target_chat = group_info.get('chat_id') or init.get_primary_user()
        origin_msg_id = group_info.get('origin_message_id')
        display_name = get_group_display_name(group_info, items)
        safe_path = escape_markdown(str(actual_path or ""), version=2)

        # 情况1：全部补全
        if pending_retry == 0 and dead_count == 0 and success_count >= total_count:
            header = escape_markdown(
                f"✅ 后台自动重试完成！任务「{display_name}」已全部补全（{success_count}/{total_count}）",
                version=2,
            )
            add_task_to_queue(target_chat, None, f"{header}\n\n📁 保存路径: `{safe_path}`", reply_to_message_id=origin_msg_id)
            init.logger.info(f"✅ 已发送后台重试全部补全通知（group={group_id}, reply={origin_msg_id}）")
            return

        # 情况2：仍有待重试项
        if pending_retry > 0:
            if newly_succeeded > 0:
                next_hint = ""
                if summary["next_retry_at"]:
                    next_hint = f"，下次检查约 {format_wait_duration(summary['next_retry_at'] - int(time.time()))}后"
                header = escape_markdown(
                    f"🔄 后台重试进展（{display_name}）：本次补全 {newly_succeeded} 条，累计完成 {success_count}/{total_count}，"
                    f"剩余 {pending_retry} 条将继续自动重试（第 {summary['attempt']} 次尝试{next_hint}）",
                    version=2,
                )
                add_task_to_queue(target_chat, None, f"{header}\n\n📁 保存路径: `{safe_path}`", reply_to_message_id=origin_msg_id)
                init.logger.info(f"✅ 已发送后台重试进展通知（group={group_id}, newly_succeeded={newly_succeeded}）")
            else:
                init.logger.info(f"任务组 {group_id} 仍有 {pending_retry} 条待重试且本轮无新增成功，跳过通知")
            return

        # 情况3：重试结束但存在失败项（仅在本轮刚结束时通知一次）
        if dead_count > 0 and (newly_dead > 0 or newly_succeeded > 0):
            cap_label = format_retry_cap_label()
            dead_links = [it.get('magnet', '') for it in items if it.get('status') == 'dead' and it.get('magnet')]
            header = escape_markdown(
                f"⛔ 任务「{display_name}」自动重试结束：成功 {success_count}/{total_count}，"
                f"{dead_count} 条等待已达上限（{cap_label}）仍未完成，已标记失败",
                version=2,
            )
            preview = "\n".join(f"`{escape_markdown(link[:200], version=2)}`" for link in dead_links[:5])
            more = escape_markdown(f"\n…另有 {len(dead_links) - 5} 条，详见附件", version=2) if len(dead_links) > 5 else ""
            add_task_to_queue(
                target_chat,
                None,
                f"{header}\n\n📁 保存路径: `{safe_path}`\n\n{preview}{more}",
                reply_to_message_id=origin_msg_id,
            )
            if len(dead_links) > 5:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_path = os.path.join(init.TEMP if getattr(init, "TEMP", None) else "/tmp", f"failed_{group_id}_{timestamp}.txt")
                try:
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write("\n".join(dead_links) + "\n")
                    send_document_via_bot(
                        chat_id=target_chat,
                        file_path=file_path,
                        caption=f"⛔ {display_name} 失败磁力列表（{len(dead_links)} 条）"[:1024],
                        reply_to_message_id=origin_msg_id,
                    )
                finally:
                    if os.path.exists(file_path):
                        os.remove(file_path)
            init.logger.info(f"⛔ 已发送任务组 {group_id} 最终失败通知")
    except Exception as e:
        init.logger.error(f"❌ 发送重试总结消息失败: {e}")


if __name__ == '__main__':
    init.load_yaml_config()
    init.create_logger()
    execute_batch_retry()
