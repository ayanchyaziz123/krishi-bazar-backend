"""Orders are only placed after an online payment (Razorpay) that the server has verified."""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from base.models import Order, PaymentAttempt, Product
from base.testing import TEST_KEYS, fake_razorpay_order, place_order, shop, sign

ADDRESS = {'address': '1 Road', 'city': 'Silchar', 'state': 'Assam', 'postalCode': '788001', 'country': 'India', 'phone': '9876543210'}


@override_settings(**TEST_KEYS)
class PayFirstTests(TestCase):
    def setUp(self):
        cache.clear()
        self.biz = shop()
        self.buyer = User.objects.create_user('b@t.test', 'b@t.test', 'pw')
        self.client = APIClient(); self.client.force_authenticate(self.buyer)
        self.tea = Product.objects.create(business=self.biz, name='Tea', price=Decimal('200'), countInStock=5)
        patcher = mock.patch('base.payments.create_order', side_effect=fake_razorpay_order)
        self.create = patcher.start(); self.addCleanup(patcher.stop)
        refund = mock.patch('base.payments.refund', return_value={'id': 'rfnd_1'})
        self.refund = refund.start(); self.addCleanup(refund.stop)

    def body(self, qty=1, **extra):
        return {'orderItems': [{'product': self.tea._id, 'qty': qty}], 'shippingAddress': ADDRESS,
                'totalPrice': 1, 'paymentMethod': 'Cash on delivery', **extra}

    def start(self, **kw):
        return self.client.post('/api/orders/pay/start/', self.body(**kw), format='json')

    def confirm(self, start, payment_id='pay_1', signature=None, client=None):
        oid = start.data['razorpay_order_id']
        return (client or self.client).post('/api/orders/pay/confirm/', {
            'razorpay_order_id': oid, 'razorpay_payment_id': payment_id,
            'razorpay_signature': signature or sign(oid, payment_id)}, format='json')

    def test_starting_a_payment_creates_no_order(self):
        res = self.start(qty=2)
        self.assertEqual(res.status_code, 200, res.data)
        # 2 x 200 = 400, free delivery over 100, 5% tax = 20: the server's total, not the browser's 1.
        self.assertEqual((res.data['amount'], res.data['key_id']), (42000, 'rzp_test_key'))
        self.create.assert_called_once()
        self.assertEqual(Order.objects.count(), 0)
        self.tea.refresh_from_db()
        self.assertEqual(self.tea.countInStock, 5)

    def test_verified_payment_places_a_paid_order_once(self):
        start = self.start(qty=2)
        res = self.confirm(start)
        self.assertEqual(res.status_code, 200, res.data)
        order = Order.objects.get()
        self.assertEqual((order.isPaid, order.payment_id, order.totalPrice), (True, 'pay_1', Decimal('420')))
        self.assertEqual(order.paymentMethod, 'Paid online (Razorpay)')
        again = self.confirm(start)                       # the page retries, or the webhook arrives too
        self.assertEqual(again.data['_id'], res.data['_id'])
        self.assertEqual(Order.objects.count(), 1)
        self.tea.refresh_from_db()
        self.assertEqual(self.tea.countInStock, 3)

    def test_unverified_or_someone_elses_payment_places_nothing(self):
        start = self.start()
        self.assertEqual(self.confirm(start, signature='forged').status_code, 400)
        self.assertEqual(self.confirm(start, signature=sign(start.data['razorpay_order_id'], 'pay_1', 'wrong-secret')).status_code, 400)
        other = APIClient(); other.force_authenticate(User.objects.create_user('o@t.test', 'o@t.test', 'pw'))
        self.assertEqual(self.confirm(start, client=other).status_code, 404)
        self.assertEqual(Order.objects.count(), 0)

    def test_sold_out_while_paying_is_refunded(self):
        start = self.start(qty=5)
        Product.objects.filter(pk=self.tea.pk).update(countInStock=2)   # sold at the counter meanwhile
        res = self.confirm(start)
        self.assertEqual(res.status_code, 409)
        self.assertIn('being refunded', res.data['detail'])
        self.refund.assert_called_once_with('pay_1')
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(PaymentAttempt.objects.get().status, 'refunded')

    def test_no_keys_means_no_orders(self):
        with override_settings(RAZORPAY_KEY_ID='', RAZORPAY_KEY_SECRET=''):
            self.assertEqual(self.start().status_code, 503)
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_the_old_place_order_call_refuses(self):
        res = self.client.post('/api/orders/add/', self.body(), format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_checkout_rules_still_apply_before_paying(self):
        self.assertEqual(self.start(qty=99).status_code, 400)            # more than in stock
        self.assertEqual(self.client.post('/api/orders/pay/start/', {'orderItems': []}, format='json').status_code, 400)
        self.create.assert_not_called()
        self.assertEqual(APIClient().post('/api/orders/pay/start/', self.body(), format='json').status_code, 401)

    def webhook(self, payload, secret=TEST_KEYS['RAZORPAY_WEBHOOK_SECRET']):
        raw = json.dumps(payload).encode()
        sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return APIClient().post('/api/payments/razorpay/webhook/', raw, content_type='application/json', HTTP_X_RAZORPAY_SIGNATURE=sig)

    def captured(self, start, amount=None, payment_id='pay_hook'):
        return {'event': 'payment.captured', 'payload': {'payment': {'entity': {
            'id': payment_id, 'order_id': start.data['razorpay_order_id'], 'amount': amount or start.data['amount']}}}}

    def test_webhook_places_the_order_if_the_page_closed(self):
        start = self.start()
        self.assertEqual(self.webhook(self.captured(start), secret='wrong').status_code, 400)
        self.assertEqual(self.webhook(self.captured(start, amount=100)).status_code, 200)   # wrong amount: ignored
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(self.webhook(self.captured(start)).status_code, 200)
        self.assertEqual(Order.objects.get().payment_id, 'pay_hook')
        self.webhook(self.captured(start))                                                    # sent twice
        self.assertEqual(Order.objects.count(), 1)

    def test_customer_cancel_needs_the_refund_to_start(self):
        order_id = place_order(self.client, self.body()).data['_id']
        from base.payments import PaymentError
        self.refund.side_effect = PaymentError('Razorpay is down')
        res = self.client.put(f'/api/orders/{order_id}/cancel/', {}, format='json')
        self.assertEqual(res.status_code, 502)
        self.assertEqual(Order.objects.get(_id=order_id).order_status, 'placed')   # still active
        self.refund.side_effect = None
        self.assertEqual(self.client.put(f'/api/orders/{order_id}/cancel/', {}, format='json').status_code, 200)


@override_settings(**TEST_KEYS)
class LooseItemOnlineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.biz = shop()
        self.client = APIClient(); self.client.force_authenticate(User.objects.create_user('l@t.test', 'l@t.test', 'pw'))
        self.rice = Product.objects.create(business=self.biz, name='Loose rice', price=Decimal('80'), countInStock=Decimal('12.7'),
                                           unit='kg', sold_by_weight=True)
        self.jar = Product.objects.create(business=self.biz, name='Pickle jar', price=Decimal('200'), countInStock=5)

    def order(self, product, qty):
        return place_order(self.client, {'orderItems': [{'product': product._id, 'qty': qty}], 'deliveryMethod': 'pickup'})

    def test_loose_items_in_half_kilo_steps(self):
        self.assertEqual(APIClient().get(f'/api/products/{self.rice._id}/').data['product']['countInStock'], 12.5)
        res = self.order(self.rice, 1.5)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data['orderItems'][0]['qty'], res.data['orderItems'][0]['unit']), (1.5, 'kg'))
        order = Order.objects.get()
        self.assertEqual(order.totalPrice, Decimal('126'))   # 1.5 x 80 = 120, 5% tax 6, pick-up free
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.countInStock, Decimal('11.2'))

    def test_other_steps_are_refused(self):
        self.assertEqual(self.order(self.rice, 1.25).status_code, 400)   # loose: halves only online
        self.assertEqual(self.order(self.jar, 1.5).status_code, 400)     # jars: whole units
        self.assertEqual(self.order(self.rice, 13).status_code, 400)     # more than in stock
        self.assertEqual(Order.objects.count(), 0)
