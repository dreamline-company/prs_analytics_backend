"""Отправка писем через SMTP стандартной библиотекой.

Единственный почтовый канал приложения. ``smtplib`` блокирующий, поэтому
отправка уходит в поток; объём — несколько писем в сутки, отдельная
async-зависимость не нужна.
"""

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from core import get_logger
from core.settings.base import Settings
from shared.errors import AppError

logger = get_logger(__name__)


class MailNotConfiguredError(AppError):
    """SMTP_HOST или SMTP_FROM не заданы — отправлять нечем."""


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    host: str
    port: int
    sender: str
    user: str | None = None
    password: str | None = None
    starttls: bool = True
    ssl: bool = False
    timeout: int = 30


def smtp_config_from_settings(settings: Settings) -> SmtpConfig | None:
    """None — почта не настроена; вызывающий решает, ошибка это или пропуск."""
    if not settings.SMTP_HOST or not settings.SMTP_FROM:
        return None
    password = settings.SMTP_PASSWORD
    return SmtpConfig(
        host=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        sender=settings.SMTP_FROM,
        user=settings.SMTP_USER,
        password=password.get_secret_value() if password is not None else None,
        starttls=settings.SMTP_STARTTLS,
        ssl=settings.SMTP_SSL,
        timeout=settings.SMTP_TIMEOUT_SECONDS,
    )


class SmtpMailer:
    def __init__(self, config: SmtpConfig) -> None:
        self.config = config

    async def send(self, message: EmailMessage) -> None:
        """Отправить письмо; ``From`` подставляется из конфига, если не задан."""
        if "From" not in message:
            message["From"] = self.config.sender
        await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> None:
        config = self.config
        context = ssl.create_default_context()
        client_cls = smtplib.SMTP_SSL if config.ssl else smtplib.SMTP
        kwargs = {"context": context} if config.ssl else {}
        with client_cls(
            config.host,
            config.port,
            timeout=config.timeout,
            **kwargs,
        ) as smtp:
            if config.starttls and not config.ssl:
                smtp.starttls(context=context)
            if config.user:
                smtp.login(config.user, config.password or "")
            smtp.send_message(message)
        logger.info(
            "Mail sent: to=%s subject=%r attachments=%s",
            message["To"],
            message["Subject"],
            sum(1 for _ in message.iter_attachments()),
        )
