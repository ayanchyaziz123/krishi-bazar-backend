"""Online payments through Razorpay (UPI, cards, net banking, wallets).

Flow: the server opens a Razorpay order for the exact amount it worked out; the customer
pays in Razorpay's window; Razorpay signs the result; the server checks that signature
(and, from a webhook, Razorpay's webhook signature) before creating the shop's order.
Nothing the browser says about money is trusted.

Keys come from the environment (RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET and, for the webhook,
RAZORPAY_WEBHOOK_SECRET). Test keys (rzp_test_...) take test payments only.
"""
import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request

from django.conf import settings

API = 'https://api.razorpay.com/v1'


class PaymentError(Exception):
    pass


def is_configured():
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def _call(method, path, body=None):
    token = base64.b64encode(f'{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}'.encode()).decode()
    req = urllib.request.Request(f'{API}{path}', method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f'Basic {token}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        try:
            detail = json.load(e).get('error', {}).get('description')
        except Exception:  # noqa: BLE001 - any unreadable error body
            detail = None
        raise PaymentError(detail or f'Razorpay answered {e.code}.')
    except (urllib.error.URLError, TimeoutError) as e:
        raise PaymentError(f'Could not reach Razorpay: {e}')


def create_order(amount_paise, receipt, notes=None):
    """A Razorpay order for this amount. Returns Razorpay's order (with its id)."""
    return _call('POST', '/orders', {'amount': int(amount_paise), 'currency': 'INR', 'receipt': receipt[:40],
                                     'notes': notes or {}})


def refund(payment_id, amount_paise=None):
    """Start a refund of a payment (all of it, or `amount_paise`)."""
    body = {} if amount_paise is None else {'amount': int(amount_paise)}
    return _call('POST', f'/payments/{payment_id}/refund', body)


def _same(a, b):
    return hmac.compare_digest(str(a or ''), str(b or ''))


def signature_ok(razorpay_order_id, razorpay_payment_id, signature):
    """Razorpay signs "order_id|payment_id" with the key secret after a successful payment."""
    if not settings.RAZORPAY_KEY_SECRET:
        return False
    expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), f'{razorpay_order_id}|{razorpay_payment_id}'.encode(), hashlib.sha256).hexdigest()
    return _same(expected, signature)


def webhook_ok(raw_body, signature):
    """Webhook calls are signed over the raw body with the webhook secret."""
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return _same(expected, signature)
