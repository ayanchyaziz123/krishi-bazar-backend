"""Checkout and delivery: each shop's delivery rules, saved addresses, order steps and cancelling."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from base.models import Coupon, CouponRedemption, CustomerAddress, Order, Product, StockMovement
from base.testing import member, shop, place_order

HOME = {'label': 'Home', 'full_name': 'Asha Das', 'phone': '+91 98765 43210', 'line1': '12 Tea Garden Road', 'area': 'Tarapur',
        'landmark': 'Near the post office', 'city': 'Silchar', 'state': 'Assam', 'pincode': '788003'}


def client_for(user=None, business=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    if business:
        c.credentials(HTTP_X_BUSINESS=business.slug)
    return c


class DeliveryTestCase(TestCase):
    def setUp(self):
        cache.clear()
        # Orders are paid online, so cancelling refunds through Razorpay (stood in for here).
        refund = mock.patch('base.payments.refund', return_value={'id': 'rfnd_test'})
        self.refund = refund.start()
        self.addCleanup(refund.stop)
        self.biz = shop()
        self.manager = client_for(member('m@t.test', business=self.biz), self.biz)
        self.buyer = User.objects.create_user('asha@t.test', 'asha@t.test', 'pw', first_name='Asha')
        self.shopper = client_for(self.buyer)
        self.rice = Product.objects.create(business=self.biz, name='Rice', price=Decimal('200'), countInStock=10)

    def settings(self, **shipping):
        res = self.manager.put('/api/store/', {'shipping': shipping}, format='json')
        self.biz.refresh_from_db()
        return res

    def add_address(self, client=None, **changes):
        return (client or self.shopper).post('/api/users/addresses/', {**HOME, **changes}, format='json')

    def order(self, qty=1, client=None, **body):
        body = {'orderItems': [{'product': self.rice._id, 'qty': qty}], 'paymentMethod': 'Cash on delivery', **body}
        return place_order((client or self.shopper), body)


class ShippingSettingsTests(DeliveryTestCase):
    def test_defaults_keep_the_original_rules(self):
        s = client_for().get('/api/store/').data['shipping']
        self.assertEqual((s['delivery_fee'], s['free_delivery_over'], s['tax_rate'], s['min_order']), ('10.00', '100.00', '5.00', '0.00'))
        self.assertTrue(s['delivery_enabled'] and s['pickup_enabled'])

    def test_shop_sets_its_own_fee_free_limit_and_tax(self):
        self.assertEqual(self.settings(delivery_fee='49', free_delivery_over='499', tax_rate='0').status_code, 200)
        address = self.add_address().data
        self.order(qty=2, addressId=address['id'])           # 400 worth of goods: under the free limit
        order = Order.objects.get()
        self.assertEqual((order.shippingPrice, order.taxPrice, order.totalPrice), (Decimal('49'), Decimal('0'), Decimal('449')))
        self.settings(free_delivery_over='')                  # never free
        self.order(qty=5, addressId=address['id'])
        self.assertEqual(Order.objects.latest('_id').shippingPrice, Decimal('49'))

    def test_minimum_order(self):
        self.settings(min_order='500')
        res = self.order(qty=1, addressId=self.add_address().data['id'])
        self.assertEqual(res.status_code, 400)
        self.assertIn('minimum order', res.data['detail'])
        self.assertEqual(self.order(qty=3, deliveryMethod='pickup').status_code, 200)

    def test_delivery_or_pickup_can_be_switched_off(self):
        address = self.add_address().data
        self.settings(delivery_enabled=False)
        self.assertEqual(self.order(addressId=address['id']).status_code, 400)
        self.assertEqual(self.order(deliveryMethod='pickup').status_code, 200)
        self.assertEqual(self.settings(pickup_enabled=False).status_code, 400)  # both off: refused
        self.settings(delivery_enabled=True, pickup_enabled=False)
        self.assertEqual(self.order(deliveryMethod='pickup').status_code, 400)
        self.assertEqual(self.order(addressId=address['id']).status_code, 200)

    def test_delivery_area_by_pin_code(self):
        self.assertEqual(self.settings(delivery_pincodes='788001, 7880*').status_code, 200)
        self.assertEqual(self.biz.delivery_pincodes, '788001, 7880*')
        inside = self.add_address(pincode='788005').data
        outside = self.add_address(pincode='781001', label='Work').data
        self.assertEqual((inside['delivers_here'], outside['delivers_here']), (True, False))
        self.assertEqual(self.order(addressId=inside['id']).status_code, 200)
        res = self.order(addressId=outside['id'])
        self.assertEqual(res.status_code, 400)
        self.assertIn("don't deliver to PIN 781001", res.data['detail'])
        self.assertEqual(self.settings(delivery_pincodes='78800, abc').status_code, 400)

    def test_settings_are_for_managers_of_their_own_shop(self):
        cashier = client_for(member('c@t.test', business=self.biz, role='cashier'), self.biz)
        self.assertEqual(cashier.put('/api/store/', {'shipping': {'delivery_fee': '0'}}, format='json').status_code, 403)
        other = shop('spice-house', 'Spice House')
        client_for(member('o@t.test', business=other), other).put('/api/store/', {'shipping': {'delivery_fee': '99'}}, format='json')
        self.biz.refresh_from_db()
        self.assertEqual(self.biz.delivery_fee, Decimal('10'))
        self.assertEqual(self.settings(delivery_fee='-5').status_code, 400)


class AddressTests(DeliveryTestCase):
    def test_saved_addresses_are_checked(self):
        res = self.add_address()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['phone'], res.data['is_default']), ('9876543210', True))
        for bad in ({'phone': '12345'}, {'pincode': '78800'}, {'state': 'Atlantis'}, {'full_name': ''}, {'line1': ''}, {'city': ''}):
            self.assertEqual(self.add_address(**bad).status_code, 400, bad)

    def test_default_address(self):
        first = self.add_address().data
        second = self.add_address(label='Work', is_default=True).data
        listed = self.shopper.get('/api/users/addresses/').data['addresses']
        self.assertEqual([(a['id'], a['is_default']) for a in listed], [(second['id'], True), (first['id'], False)])
        self.shopper.delete(f"/api/users/addresses/{second['id']}/")
        self.assertTrue(CustomerAddress.objects.get(pk=first['id']).is_default)

    def test_addresses_are_private_and_per_shop(self):
        mine = self.add_address().data
        other = client_for(User.objects.create_user('b@t.test', 'b@t.test', 'pw'))
        self.assertEqual(other.get('/api/users/addresses/').data['addresses'], [])
        self.assertEqual(other.patch(f"/api/users/addresses/{mine['id']}/", {'city': 'X'}, format='json').status_code, 404)
        self.assertEqual(other.delete(f"/api/users/addresses/{mine['id']}/").status_code, 404)
        # Someone else's saved address can't be used to place an order.
        other_order = self.order(client=other, addressId=mine['id'])
        self.assertEqual(other_order.status_code, 400)
        # Another shop doesn't see this shop's addresses.
        shop('spice-house', 'Spice House')
        at_b = client_for(self.buyer, None); at_b.credentials(HTTP_X_BUSINESS='spice-house')
        self.assertEqual(at_b.get('/api/users/addresses/').data['addresses'], [])
        self.assertEqual(client_for().get('/api/users/addresses/').status_code, 401)

    def test_order_keeps_a_copy_of_the_address(self):
        saved = self.add_address().data
        placed = self.order(addressId=saved['id'], deliveryNote='Ring the bell twice').data
        self.shopper.patch(f"/api/users/addresses/{saved['id']}/", {'city': 'Guwahati', 'pincode': '781001'}, format='json')
        a = Order.objects.get(_id=placed['_id']).shippingaddress
        self.assertEqual((a.full_name, a.phone, a.address, a.area, a.landmark, a.city, a.state, a.postalCode),
                         ('Asha Das', '9876543210', '12 Tea Garden Road', 'Tarapur', 'Near the post office', 'Silchar', 'Assam', '788003'))
        self.assertEqual(placed['delivery_note'], 'Ring the bell twice')

    def test_new_address_typed_at_checkout(self):
        res = self.order(shippingAddress={**HOME, 'phone': '9876543210'})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.order(shippingAddress={**HOME, 'phone': ''}).status_code, 400)


class OrderStepTests(DeliveryTestCase):
    def setUp(self):
        super().setUp()
        self.placed = self.order(qty=2, addressId=self.add_address().data['id']).data
        self.oid = self.placed['_id']

    def step(self, status, client=None, **extra):
        return (client or self.manager).put(f'/api/orders/{self.oid}/status/', {'status': status, **extra}, format='json')

    def test_order_moves_through_its_steps(self):
        self.assertEqual((self.placed['order_status'], self.placed['next_steps'][0]), ('placed', 'confirmed'))
        for status in ('confirmed', 'packed', 'out_for_delivery'):
            self.assertEqual(self.step(status).status_code, 200, status)
        self.assertEqual(self.step('packed').status_code, 400)          # no going back
        self.assertEqual(self.step('ready_for_pickup').status_code, 400)  # not a delivery step
        done = self.step('delivered', note='Left with the neighbour').data
        self.assertEqual((done['order_status'], done['isDelivered'], done['next_steps']), ('delivered', True, []))
        self.assertEqual([e['status'] for e in done['events']], ['placed', 'confirmed', 'packed', 'out_for_delivery', 'delivered'])
        self.assertEqual(done['events'][-1]['note'], 'Left with the neighbour')
        # A customer sees the same timeline.
        self.assertEqual(len(self.shopper.get(f'/api/orders/{self.oid}/').data['events']), 5)

    def test_pickup_orders_have_a_ready_step(self):
        pickup = self.order(deliveryMethod='pickup').data
        self.assertEqual(pickup['next_steps'], ['confirmed', 'packed', 'ready_for_pickup', 'delivered'])

    def test_only_order_staff_of_this_shop_change_steps(self):
        cashier = client_for(member('c@t.test', business=self.biz, role='cashier'), self.biz)
        self.assertEqual(self.step('confirmed', client=cashier).status_code, 403)
        self.assertEqual(self.step('confirmed', client=self.shopper).status_code, 403)
        other = shop('spice-house', 'Spice House')
        self.assertEqual(self.step('confirmed', client=client_for(member('o@t.test', business=other), other)).status_code, 404)

    def test_customer_cancels_before_packing_and_stock_comes_back(self):
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.countInStock, 8)
        res = self.shopper.put(f'/api/orders/{self.oid}/cancel/', {'reason': 'Ordered twice'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.refund.assert_called_once_with(Order.objects.get(_id=self.oid).payment_id)
        self.assertEqual(res.data['order_status'], 'cancelled')
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.countInStock, 10)
        back = StockMovement.objects.get(reason='online_cancel')
        self.assertEqual((back.change, back.order_id), (Decimal('2'), self.oid))
        self.assertIn('Ordered twice', res.data['events'][-1]['note'])
        self.assertEqual(self.shopper.put(f'/api/orders/{self.oid}/cancel/', {}, format='json').status_code, 400)  # only once
        self.assertEqual(self.step('confirmed').status_code, 400)

    def test_customer_cannot_cancel_once_packed(self):
        self.step('confirmed'); self.step('packed')
        self.assertEqual(self.shopper.put(f'/api/orders/{self.oid}/cancel/', {}, format='json').status_code, 400)
        # The shop can still cancel it, and the payment is refunded.
        res = self.manager.put(f'/api/orders/{self.oid}/status/', {'status': 'cancelled'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIn('Refund of', res.data['events'][-1]['note'])

    def test_someone_else_cannot_cancel(self):
        other = client_for(User.objects.create_user('b@t.test', 'b@t.test', 'pw'))
        self.assertEqual(other.put(f'/api/orders/{self.oid}/cancel/', {}, format='json').status_code, 404)

    def test_cancelling_gives_the_coupon_back(self):
        coupon = Coupon.objects.create(business=self.biz, coupon_code='SAVE20', discount_amount=20, discount_type='flat', expiration_date=timezone.now())
        redemption = CouponRedemption.objects.create(business=self.biz, user_id=self.buyer, coupon_code=coupon, total_discount=20, redemption_date=timezone.now())
        placed = self.order(deliveryMethod='pickup', coupon_id=redemption.id).data
        redemption.refresh_from_db()
        self.assertTrue(redemption.is_used)
        self.shopper.put(f"/api/orders/{placed['_id']}/cancel/", {}, format='json')
        redemption.refresh_from_db()
        self.assertFalse(redemption.is_used)
