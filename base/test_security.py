"""Phase 0 security fixes: server-side checkout pricing, coupons, sign-up codes,
rate limits, CORS and secrets kept out of the code."""
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from base.testing import member, shop, place_order

from base.models import Coupon, CouponRedemption, Order, Product, TempUser

ADDRESS = {'address': '1 Road', 'city': 'Silchar', 'state': 'Assam', 'postalCode': '788001', 'country': 'India', 'phone': '9876543210'}


class SecurityTestCase(TestCase):
    def setUp(self):
        cache.clear()  # rate-limit counters live in the cache


class CheckoutPricingTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.buyer = User.objects.create_user('buyer@t.test', 'buyer@t.test', 'pw')
        self.client = APIClient()
        self.client.force_authenticate(self.buyer)
        self.tea = Product.objects.create(business=shop(), name='Tea', price=Decimal('200'), online_price=Decimal('220'), countInStock=10)
        self.rice = Product.objects.create(business=shop(), name='Rice', price=Decimal('50'), is_offer=True, offer_percentage=10, countInStock=10)

    def order(self, items, **extra):
        body = {'orderItems': items, 'paymentMethod': 'PayPal', 'shippingAddress': ADDRESS,
                'taxPrice': 0, 'shippingPrice': 0, 'totalPrice': 1, **extra}
        return place_order(self.client, body)

    def test_browser_prices_and_totals_are_ignored(self):
        res = self.order([{'product': self.tea._id, 'qty': 2, 'price': '1'}])
        self.assertEqual(res.status_code, 200, res.data)
        order = Order.objects.get()
        # 2 x 220 online price = 440, free shipping over 100, 5% tax = 22
        self.assertEqual((order.taxPrice, order.shippingPrice, order.totalPrice), (Decimal('22'), Decimal('0'), Decimal('462')))
        self.assertEqual(order.orderitem_set.get().price, Decimal('220'))

    def test_offer_applied_once_and_small_orders_pay_shipping(self):
        self.order([{'product': self.rice._id, 'qty': 1, 'price': '0.01'}])
        order = Order.objects.get()
        # 50 less 10% = 45; shipping 10; tax 2.25
        self.assertEqual(order.totalPrice, Decimal('57.25'))

    def test_same_product_twice_is_counted_together_for_stock(self):
        res = self.order([{'product': self.tea._id, 'qty': 6}, {'product': self.tea._id, 'qty': 6}])
        self.assertEqual(res.status_code, 400)
        self.tea.refresh_from_db()
        self.assertEqual(self.tea.countInStock, 10)

    def test_store_pickup_needs_no_address_and_pays_no_shipping(self):
        res = self.order([{'product': self.rice._id, 'qty': 1}], deliveryMethod='pickup', shippingAddress={},
                         paymentMethod='Pay at store')
        self.assertEqual(res.status_code, 200, res.data)
        order = Order.objects.get()
        # 45 with no shipping (a delivery would pay 10); tax 2.25
        self.assertEqual((order.delivery_method, order.shippingPrice, order.totalPrice), ('pickup', Decimal('0'), Decimal('47.25')))
        self.assertEqual(order.shippingaddress.address, 'Pick up at Krishi Bazar')
        self.assertEqual(res.data['delivery_method'], 'pickup')
        self.assertEqual(self.order([{'product': self.rice._id, 'qty': 1}], deliveryMethod='drone').status_code, 400)

    def test_bad_input_is_refused(self):
        self.assertEqual(self.order([{'product': 'x', 'qty': 1}]).status_code, 400)
        self.assertEqual(self.order([{'product': self.tea._id, 'qty': 1}], shippingAddress={}).status_code, 400)
        self.assertEqual(place_order(APIClient(), {}).status_code, 401)


