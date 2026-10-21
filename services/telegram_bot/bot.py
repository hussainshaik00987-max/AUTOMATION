"""Core Telegram Bot handlers and review workflow for Coldwire."""
import logging
import sys
from pathlib import Path
from typing import Optional, Tuple, Any

# Ensure discovery package is importable
DISCOVERY_PATH = Path(__file__).resolve().parent.parent / "discovery"
if str(DISCOVERY_PATH) not in sys.path:
    sys.path.insert(0, str(DISCOVERY_PATH))

import db
try:
    from telegram_bot.config import AUTHORIZED_USER_ID
    from telegram_bot.formatter import (
        format_lead_message,
        build_lead_keyboard,
        build_decision_keyboard,
    )
except ImportError:
    from .config import AUTHORIZED_USER_ID
    from .formatter import (
        format_lead_message,
        build_lead_keyboard,
        build_decision_keyboard,
    )

logger = logging.getLogger(__name__)

ACTION_TO_STATUS = {
    "approve": "approved",
    "reject": "rejected",
    "review_later": "review_later",
}


def get_conn(context: Any = None):
    """Retrieve an active SQLite connection from context or create one."""
    if context and hasattr(context, "bot_data") and "db_conn" in context.bot_data:
        return context.bot_data["db_conn"]
    return db.connect()


def check_authorization(update: Any) -> Tuple[bool, str]:
    """Check if the update originates from the authorized owner in a private chat.

    Returns: (is_authorized, reason)
    """
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)

    # For callback queries, chat can be in message
    if not chat and getattr(update, "callback_query", None):
        cb = update.callback_query
        chat = getattr(getattr(cb, "message", None), "chat", None)
        if not user:
            user = getattr(cb, "from_user", None)

    if not user:
        return False, "Missing user information"

    if user.id != AUTHORIZED_USER_ID:
        logger.warning("Unauthorized access attempt from user_id=%s", user.id)
        return False, "Unauthorized user"

    chat_type = getattr(chat, "type", None)
    if chat_type != "private":
        logger.warning("Rejected non-private chat access from chat_id=%s type=%s", getattr(chat, "id", None), chat_type)
        return False, "Lead review is strictly restricted to private chats"

    return True, "Authorized"


async def start_command(update: Any, context: Any = None) -> None:
    """Handle /start command - verify authorization and display welcome."""
    is_auth, reason = check_authorization(update)
    if not is_auth:
        if update.message:
            await update.message.reply_text("⛔ Access denied. This bot is private to the authorized owner.")
        return

    welcome_text = (
        "👋 *Welcome to Coldwire Lead Review Bot*\n\n"
        "You are authenticated as the authorized owner.\n"
        "This bot delivers verified local business leads (Tirupati & Kadapa) for your manual review and approval.\n\n"
        "*Commands:*\n"
        "• `/pending` — View summary of pending leads\n"
        "• `/send_pending` — Send unnotified leads to this chat for review\n"
        "• `/retry_failed` — Reset failed sends and retry\n"
        "• `/help` — Show instructions and workflow rules"
    )
    if update.message:
        await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def help_command(update: Any, context: Any = None) -> None:
    """Handle /help command - display usage guidance and policies."""
    is_auth, _ = check_authorization(update)
    if not is_auth:
        if update.message:
            await update.message.reply_text("⛔ Access denied.")
        return

    help_text = (
        "📖 *Coldwire Review Bot Instructions*\n\n"
        "*Lead Review Workflow:*\n"
        "1. Run `/send_pending` to receive new business leads.\n"
        "2. Review each business's online presence, score, and verified opportunity evidence.\n"
        "3. Choose an action via the inline buttons:\n"
        "   • *Approve* — Qualifies lead for future approved outreach.\n"
        "   • *Reject* — Excludes lead permanently from outreach.\n"
        "   • *Review Later* — Leaves lead in deferred pending state.\n\n"
        "*Approval Boundary:* Approval only records your decision in the database. "
        "It *never* triggers automatic messages, phone calls, or external contact.\n\n"
        "*Commands:*\n"
        "• `/pending` — Summary of leads awaiting review\n"
        "• `/send_pending` — Dispatch pending leads\n"
        "• `/retry_failed` — Reset any delivery failures\n"
        "• `/help` — Display this guide"
    )
    if update.message:
        await update.message.reply_text(help_text, parse_mode="Markdown")


async def pending_command(update: Any, context: Any = None) -> None:
    """Handle /pending command - show count of pending, unnotified, and failed leads."""
    is_auth, _ = check_authorization(update)
    if not is_auth:
        if update.message:
            await update.message.reply_text("⛔ Access denied.")
        return

    conn = get_conn(context)
    all_pending = db.get_pending_reviews(conn)
    unnotified = [r for r in all_pending if r["notification_state"] == "unnotified"]
    sent = [r for r in all_pending if r["notification_state"] == "sent"]
    failed = [r for r in all_pending if r["notification_state"] == "failed"]

    # Overall counts
    total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    approved_count = conn.execute("SELECT COUNT(*) FROM lead_reviews WHERE status='approved'").fetchone()[0]
    rejected_count = conn.execute("SELECT COUNT(*) FROM lead_reviews WHERE status='rejected'").fetchone()[0]
    later_count = conn.execute("SELECT COUNT(*) FROM lead_reviews WHERE status='review_later'").fetchone()[0]

    msg = (
        "📊 *Lead Pipeline Status*\n\n"
        f"• *Total Discovered Leads:* {total_leads}\n"
        f"• *Pending Reviews:* {len(all_pending)}\n"
        f"   └ Unnotified (ready to send): `{len(unnotified)}`\n"
        f"   └ Sent (in chat awaiting review): `{len(sent)}`\n"
        f"   └ Failed sends: `{len(failed)}`\n\n"
        "*Decisions Recorded:*\n"
        f"• ✅ Approved: `{approved_count}`\n"
        f"• ❌ Rejected: `{rejected_count}`\n"
        f"• ⏳ Review Later: `{later_count}`\n\n"
        "Use `/send_pending` to deliver unnotified leads."
    )
    if update.message:
        await update.message.reply_text(msg, parse_mode="Markdown")


