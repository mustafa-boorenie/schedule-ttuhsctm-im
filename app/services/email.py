"""
Email service for sending magic links and notifications.
"""
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from ..settings import settings


class EmailService:
    """Service for sending emails."""

    def __init__(self):
        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password
        self.from_email = settings.from_email

    async def send_email(
        self,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None,
    ) -> bool:
        """Send an email."""
        if not self.smtp_user or not self.smtp_password:
            # If SMTP is not configured, log the email instead
            print(f"[EMAIL] Would send to {to_email}:")
            print(f"  Subject: {subject}")
            print(f"  Content: {text_content or html_content[:200]}...")
            return True

        message = MIMEMultipart("alternative")
        message["From"] = self.from_email
        message["To"] = to_email
        message["Subject"] = subject

        if text_content:
            message.attach(MIMEText(text_content, "plain"))
        message.attach(MIMEText(html_content, "html"))

        try:
            await aiosmtplib.send(
                message,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user,
                password=self.smtp_password,
                start_tls=True,
            )
            return True
        except Exception as e:
            print(f"Failed to send email: {e}")
            return False

    async def send_magic_link(self, to_email: str, magic_link_url: str) -> bool:
        """Send a magic link email for admin authentication."""
        subject = "Your Login Link - Residency Rotation Calendar"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 40px 20px; }}
                .button {{
                    display: inline-block;
                    background: #06b6d4;
                    color: white;
                    padding: 14px 28px;
                    text-decoration: none;
                    border-radius: 8px;
                    font-weight: 500;
                    margin: 20px 0;
                }}
                .footer {{ color: #64748b; font-size: 14px; margin-top: 40px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Login to Rotation Calendar</h1>
                <p>Click the button below to log in to the admin portal. This link will expire in {settings.magic_link_expire_minutes} minutes.</p>

                <a href="{magic_link_url}" class="button">Log In</a>

                <p>Or copy and paste this URL into your browser:</p>
                <p style="word-break: break-all; color: #64748b;">{magic_link_url}</p>

                <div class="footer">
                    <p>If you didn't request this login link, you can safely ignore this email.</p>
                    <p>— Residency Rotation Calendar</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_content = f"""
Login to Rotation Calendar

Click the link below to log in to the admin portal. This link will expire in {settings.magic_link_expire_minutes} minutes.

{magic_link_url}

If you didn't request this login link, you can safely ignore this email.

— Residency Rotation Calendar
        """

        return await self.send_email(to_email, subject, html_content, text_content)
