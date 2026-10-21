"""Entry point for Coldwire Telegram Lead Review Bot."""
import argparse
import logging
import sys
from pathlib import Path

# Set up paths
ROOT = Path(__file__).resolve().parent
SERVICES_DIR = ROOT.parent
PROJECT_ROOT = SERVICES_DIR.parent
DISCOVERY_PATH = SERVICES_DIR / "discovery"

for p in (str(SERVICES_DIR), str(DISCOVERY_PATH), str(PROJECT_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from telegram_bot.config import (
        TELEGRAM_BOT_TOKEN,
        AUTHORIZED_USER_ID,
        validate_config,
    )
    from telegram_bot.telegram_compat import (
        HAS_PTB,
        Application,
        CommandHandler,
        CallbackQueryHandler,
    )
    from telegram_bot.bot import (
        start_command,
        help_command,
        pending_command,
        send_pending_command,
        retry_failed_command,
        handle_callback,
    )
except ImportError:
    from .config import (
        TELEGRAM_BOT_TOKEN,
        AUTHORIZED_USER_ID,
        validate_config,
    )
    from .telegram_compat import (
        HAS_PTB,
        Application,
        CommandHandler,
        CallbackQueryHandler,
    )
    from .bot import (
        start_command,
        help_command,
        pending_command,
        send_pending_command,
        retry_failed_command,
        handle_callback,
    )
import db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("coldwire.telegram_bot")


def build_app(token: str, conn=None):
    """Build Application and register all command and callback handlers."""
    app = Application.builder().token(token).build()
    if conn is not None:
        if not hasattr(app, "bot_data"):
            app.bot_data = {}
        app.bot_data["db_conn"] = conn

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("send_pending", send_pending_command))
    app.add_handler(CommandHandler("retry_failed", retry_failed_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    return app


def main():
    parser = argparse.ArgumentParser(description="Coldwire Telegram Lead Review Bot")
    parser.add_argument(
        "--import-csv",
        type=str,
        help="Optional path to a CSV file to import leads before starting the bot",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and database without starting long-lived polling",
    )
    args = parser.parse_args()

    # 1. Validate configuration
    is_valid, msg = validate_config(strict=not args.dry_run)
    if not is_valid:
        logger.error("Configuration error: %s", msg)
        if not args.dry_run:
            sys.exit(1)

    logger.info("Configuration validated. Authorized User ID: %s", AUTHORIZED_USER_ID)

    # 2. Initialize database
    conn = db.init()
    logger.info("Database initialized successfully at %s", db.DB_PATH)

    # 3. Optional CSV import
    if args.import_csv:
        csv_path = Path(args.import_csv)
        logger.info("Importing leads from CSV: %s", csv_path)
        imported, new_reviews = db.import_leads_from_csv(conn, csv_path)
        logger.info("Imported %d lead(s), initialized %d review record(s)", imported, new_reviews)

    if args.dry_run:
        logger.info("Dry run complete. Exiting.")
        return

    if not HAS_PTB:
        logger.error(
            "python-telegram-bot is not installed. "
            "Please install it with: pip install 'python-telegram-bot>=21.0'"
        )
        sys.exit(1)

    # 4. Build application and start polling
    app = build_app(TELEGRAM_BOT_TOKEN, conn=conn)
    logger.info("Coldwire Telegram Bot is running. Waiting for private messages from user %s...", AUTHORIZED_USER_ID)
    app.run_polling()


if __name__ == "__main__":
    main()