async def send_pending_command(update: Any, context: Any = None) -> int:
    """Send eligible unnotified leads to the authorized chat.

    Updates notification state to 'sent' ONLY upon confirmed send.
    Returns the count of successfully sent leads.
    """
    is_auth, _ = check_authorization(update)
    if not is_auth:
        if update.message:
            await update.message.reply_text("⛔ Access denied.")
        return 0

    conn = get_conn(context)
    unnotified_leads = db.get_pending_reviews(conn, unnotified_only=True)

    if not unnotified_leads:
        if update.message:
            await update.message.reply_text("✅ No unnotified pending leads found. Check `/pending`.")
        return 0

    chat_id = update.effective_chat.id
    sent_count = 0
    fail_count = 0

    if update.message:
        await update.message.reply_text(f"🚀 Sending {len(unnotified_leads)} pending lead(s) for review...")

    bot = context.bot if context and hasattr(context, "bot") else None

    for lead_row in unnotified_leads:
        lead_dict = dict(lead_row)
        lead_id = lead_dict["lead_id"]
        text = format_lead_message(lead_dict)
        keyboard = build_lead_keyboard(lead_id)

        try:
            if bot:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="Markdown",
                    )
                except Exception:
                    # Markdown formatting fallback to avoid dropping leads with special chars
                    await bot.send_message(
                        chat_id=chat_id,
                        text=text.replace("*", "").replace("_", ""),
                        reply_markup=keyboard,
                    )
            # Only mark 'sent' after successful send confirmation
            db.set_notification_state(conn, lead_id, "sent")
            sent_count += 1
        except Exception as e:
            logger.error("Failed to send lead_id=%s to Telegram: %s", lead_id, e)
            db.set_notification_state(conn, lead_id, "failed")
            fail_count += 1

    summary = f"🏁 Finished: {sent_count} lead(s) sent successfully."
    if fail_count > 0:
        summary += f" ({fail_count} failed; retry with `/retry_failed`)."

    if update.message:
        await update.message.reply_text(summary)

    return sent_count


async def retry_failed_command(update: Any, context: Any = None) -> int:
    """Reset failed notifications back to unnotified and optionally re-send."""
    is_auth, _ = check_authorization(update)
    if not is_auth:
        if update.message:
            await update.message.reply_text("⛔ Access denied.")
        return 0

    conn = get_conn(context)
    reset_count = db.reset_failed_notifications(conn)

    if reset_count == 0:
        if update.message:
            await update.message.reply_text("ℹ️ No failed notifications found.")
        return 0

    if update.message:
        await update.message.reply_text(
            f"🔄 Reset {reset_count} failed lead notification(s) back to unnotified.\n"
            "Now sending them..."
        )

    return await send_pending_command(update, context)


async def handle_callback(update: Any, context: Any = None) -> None:
    """Handle inline button clicks: Approve, Reject, Review Later."""
    query = update.callback_query
    if not query:
        return

    is_auth, _ = check_authorization(update)
    if not is_auth:
        await query.answer("⛔ Access denied. Unauthorized.", show_alert=True)
        return

    data = query.data or ""
    if data.startswith("noop:"):
        await query.answer("Decision already recorded.")
        return

    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "review":
        await query.answer("Invalid action data.")
        return

    action, lead_id_str = parts[1], parts[2]
    target_status = ACTION_TO_STATUS.get(action)

    if not target_status:
        await query.answer("Unknown review action.")
        return

    try:
        lead_id = int(lead_id_str)
    except ValueError:
        await query.answer("Invalid lead ID.")
        return

    conn = get_conn(context)
    current_review = db.get_review(conn, lead_id)

    if not current_review:
        await query.answer("Lead review record not found.", show_alert=True)
        return

    # Check for repeated clicks / duplicate actions
    if current_review["status"] == target_status:
        await query.answer(f"Lead is already marked as {target_status.replace('_', ' ')}.")
        return

    # Persist decision
    user_id = query.from_user.id if hasattr(query, "from_user") else AUTHORIZED_USER_ID
    db.set_review_status(
        conn,
        lead_id,
        target_status,
        actor=f"telegram:{user_id}",
        note=f"Action: {action}",
    )

    # Acknowledge user click
    action_labels = {
        "approved": "Approved ✅",
        "rejected": "Rejected ❌",
        "review_later": "Deferred ⏳",
    }
    label = action_labels.get(target_status, target_status)
    await query.answer(f"Lead #{lead_id} {label}")

    # Update inline message buttons to reflect recorded decision
    new_keyboard = build_decision_keyboard(target_status)
    try:
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
    except Exception as e:
        logger.debug("Could not edit message reply markup: %s", e)
