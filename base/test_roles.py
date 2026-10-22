from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from base.testing import member, shop, place_order

from base.models import Order, Product


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


class StaffRoleTests(TestCase):
    def setUp(self):
        self.manager_user = member('boss@shop.test', 'boss@shop.test', 'pw')
        self.manager = client_for(self.manager_user)
        self.rice = Product.objects.create(business=shop(), name='Rice', category='Rice', price=Decimal('100'),
                                           countInStock=20, cost_price=Decimal('70'))

    def make(self, role, email):
        res = self.manager.post('/api/users/staff/', {'name': role.title(), 'email': email,
                                                     'password': 'longpassword', 'role': role}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['role'], role)
        return User.objects.get(username=email)

    # --- the old security hole -------------------------------------------------
    def test_customer_cannot_make_themselves_admin(self):
        customer = User.objects.create_user('cust@shop.test', 'cust@shop.test', 'pw')
        res = client_for(customer).put(f'/api/users/update/{customer.id}/',
                                       {'name': 'x', 'email': 'cust@shop.test', 'isAdmin': True}, format='json')
        self.assertEqual(res.status_code, 403)
        customer.refresh_from_db()
        self.assertFalse(customer.is_staff)

    # --- logging in ---------------------------------------------------------------
    def test_login_returns_role(self):
        self.make('cashier', 'till@shop.test')
        res = APIClient().post('/api/users/login/', {'username': 'till@shop.test', 'password': 'longpassword'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data['role'], res.data['isAdmin']), ('cashier', False))

    # --- cashier --------------------------------------------------------------------
    def test_cashier_sells_without_seeing_costs_or_profit(self):
        cashier = client_for(self.make('cashier', 'till@shop.test'))
        products = cashier.get('/api/inventory/products/').data
        self.assertNotIn('cost_price', products[0])
        sale = cashier.post('/api/inventory/sales/', {'payment_method': 'cash',
                            'items': [{'product': self.rice._id, 'qty': 2}]}, format='json')
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertNotIn('profit', sale.data)
        self.assertNotIn('profit', cashier.get('/api/inventory/sales/').data['sales'][0])
        summary = cashier.get('/api/inventory/summary/').data
        self.assertNotIn('profit', summary['shop'])
        self.assertNotIn('stock_value', summary)

    def test_cashier_is_kept_out_of_stock_and_manager_pages(self):
        cashier = client_for(self.make('cashier', 'till@shop.test'))
        blocked = [
            ('post', '/api/inventory/adjust/', {'product': self.rice._id, 'reason': 'wastage', 'qty': 1}),
            ('post', '/api/inventory/purchases/', {'items': []}),
            ('get', '/api/inventory/home/', None),
            ('post', '/api/products/create/', None),
            ('get', '/api/orders/', None),
            ('get', '/api/users/', None),
            ('post', '/api/categories/', {'name': 'X'}),
        ]
        for method, url, body in blocked:
            res = getattr(cashier, method)(url, body, format='json')
            self.assertEqual(res.status_code, 403, f'{method} {url}')

    # --- stock keeper ----------------------------------------------------------------
    def test_stock_keeper_manages_stock_but_cannot_sell(self):
        keeper = client_for(self.make('stock', 'store@shop.test'))
        res = keeper.post('/api/inventory/purchases/', {'items': [
            {'product': self.rice._id, 'qty': 5, 'unit_cost': '72'}]}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        res = keeper.post('/api/inventory/adjust/', {'product': self.rice._id, 'reason': 'wastage', 'qty': 1}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.countInStock, 24)
        self.assertEqual(keeper.post('/api/inventory/sales/', {'items': [
            {'product': self.rice._id, 'qty': 1}]}, format='json').status_code, 403)
        summary = keeper.get('/api/inventory/summary/').data
        self.assertNotIn('shop', summary)
        self.assertIn('low_stock', summary)

    # --- managing staff ---------------------------------------------------------------
    def test_manager_changes_roles(self):
        user = self.make('cashier', 'till@shop.test')
        res = self.manager.put(f'/api/users/update/{user.id}/', {'name': 'Till', 'email': 'till@shop.test',
                               'role': 'stock'}, format='json')
        self.assertEqual(res.data['role'], 'stock')
        res = self.manager.put(f'/api/users/update/{user.id}/', {'name': 'Till', 'email': 'till@shop.test',
                               'role': 'manager'}, format='json')
        self.assertEqual((res.data['role'], res.data['isAdmin']), ('manager', True))
        res = self.manager.put(f'/api/users/update/{user.id}/', {'name': 'Till', 'email': 'till@shop.test',
                               'role': 'customer'}, format='json')
        self.assertIsNone(res.data['role'])
        self.assertEqual(client_for(User.objects.get(pk=user.pk)).get('/api/inventory/products/').status_code, 403)

    def test_manager_cannot_lock_themselves_out(self):
        res = self.manager.put(f'/api/users/update/{self.manager_user.id}/', {'name': 'Boss',
                               'email': 'boss@shop.test', 'role': 'cashier'}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.manager.delete(f'/api/users/delete/{self.manager_user.id}/').status_code, 400)

    def test_old_admin_checkbox_still_works(self):
        # A customer of this shop (they have an order here) can be made a manager.
        user = User.objects.create_user('old@shop.test', 'old@shop.test', 'pw')
        Order.objects.create(business=shop(), user=user, totalPrice=0)
        res = self.manager.put(f'/api/users/update/{user.id}/', {'name': 'old@shop.test', 'email': 'old@shop.test',
                               'isAdmin': True}, format='json')
        self.assertEqual(res.data['role'], 'manager')

    def test_staff_account_rules(self):
        bad = self.manager.post('/api/users/staff/', {'name': 'A', 'email': 'a@shop.test',
                                'password': 'short', 'role': 'cashier'}, format='json')
        self.assertEqual(bad.status_code, 400)
        self.make('cashier', 'till@shop.test')
        dup = self.manager.post('/api/users/staff/', {'name': 'B', 'email': 'TILL@shop.test',
                                'password': 'longpassword', 'role': 'cashier'}, format='json')
        self.assertEqual(dup.status_code, 400)
        cashier = client_for(User.objects.get(username='till@shop.test'))
        self.assertEqual(cashier.post('/api/users/staff/', {'name': 'C', 'email': 'c@shop.test',
                         'password': 'longpassword', 'role': 'manager'}, format='json').status_code, 403)


class OnlineOrdersRoleTests(TestCase):
    def setUp(self):
        self.manager = client_for(member('m@shop.test', 'm@shop.test', 'pw'))
        res = self.manager.post('/api/users/staff/', {'name': 'Orders', 'email': 'web@shop.test',
                                'password': 'longpassword', 'role': 'orders'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['role'], 'orders')
        self.orders = client_for(User.objects.get(username='web@shop.test'))
        self.buyer_user = User.objects.create_user('buyer@shop.test', 'buyer@shop.test', 'pw')
        self.buyer = client_for(self.buyer_user)
        self.other = client_for(User.objects.create_user('other@shop.test', 'other@shop.test', 'pw'))
        item = Product.objects.create(business=shop(), name='Pickle', price=Decimal('300'), countInStock=5)
        res = place_order(self.buyer, {
            'orderItems': [{'product': item._id, 'qty': 1, 'price': '300'}],
            'paymentMethod': 'cash', 'taxPrice': 0, 'shippingPrice': 0, 'totalPrice': 300,
            'shippingAddress': {'address': '1 Road', 'city': 'Silchar', 'state': 'Assam', 'postalCode': '788001', 'country': 'India', 'phone': '9876543210'},
        })
        self.order_id = res.data['_id']

    def test_orders_staff_handle_website_orders(self):
        self.assertEqual(len(self.orders.get('/api/orders/').data), 1)
        self.assertEqual(self.orders.get(f'/api/orders/{self.order_id}/').status_code, 200)
        self.assertEqual(self.orders.put(f'/api/orders/{self.order_id}/pay/', {}, format='json').status_code, 200)
        self.assertEqual(self.orders.put(f'/api/orders/{self.order_id}/deliver/', {}, format='json').status_code, 200)

    def test_orders_staff_are_kept_out_of_everything_else(self):
        for method, url in [('get', '/api/inventory/products/'.replace('products/', 'home/')),
                            ('post', '/api/inventory/sales/'), ('post', '/api/inventory/adjust/'),
                            ('get', '/api/users/'), ('post', '/api/products/create/')]:
            self.assertEqual(getattr(self.orders, method)(url, {}, format='json').status_code, 403, url)

    def test_customers_only_see_and_pay_their_own_orders(self):
        self.assertEqual(self.buyer.get(f'/api/orders/{self.order_id}/').status_code, 200)
        # "Not found" rather than "forbidden", so other people's order numbers reveal nothing.
        self.assertEqual(self.other.get(f'/api/orders/{self.order_id}/').status_code, 404)
        self.assertEqual(self.other.put(f'/api/orders/{self.order_id}/pay/', {}, format='json').status_code, 404)
        self.assertEqual(self.other.get('/api/orders/').status_code, 403)
        # A cash order is marked paid by the shop, not by the customer.
        self.assertEqual(self.buyer.put(f'/api/orders/{self.order_id}/pay/', {}, format='json').status_code, 403)

    def test_customers_never_mark_orders_paid(self):
        # Orders are paid online at checkout; an older unpaid order stays unpaid until the shop marks it.
        Order.objects.filter(_id=self.order_id).update(isPaid=False, paymentMethod='PayPal')
        self.assertEqual(self.buyer.put(f'/api/orders/{self.order_id}/pay/', {}, format='json').status_code, 403)
        self.assertFalse(Order.objects.get(_id=self.order_id).isPaid)
