"""Helpers for tests: a shop (business) and staff who belong to it."""
from django.conf import settings
from django.contrib.auth.models import User

from base.models import Business, Membership


def shop(slug=None, name=None):
    """The default shop (the one the public site shows), or another business by slug."""
    slug = slug or settings.DEFAULT_BUSINESS_SLUG
    business, _ = Business.objects.get_or_create(slug=slug, defaults={'name': name or slug.replace('-', ' ').title()})
    return business


def member(username, email=None, password='pw', role='manager', business=None, **extra):
    """A user with a role in a business (the default shop unless given)."""
    user = User.objects.create_user(username, email or username, password, **extra)
    Membership.objects.create(user=user, business=business or shop(), role=role)
    return user


# --- Paying online in tests ----------------------------------------------------------------
# Orders are only created after an online payment. These helpers stand in for Razorpay:
# the Razorpay order is made up locally and the payment is signed the way Razorpay signs it.
import hashlib as _hashlib
import hmac as _hmac
import uuid as _uuid
from unittest import mock as _mock

from django.test import override_settings as _override_settings

TEST_KEYS = {'RAZORPAY_KEY_ID': 'rzp_test_key', 'RAZORPAY_KEY_SECRET': 'test-secret', 'RAZORPAY_WEBHOOK_SECRET': 'hook-secret'}


def fake_razorpay_order(amount_paise, receipt, notes=None):
    return {'id': f'order_{_uuid.uuid4().hex[:14]}', 'amount': int(amount_paise), 'currency': 'INR'}


def sign(razorpay_order_id, payment_id, secret=TEST_KEYS['RAZORPAY_KEY_SECRET']):
    return _hmac.new(secret.encode(), f'{razorpay_order_id}|{payment_id}'.encode(), _hashlib.sha256).hexdigest()


def place_order(client, body):
    """Check out and pay, like the payment page does. Returns the response that would
    reach the page: the start's error if it failed, else the confirmed order."""
    with _override_settings(**TEST_KEYS), _mock.patch('base.payments.create_order', side_effect=fake_razorpay_order):
        start = client.post('/api/orders/pay/start/', body, format='json')
        if start.status_code != 200:
            return start
        order_id, payment_id = start.data['razorpay_order_id'], f'pay_{_uuid.uuid4().hex[:14]}'
        return client.post('/api/orders/pay/confirm/', {'razorpay_order_id': order_id, 'razorpay_payment_id': payment_id,
                                                        'razorpay_signature': sign(order_id, payment_id)}, format='json')
