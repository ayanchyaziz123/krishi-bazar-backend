"""Sending one-time codes by email. Credentials come from settings (the environment),
never from this file. Locally, without credentials, emails are printed in the terminal."""
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_otp(user_email, otp_code):
    try:
        send_mail(
            subject='Your sign-up code',
            message=f'Your code is {otp_code}. It expires in {settings.OTP_TTL_MINUTES} minutes. '
                    'If you did not ask for it, you can ignore this email.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user_email],
            fail_silently=False,
        )
        return True
    except Exception:  # noqa: BLE001 - any mail failure means "could not send"
        logger.exception('Could not send the sign-up code email')
        return False
