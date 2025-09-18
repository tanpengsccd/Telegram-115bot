from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler
from pathlib import Path
import time
import os
import init

async def push2aria2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("push2aria2_"):
        # 检查是否是新的ID格式
        task_id = data[len("push2aria2_"):]
        save_path = ""
        if hasattr(init, 'pending_push_tasks') and task_id in init.pending_push_tasks:
            # 新格式：从全局存储中获取数据
            task_data = init.pending_push_tasks[task_id]
            save_path = task_data["path"]
            init.logger.info(f"推送任务ID: {task_id}, 文件路径: {save_path}")
            # 清理已使用的任务数据
            del init.pending_push_tasks[task_id]
        else:
            init.logger.warning("❌ 无效的任务ID或任务已过期。")
            await query.answer("❌ 无效的任务ID或任务已过期。", show_alert=True)
            return
        try:
            if not save_path:
                init.logger.warning("❌ 无效的文件路径，无法推送到Aria2。")
                await query.answer("❌ 无效的文件路径，无法推送到Aria2。", show_alert=True)
                return
            download_urls = init.openapi_115.get_file_download_url(save_path)
            init.logger.info(f"[{save_path}]目录发现{len(download_urls)}个文件需要下载")
            from app.utils.aria2 import download_by_url
            # 获取文件夹名作为下载目录
            path = Path(save_path)
            last_part = path.parts[-1] if path.parts[-1] else path.parts[-2]
            download_dir = os.path.join(init.bot_config.get("aria2", {}).get("download_path", ""), last_part)
            init.logger.info(f"推送到Aria2，下载目录: {download_dir}")
            all_pushed = True
            for download_url in download_urls:
                download = download_by_url(download_url, download_dir)
                if not download:
                    all_pushed = False
                    init.logger.error(f"推送到Aria2失败，下载链接: {download_url}")
                time.sleep(1)  # 避免短时间内添加过多任务
            
            # 尝试编辑消息，处理不同的消息类型
            device_name = init.bot_config.get('aria2', {}).get('device_name', 'Aria2') or 'Aria2'
            try:
                
                if all_pushed:
                    # 首先尝试编辑caption（适用于图片消息）
                    await query.edit_message_caption(caption=f"✅ [{last_part}]已推送至{device_name}！")
                else:
                    await query.edit_message_caption(caption=f"❌ [{last_part}]推送到{device_name}失败，请检查配置或稍后再试。")
            except Exception:
                try:
                    # 如果编辑caption失败，尝试编辑文本（适用于纯文本消息）
                    if download:
                        await query.edit_message_text(f"✅ [{last_part}]已推送至{device_name}！")
                    else:
                        await query.edit_message_text(f"❌ [{last_part}]推送到{device_name}失败，请检查配置或稍后再试。")
                except Exception:
                    # 如果都失败，使用answer显示结果
                    if download:
                        await query.answer(f"✅ [{last_part}]已推送至{device_name}！", show_alert=True)
                    else:
                        await query.answer(f"❌ [{last_part}]推送到{device_name}失败，请检查配置或稍后再试。", show_alert=True)
                
        except Exception as e:
            init.logger.error(f"推送到{device_name}失败: {e}")
            try:
                await query.edit_message_caption(caption=f"❌ [{last_part if 'last_part' in locals() else '文件'}]推送到{device_name}失败: {str(e)}")
            except Exception:
                try:
                    await query.edit_message_text(f"❌ [{last_part if 'last_part' in locals() else '文件'}]推送到{device_name}失败: {str(e)}")
                except Exception:
                    await query.answer(f"❌ 推送到{device_name}失败: {str(e)}", show_alert=True)



def register_aria2_handlers(application):
    aria2_handler = CallbackQueryHandler(push2aria2, pattern=r"^push2aria2_.+")
    application.add_handler(aria2_handler)