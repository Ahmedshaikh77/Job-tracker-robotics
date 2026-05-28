"""Telegram alert sender."""
from __future__ import annotations
import os
import logging
import time
import requests
from .models import Job

log = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends job alerts to Telegram via Bot API."""

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            log.warning("Telegram disabled: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")

    def _send(self, text: str) -> bool:
        if not self.enabled:
            log.info("[DRY-RUN ALERT]\n%s", text)
            return True
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            r = requests.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if r.status_code == 429:  # rate limited
                retry = r.json().get("parameters", {}).get("retry_after", 5)
                log.warning("Telegram rate-limited, sleeping %s s", retry)
                time.sleep(retry + 1)
                return self._send(text)
            r.raise_for_status()
            return True
        except Exception as e:
            log.error("Telegram send failed: %s", e)
            return False

    @staticmethod
    def _esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def alert(self, job: Job, score: int) -> bool:
        e = self._esc
        text = (
            f"🚨 <b>{e(job.title)}</b>\n"
            f"🏢 {e(job.company)}\n"
            f"📍 {e(job.location) or '—'}\n"
            f"⭐ score: {score}\n"
            f'🔗 <a href="{e(job.url)}">Apply</a>'
        )
        return self._send(text)

    def alert_batch(self, jobs_with_scores: list[tuple[Job, int]]) -> int:
        """Send one alert per job. Returns count successfully sent."""
        sent = 0
        for job, score in jobs_with_scores:
            if self.alert(job, score):
                sent += 1
            # Telegram allows ~30 msg/sec but be conservative
            time.sleep(0.1)
        return sent

    def alert_run_summary(self, total_fetched: int, new_count: int, alert_count: int, errors: list[str]) -> None:
        """Optional digest after each run. Only sent if there were errors or alerts."""
        if alert_count == 0 and not errors:
            return
        e = self._esc
        err_block = ""
        if errors:
            err_block = "\n\n⚠️ Errors:\n" + "\n".join(f"• {e(x)}" for x in errors[:10])
        text = (
            f"📊 Run summary\n"
            f"Fetched: {total_fetched}\n"
            f"New: {new_count}\n"
            f"Alerted: {alert_count}"
            f"{err_block}"
        )
        if errors:  # only send a summary when there are errors (alerts already sent individually)
            self._send(text)
