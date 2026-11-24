# -*- coding: utf-8 -*-
"""Helpers for setting Telegram message reactions safely from async or worker threads."""

import asyncio
import init
from telegram import ReactionTypeEmoji

# Telegram Bot API officially supported reaction emojis
REACTION_PROCESSING = "👀"
REACTION_DONE = "👌"
REACTION_ERROR = "😢"


async def set_message_reaction_safe(bot, chat_id: int, message_id: int, emoji: str) -> bool:
    """Safely set a single emoji reaction on a Telegram message without raising."""
    if not bot or not chat_id or not message_id or not emoji:
        return False
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji)],
        )
        return True
    except Exception as e:
        if init.logger:
            init.logger.debug(f"设置消息表情回应失败 (chat={chat_id}, msg={message_id}, emoji={emoji}): {e}")
        return False


async def clear_message_reaction_safe(bot, chat_id: int, message_id: int) -> bool:
    """Safely remove the bot's reaction from a Telegram message without raising."""
    if not bot or not chat_id or not message_id:
        return False
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[],
        )
        return True
    except Exception as e:
        if init.logger:
            init.logger.debug(f"移除消息表情回应失败 (chat={chat_id}, msg={message_id}): {e}")
        return False


def schedule_message_reaction(chat_id: int, message_id: int, emoji: str) -> bool:
    """Schedule a reaction update from a synchronous worker thread via the queue loop."""
    if not chat_id or not message_id or not emoji:
        return False
    try:
        import app.utils.message_queue as mq
        loop = getattr(mq, "global_loop", None)
        bot = getattr(mq, "global_bot", None)
        if loop is None or bot is None:
            return False
        asyncio.run_coroutine_threadsafe(
            set_message_reaction_safe(bot, chat_id, message_id, emoji),
            loop,
        )
        return True
    except Exception as e:
        if init.logger:
            init.logger.debug(f"调度消息表情回应失败: {e}")
        return False


def schedule_reactions_batch(targets, emoji: str) -> None:
    """Apply the same reaction emoji to a list of (chat_id, message_id) tuples."""
    if not targets:
        return
    seen = set()
    for item in targets:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        chat_id, message_id = item
        key = (chat_id, message_id)
        if key in seen:
            continue
        seen.add(key)
        schedule_message_reaction(chat_id, message_id, emoji)