class CouponTests(SecurityTestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user('o@t.test', 'o@t.test', 'pw')
        self.other = User.objects.create_user('x@t.test', 'x@t.test', 'pw')
        coupon = Coupon.objects.create(business=shop(), coupon_code='SAVE30', discount_amount=30, discount_type='flat', expiration_date=timezone.now())
        self.redemption = CouponRedemption.objects.create(business=shop(), user_id=self.owner, coupon_code=coupon, total_discount=30,
                                                          redemption_date=timezone.now())
        self.item = Product.objects.create(business=shop(), name='Tea', price=Decimal('200'), countInStock=5)

    def client_for(self, user):
        c = APIClient(); c.force_authenticate(user); return c

    def test_customers_see_only_their_own_coupons(self):
        mine = self.client_for(self.owner).get('/api/products/coupons/mine/').data
        self.assertEqual([(c['id'], c['coupon_code']) for c in mine], [(self.redemption.id, 'SAVE30')])
        self.assertEqual(self.client_for(self.other).get('/api/products/coupons/mine/').data, [])
        self.assertEqual(APIClient().get('/api/products/coupons/mine/').status_code, 401)

    def test_coupon_list_is_private(self):
        self.assertIn(APIClient().get('/api/products/coupons/').status_code, (401, 403))
        self.assertEqual(self.client_for(self.owner).get('/api/products/coupons/').status_code, 403)

    def test_check_uses_the_login_not_the_body(self):
        res = self.client_for(self.other).post('/api/products/coupon_check/', {'coupon_code': 'SAVE30', 'user_id': self.owner.id}, format='json')
        self.assertEqual(res.data['status'], 2)
        res = self.client_for(self.owner).post('/api/products/coupon_check/', {'coupon_code': 'SAVE30'}, format='json')
        self.assertEqual((res.data['status'], res.data['coupon_id']), (3, self.redemption.id))

    def test_someone_else_cannot_use_or_burn_a_coupon(self):
        body = {'orderItems': [{'product': self.item._id, 'qty': 1}], 'shippingAddress': ADDRESS, 'coupon_id': self.redemption.id}
        self.assertEqual(place_order(self.client_for(self.other), body).status_code, 400)
        self.redemption.refresh_from_db()
        self.assertFalse(self.redemption.is_used)
        res = place_order(self.client_for(self.owner), body)
        self.assertEqual(Order.objects.get().totalPrice, Decimal('180'))  # 200 + 10 tax - 30
        self.redemption.refresh_from_db()
        self.assertTrue(self.redemption.is_used)
        again = place_order(self.client_for(self.owner), body)
        self.assertEqual(again.status_code, 400)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SignUpCodeTests(SecurityTestCase):
    def start(self, email='new@t.test'):
        return APIClient().post('/api/users/temp_register/', {'name': 'New', 'email': email, 'mobile': '01711000000',
                                                              'password': 'a-long-password-1'}, format='json')

    def code(self):
        return re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)

    def test_full_sign_up_and_the_password_works(self):
        self.assertEqual(self.start().status_code, 200)
        temp = TempUser.objects.get()
        self.assertNotEqual(temp.temp_user_otp, self.code())  # stored hashed
        res = APIClient().post('/api/users/register/', {'email': 'NEW@t.test', 'otp': self.code()}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        login = APIClient().post('/api/users/login/', {'username': 'new@t.test', 'password': 'a-long-password-1'}, format='json')
        self.assertEqual(login.status_code, 200)  # the old code hashed the password twice

    def test_wrong_code_attempts_are_limited(self):
        self.start()
        code = self.code()
        for _ in range(settings.OTP_MAX_ATTEMPTS):
            APIClient().post('/api/users/register/', {'email': 'new@t.test', 'otp': '000000' if code != '000000' else '111111'}, format='json')
        res = APIClient().post('/api/users/register/', {'email': 'new@t.test', 'otp': code}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(User.objects.filter(username='new@t.test').exists())

    def test_codes_expire(self):
        self.start()
        TempUser.objects.update(otp_created_at=timezone.now() - timedelta(minutes=settings.OTP_TTL_MINUTES + 1))
        res = APIClient().post('/api/users/register/', {'email': 'new@t.test', 'otp': self.code()}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_weak_passwords_and_missing_fields_refused(self):
        bad = APIClient().post('/api/users/temp_register/', {'name': 'N', 'email': 'w@t.test', 'mobile': '1', 'password': '123'}, format='json')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(APIClient().post('/api/users/temp_register/', {}, format='json').status_code, 400)
        self.assertEqual(len(mail.outbox), 0)


class RateLimitTests(SecurityTestCase):
    def test_login_is_rate_limited(self):
        User.objects.create_user('l@t.test', 'l@t.test', 'right-password')
        codes = [APIClient().post('/api/users/login/', {'username': 'l@t.test', 'password': 'wrong'}, format='json').status_code
                 for _ in range(11)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10], 429)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_code_emails_are_rate_limited(self):
        codes = [APIClient().post('/api/users/temp_register/', {'name': 'N', 'email': f'r{i}@t.test', 'mobile': f'0{i}',
                                                              'password': 'a-long-password-1'}, format='json').status_code for i in range(6)]
        self.assertEqual(codes[5], 429)
        self.assertEqual(len(mail.outbox), 5)

    def test_contact_form_is_rate_limited(self):
        body = {'first_name': 'R', 'email': 'r@t.test', 'message': 'Hello there'}
        codes = [APIClient().post('/api/contact/create/', body, format='json').status_code for _ in range(11)]
        self.assertEqual(codes[10], 429)


class ConfigTests(SecurityTestCase):
    def test_cors_is_not_open_to_every_site(self):
        self.assertFalse(settings.CORS_ALLOW_ALL_ORIGINS)
        res = APIClient().get('/api/products/', HTTP_ORIGIN='https://evil.example')
        self.assertNotIn('Access-Control-Allow-Origin', res)

    def test_no_secrets_written_in_the_code(self):
        root = Path(settings.BASE_DIR)
        patterns = [re.compile(r'\.login\(\s*[\'"][^\'"]+[\'"]\s*,\s*[\'"][^\'"]+[\'"]'),
                    re.compile(r'SECRET_KEY\s*=\s*[\'"][^\'"]{20,}[\'"]'),
                    re.compile(r'xai-[A-Za-z0-9]{20,}'), re.compile(r'AKIA[0-9A-Z]{16}')]
        offenders = []
        for path in list((root / 'base').rglob('*.py')) + list((root / 'backend').rglob('*.py')):
            text = path.read_text(errors='ignore')
            if any(p.search(text) for p in patterns):
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])
