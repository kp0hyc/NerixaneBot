from modules.commands import *
from modules.root import *
from modules.social_rating import *
from modules.config import MyBotState

from datetime import time

from telethon import TelegramClient, events
from telethon.tl.types import (
    UpdateBotMessageReaction, 
)

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

def main():
    check_init_user_table()

    mc = TelegramClient('anon', API_ID, API_HASH)
    
    mc.start(bot_token=BOT_TOKEN)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .build()
    )
    app.add_handler(
        MessageHandler(filters.Chat(chat_id=ORIG_CHANNEL_ID) & ~filters.COMMAND, handle_cocksize)
    )

    app.add_handler(
        MessageHandler(filters.Chat(chat_id=GAMBLING_CHANNEL_ID) & ~filters.COMMAND, handle_gambling)
    )
    
    app.add_handler(CommandHandler("edit_weights", edit_weights_cmd))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.REPLY,
            edit_weights_reply
        ),
        group=1
    )
    app.add_handler(CommandHandler("notify",   subscribe))
    app.add_handler(CommandHandler("stop", unsubscribe))
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("start", warn_use_dm, filters=~filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("rating", show_rating))
    app.add_handler(CommandHandler("ban", ban_media))
    app.add_handler(CommandHandler("block", block_media))
    app.add_handler(CommandHandler("unban", unban_media))
    app.add_handler(CommandHandler("shutdown", shutdown_bot))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("ban_sc_user", ban_sc_user))
    app.add_handler(CommandHandler("unban_sc_user", unban_sc_user))
    app.add_handler(CommandHandler("sc", change_social_rating))
    app.add_handler(CommandHandler("bw", add_banword))
    app.add_handler(CommandHandler("remove_bw", remove_banword))
    app.add_handler(CommandHandler("start_bet", start_bet))
    app.add_handler(CommandHandler("close_bet", close_bet))
    app.add_handler(CommandHandler("finish_bet", finish_bet))
    app.add_handler(CommandHandler("slot", slot_command))
    app.add_handler(CommandHandler("stop_slot", stop_slot_command))
    app.add_handler(CommandHandler("resume_slot", resume_slot_command))
    app.add_handler(CommandHandler("add_helper", add_helper))
    app.add_handler(CommandHandler("remove_helper", remove_helper))
    app.add_handler(CommandHandler("ignore_bot", ignore_bot))
    app.add_handler(CommandHandler("stop_ignore_bot", stop_ignore_bot))
    
    app.add_handler(CallbackQueryHandler(stats_page_callback, pattern=r"^stats:(?:global|daily|social|social_global|cock|casino):\d+$"))
    app.add_handler(CallbackQueryHandler(follow_callback, pattern=r"^follow$"))

    @mc.on(events.MessageDeleted(chats=ORIG_CHANNEL_ID))
    async def on_deleted(event):
        print("delted in chat id: ", event.chat_id)
        for msg_id in event.deleted_ids:
            await delete_forwards(app.bot, ORIG_CHANNEL_ID, msg_id)
        
        MyBotState.save_forward_map()
    
    @mc.on(events.MessageEdited(chats=ORIG_CHANNEL_ID))
    async def on_edited(event):
        print("edited in chat id: ", event.chat_id)
        orig_id   = event.chat_id
        orig_msg  = event.message.id
        await edit_forwards(app.bot, event, orig_id, orig_msg)

    @mc.on(events.Raw)
    async def handler(event):
        if not isinstance(event, UpdateBotMessageReaction):
            return
        await on_message_reaction(mc, event)

    app.job_queue.run_repeating(
        persist_stats,
        interval=300,
        first=300 
    )
    
    app.job_queue.run_repeating(
        refresh_polls,
        interval=300,
        first=240
    )
    
    app.job_queue.run_daily(
        reset_daily,
        time=time(hour=0, minute=0, tzinfo=TYUMEN)
    )
    
    app.job_queue.run_daily(
        reset_monthly_social_rating,
        time=time(hour=0, minute=1, tzinfo=TYUMEN)
    )

    app.run_polling(
        timeout=30,
    )
    print("exiting")

if __name__ == "__main__":
    main()
