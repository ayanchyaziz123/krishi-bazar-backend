from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from base.testing import member, shop, place_order

from base.models import Product, Sale, StockMovement


class InventoryApiTests(TestCase):
    def setUp(self):
        self.admin = member('owner', 'owner@example.com', 'pw')
        self.customer = User.objects.create_user('buyer', 'buyer@example.com', 'pw')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.rice = Product.objects.create(business=shop(), name='Chinigura Rice', category='Rice', price=Decimal('180'),
                                           countInStock=10, unit='kg', cost_price=Decimal('140'))
        self.tea = Product.objects.create(business=shop(), name='Sylheti Green Tea', category='Tea', price=Decimal('400'),
                                          is_offer=True, offer_percentage=5, countInStock=2)

    def test_non_admin_is_refused(self):
        client = APIClient()
        client.force_authenticate(self.customer)
        self.assertEqual(client.get('/api/inventory/products/').status_code, 403)

    def test_counter_sale_reduces_stock_and_records_profit(self):
        res = self.client.post('/api/inventory/sales/', {
            'payment_method': 'cash',
            'discount': '20',
            'items': [{'product': self.rice._id, 'qty': 3},
                      {'product': self.tea._id, 'qty': 1, 'unit_price': '1'}],
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.rice.refresh_from_db()
        self.tea.refresh_from_db()
        self.assertEqual(self.rice.countInStock, 7)
        self.assertEqual(self.tea.countInStock, 1)
        # A price sent by the browser is ignored: 3 x 180 + 380 offer price - 20 discount.
        self.assertEqual(res.data['total'], 900.0)
        self.assertEqual(res.data['amount_paid'], 900.0)
        # Cost is 3 x 140, and tea has no buying price yet.
        self.assertEqual(res.data['profit'], 900.0 - 420.0)
        self.assertEqual(StockMovement.objects.filter(reason='shop_sale').count(), 2)

    def test_offer_price_is_the_default(self):
        res = self.client.post('/api/inventory/sales/', {
            'items': [{'product': self.tea._id, 'qty': 1}]}, format='json')
        self.assertEqual(res.data['total'], 380.0)

    def test_overselling_is_refused_and_nothing_changes(self):
        res = self.client.post('/api/inventory/sales/', {
            'items': [{'product': self.rice._id, 'qty': 2},
                      {'product': self.tea._id, 'qty': 5}]}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertIn('Not enough stock', res.data['detail'])
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.countInStock, 10)
        self.assertEqual(Sale.objects.count(), 0)

    def test_due_sale_needs_customer_and_can_be_collected(self):
        res = self.client.post('/api/inventory/sales/', {
            'payment_method': 'due',
            'items': [{'product': self.rice._id, 'qty': 1}]}, format='json')
        self.assertEqual(res.status_code, 400)

        res = self.client.post('/api/inventory/sales/', {
            'payment_method': 'due', 'customer_name': 'Karim', 'customer_phone': '01700000000',
            'amount_paid': '50',
            'items': [{'product': self.rice._id, 'qty': 1}]}, format='json')
        self.assertEqual(res.data['due'], 130.0)
        sale_id = res.data['id']
        self.assertEqual(len(self.client.get('/api/inventory/dues/').data), 1)

        self.assertEqual(self.client.post(f'/api/inventory/sales/{sale_id}/collect/',
                                          {'amount': '200'}, format='json').status_code, 400)
        res = self.client.post(f'/api/inventory/sales/{sale_id}/collect/', {'amount': '130'}, format='json')
        self.assertEqual(res.data['due'], 0.0)
        self.assertEqual(len(self.client.get('/api/inventory/dues/').data), 0)

    def test_receiving_stock_adds_stock_and_updates_buying_price(self):
        supplier = self.client.post('/api/inventory/suppliers/', {'name': 'Sreemangal Tea Garden'}, format='json').data
        res = self.client.post('/api/inventory/purchases/', {
            'supplier': supplier['id'],
            'items': [{'product': self.tea._id, 'qty': 20, 'unit_cost': '250', 'expiry_date': '2026-10-03'}],
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.tea.refresh_from_db()
        self.assertEqual(self.tea.countInStock, 22)
        self.assertEqual(self.tea.cost_price, Decimal('250'))

    def test_wastage_and_correction(self):
        res = self.client.post('/api/inventory/adjust/', {
            'product': self.rice._id, 'reason': 'wastage', 'qty': 2, 'note': 'Damp sack'}, format='json')
        self.assertEqual(res.data['countInStock'], 8)
        res = self.client.post('/api/inventory/adjust/', {
            'product': self.rice._id, 'reason': 'correction', 'qty': 12}, format='json')
        self.assertEqual(res.data['countInStock'], 12)
        moves = self.client.get(f'/api/inventory/movements/?product={self.rice._id}').data
        self.assertEqual([m['change'] for m in moves], [4, -2])

    def test_summary(self):
        self.client.post('/api/inventory/sales/', {
            'items': [{'product': self.tea._id, 'qty': 1}]}, format='json')
        data = self.client.get('/api/inventory/summary/').data
        self.assertEqual(data['shop']['count'], 1)
        self.assertEqual(data['shop']['total'], 380.0)
        self.assertEqual(data['top_items'][0]['name'], 'Sylheti Green Tea')
        # Tea is now at 1, below the default reorder level of 5.
        self.assertIn(self.tea._id, [p['_id'] for p in data['low_stock']])

    def test_online_order_is_logged(self):
        client = APIClient()
        client.force_authenticate(self.customer)
        res = place_order(client, {
            'orderItems': [{'product': self.rice._id, 'qty': 2, 'price': '180'}],
            'paymentMethod': 'PayPal', 'taxPrice': '0', 'shippingPrice': '60', 'totalPrice': '420',
            'shippingAddress': {'address': '1 Road', 'city': 'Silchar', 'state': 'Assam', 'postalCode': '788001', 'country': 'India', 'phone': '9876543210'},
        })
        self.assertEqual(res.status_code, 200, res.data)
        move = StockMovement.objects.get(reason='online_sale')
        self.assertEqual((move.change, move.stock_after), (-2, 8))
        self.assertEqual(self.client.get('/api/inventory/summary/').data['online']['profit'], 360.0 - 280.0)


class ProductChannelTests(TestCase):
    """Sell online / in shop, online price, selling by weight and short codes."""

    def setUp(self):
        self.admin = member('owner2', 'owner2@example.com', 'pw')
        self.customer = User.objects.create_user('buyer2', 'buyer2@example.com', 'pw')
        self.staff = APIClient()
        self.staff.force_authenticate(self.admin)
        self.lentils = Product.objects.create(business=shop(), name='Masoor Dal', category='Dry Goods', price=Decimal('150'),
                                              countInStock=Decimal('10.5'), unit='kg', sold_by_weight=True,
                                              cost_price=Decimal('100'), code='101')
        self.jar = Product.objects.create(business=shop(), name='Mango Pickle', category='Pickle', price=Decimal('320'),
                                          online_price=Decimal('340'), countInStock=5, unit='jar')
        self.loose = Product.objects.create(business=shop(), name='Loose Chilies', category='Spices', price=Decimal('140'),
                                            countInStock=8, sell_online=False)
        self.web_only = Product.objects.create(business=shop(), name='Gift Box', category='Tea', price=Decimal('900'),
                                               countInStock=3, sell_in_shop=False)

    def _sale(self, product, qty):
        return self.staff.post('/api/inventory/sales/', {
            'items': [{'product': product._id, 'qty': qty}]}, format='json')

    def test_weighed_item_sells_part_kilos(self):
        res = self._sale(self.lentils, '1.25')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['total'], 187.5)
        self.assertEqual(res.data['items'][0]['qty'], 1.25)
        self.lentils.refresh_from_db()
        self.assertEqual(self.lentils.countInStock, Decimal('9.25'))

    def test_counted_item_refuses_part_units(self):
        res = self._sale(self.jar, '1.5')
        self.assertEqual(res.status_code, 400)
        self.assertIn('whole number', res.data['detail'])

    def test_counter_refuses_items_not_sold_in_shop(self):
        res = self._sale(self.web_only, 1)
        self.assertEqual(res.status_code, 400)
        self.assertIn('not sold in the shop', res.data['detail'])

    def test_counter_uses_shop_price_not_online_price(self):
        self.assertEqual(self._sale(self.jar, 1).data['total'], 320.0)

    def test_website_hides_shop_only_items_and_shows_online_price(self):
        names = [p['name'] for p in APIClient().get('/api/products/').data['products']]
        self.assertNotIn('Loose Chilies', names)
        self.assertIn('Gift Box', names)
        detail = APIClient().get(f'/api/products/{self.jar._id}/').data['product']
        self.assertEqual(Decimal(detail['price']), Decimal('340'))
        self.assertEqual(Decimal(detail['shop_price']), Decimal('320'))
        self.assertEqual(APIClient().get(f'/api/products/{self.loose._id}/').status_code, 404)

    def test_website_sees_weighed_stock_in_halves(self):
        # Loose items can be ordered online in steps of 0.5, so 10.5 kg is all available.
        detail = APIClient().get(f'/api/products/{self.lentils._id}/').data['product']
        self.assertEqual(detail['countInStock'], 10.5)
        self.assertEqual(detail['stock_exact'], 10.5)

    def test_admin_can_list_hidden_products(self):
        names = [p['name'] for p in self.staff.get('/api/products/?scope=all').data['products']]
        self.assertIn('Loose Chilies', names)
        # A customer asking for everything still only gets website products.
        customer = APIClient()
        customer.force_authenticate(self.customer)
        names = [p['name'] for p in customer.get('/api/products/?scope=all').data['products']]
        self.assertNotIn('Loose Chilies', names)

    def _order(self, product, qty):
        client = APIClient()
        client.force_authenticate(self.customer)
        return place_order(client, {
            'orderItems': [{'product': product._id, 'qty': qty, 'price': '1'}],
            'paymentMethod': 'cash', 'taxPrice': 0, 'shippingPrice': 0, 'totalPrice': 1,
            'shippingAddress': {'address': '1 Road', 'city': 'Silchar', 'state': 'Assam', 'postalCode': '788001', 'country': 'India', 'phone': '9876543210'},
        })

    def test_online_order_refuses_shop_only_items(self):
        res = self._order(self.loose, 1)
        self.assertEqual(res.status_code, 400)
        self.assertIn('no longer sold online', res.data['detail'])
        self.loose.refresh_from_db()
        self.assertEqual(self.loose.countInStock, 8)

    def test_online_order_refuses_more_than_stock(self):
        res = self._order(self.jar, 6)
        self.assertEqual(res.status_code, 400)
        self.assertIn('Only 5', res.data['detail'])

    def test_online_order_reduces_stock(self):
        res = self._order(self.jar, 2)
        self.assertEqual(res.status_code, 200, res.data)
        self.jar.refresh_from_db()
        self.assertEqual(self.jar.countInStock, 3)

    def _edit(self, product, **changes):
        body = {f: getattr(product, f) or '' for f in (
            'category', 'brand', 'name', 'model', 'processor', 'display', 'graphics_card', 'storage',
            'operating_system', 'web_cam', 'weight', 'color', 'battery', 'warranty', 'description')}
        body.update(ram_memory=0, price=str(product.price), countInStock=str(product.countInStock),
                    is_offer=False, offer_percentage=0)
        body.update(changes)
        return self.staff.put(f'/api/products/update/{product._id}/', body, format='json')

    def test_edit_saves_channels_and_code(self):
        res = self._edit(self.jar, sell_online=False, online_price='', code=' 205 ')
        self.assertEqual(res.status_code, 200, res.data)
        self.jar.refresh_from_db()
        self.assertFalse(self.jar.sell_online)
        self.assertIsNone(self.jar.online_price)
        self.assertEqual(self.jar.code, '205')

    def test_edit_refuses_duplicate_code(self):
        res = self._edit(self.jar, code='101')
        self.assertEqual(res.status_code, 400)
        self.assertIn('already used', res.data['detail'])

    def test_edit_records_stock_change_as_correction(self):
        res = self._edit(self.jar, countInStock='7')
        self.assertEqual(res.status_code, 200, res.data)
        self.jar.refresh_from_db()
        self.assertEqual(self.jar.countInStock, 7)
        move = StockMovement.objects.get(product=self.jar)
        self.assertEqual((move.reason, move.change), ('correction', Decimal('2')))

    def test_edit_refuses_part_units_for_counted_item(self):
        self.assertEqual(self._edit(self.jar, countInStock='2.5').status_code, 400)


class HomeDashboardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(member('home', 'home@example.com', 'pw'))
        self.rice = Product.objects.create(business=shop(), name='Rice', category='Rice', price=Decimal('100'),
                                           countInStock=10, cost_price=Decimal('60'))

    def test_today_figures_payments_and_recent(self):
        for method in ('cash', 'cash', 'bkash'):
            self.client.post('/api/inventory/sales/', {'payment_method': method,
                             'items': [{'product': self.rice._id, 'qty': 1}]}, format='json')
        res = self.client.get('/api/inventory/home/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['today']['net_sales'], 300.0)
        self.assertEqual(res.data['today']['transactions'], 3)
        self.assertEqual(res.data['today']['average_sale'], 100.0)
        self.assertEqual(res.data['today']['profit'], 120.0)
        self.assertEqual(res.data['last_week']['net_sales'], 0.0)
        self.assertEqual([(p['method'], p['count']) for p in res.data['payments']], [('Cash', 2), ('bKash', 1)])
        self.assertEqual(sum(h['shop'] for h in res.data['hourly']), 300.0)
        self.assertEqual(len(res.data['recent']), 3)
        self.assertEqual(res.data['top_items'][0]['qty'], 3)

    def test_customers_cannot_see_it(self):
        client = APIClient()
        client.force_authenticate(User.objects.create_user('c3', 'c3@example.com', 'pw'))
        self.assertEqual(client.get('/api/inventory/home/').status_code, 403)


class UpiPaymentTests(TestCase):
    def test_upi_sale_is_accepted(self):
        client = APIClient()
        client.force_authenticate(member('upi', 'upi@example.com', 'pw'))
        item = Product.objects.create(business=shop(), name='Tea', price=Decimal('200'), countInStock=3)
        res = client.post('/api/inventory/sales/', {'payment_method': 'upi',
                          'items': [{'product': item._id, 'qty': 1}]}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['payment_label'], 'UPI')
