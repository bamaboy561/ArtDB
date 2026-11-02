"""The site with its Telegram bot and daily reports running from the start.

Streamlit runs app.py only when someone opens the site, so after every restart
the bot stayed silent until then. Started here, they run as soon as the server
does; app.py starts them too, and the second start is a no-op in this process.
"""
from __future__ import annotations

from pathlib import Path
import sys

from streamlit.web import cli

from telegram_bot import start_telegram_bot
from telegram_scheduler import start_telegram_scheduler


def main() -> None:
    start_telegram_scheduler()
    start_telegram_bot()
    sys.argv = ["streamlit", "run", str(Path(__file__).resolve().parent / "app.py"), *sys.argv[1:]]
    cli.main()


if __name__ == "__main__":
    main()
