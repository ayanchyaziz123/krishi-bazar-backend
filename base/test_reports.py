"""Sales and reports across every channel: counter bills and online orders together."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from base.models import Product
from base.testing import member, place_order, shop


def client_for(user, business):
    c = APIClient()
    c.force_authenticate(user)
    c.credentials(HTTP_X_BUSINESS=business.slug)
    return c


class AllChannelReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.biz = shop()
        self.manager = client_for(member('m@t.test', business=self.biz), self.biz)
        self.tea = Product.objects.create(business=self.biz, name='Tea', price=Decimal('100'), cost_price=Decimal('60'), countInStock=50)
        # Counter: one cash bill (2 x 100) and one UPI bill (1 x 100), and one bill on credit (1 x 100).
        for method, qty in (('cash', 2), ('upi', 1)):
            self.manager.post('/api/inventory/sales/', {'payment_method': method, 'items': [{'product': self.tea._id, 'qty': qty}]}, format='json')
        self.manager.post('/api/inventory/sales/', {'payment_method': 'due', 'customer_name': 'Ravi', 'customer_phone': '9876543210',
                                                   'items': [{'product': self.tea._id, 'qty': 1}]}, format='json')
        # Online: one paid pick-up order (3 x 100 + 5% tax = 315) and one that is then cancelled.
        buyer = APIClient(); buyer.force_authenticate(User.objects.create_user('b@t.test', 'b@t.test', 'pw', first_name='Asha'))
        body = lambda qty: {'orderItems': [{'product': self.tea._id, 'qty': qty}], 'deliveryMethod': 'pickup'}  # noqa: E731
        self.kept = place_order(buyer, body(3)).data
        self.dropped = place_order(buyer, body(1)).data
        with mock.patch('base.payments.refund', return_value={'id': 'rfnd_1'}):
            buyer.put(f"/api/orders/{self.dropped['_id']}/cancel/", {}, format='json')

    def test_one_list_for_every_channel(self):
        rows = self.manager.get('/api/inventory/transactions/').data['transactions']
        self.assertEqual(sorted((r['kind'], r['channel']) for r in rows),
                         [('online', 'Online · Pick-up'), ('online', 'Online · Pick-up'), ('shop', 'Counter'), ('shop', 'Counter'), ('shop', 'Counter')])
        online = {r['id']: r for r in rows if r['kind'] == 'online'}
        self.assertEqual((online[self.kept['_id']]['payment'], online[self.kept['_id']]['total'], online[self.kept['_id']]['counted']), ('Paid online', 315.0, True))
        self.assertEqual((online[self.dropped['_id']]['status_label'], online[self.dropped['_id']]['counted']), ('Refunded', False))
        self.assertEqual(online[self.kept['_id']]['profit'], 120.0)        # 3 x (100 - 60)
        self.assertEqual(online[self.kept['_id']]['customer'], 'Asha')

    def test_summary_counts_every_channel_but_not_cancellations(self):
        s = self.manager.get('/api/inventory/summary/').data
        self.assertEqual((s['shop']['count'], s['shop']['total']), (3, 400.0))
        self.assertEqual((s['online']['count'], s['online']['total'], s['online']['pickup_count']), (1, 315.0, 1))
        self.assertEqual((s['all']['count'], s['all']['total']), (4, 715.0))
        self.assertEqual(s['cancelled'], {'count': 1, 'refunded': 105.0})
        self.assertEqual(s['payments'], {'cash': 200.0, 'upi': 100.0, 'card': 0.0, 'other': 0.0, 'online': 315.0, 'due': 100.0})
        home = self.manager.get('/api/inventory/home/').data['today']
        self.assertEqual((home['online_count'], home['online_total']), (1, 315.0))

    def test_roles_and_businesses(self):
        cashier = client_for(member('c@t.test', business=self.biz, role='cashier'), self.biz)
        rows = cashier.get('/api/inventory/transactions/').data['transactions']
        self.assertEqual(len(rows), 5)
        self.assertNotIn('profit', rows[0])
        stock = client_for(member('s@t.test', business=self.biz, role='stock'), self.biz)
        self.assertEqual(stock.get('/api/inventory/transactions/').status_code, 403)
        other = shop('spice-house', 'Spice House')
        self.assertEqual(client_for(member('o@t.test', business=other), other).get('/api/inventory/transactions/').data['transactions'], [])


class DashboardProfitTests(AllChannelReportTests):
    def test_profit_covers_counter_and_online(self):
        today = self.manager.get('/api/inventory/home/').data['today']
        # Counter: 4 tea sold at 100, bought at 60 = 160. Online: 3 kept, at 100, bought at 60 = 120.
        self.assertEqual((today['shop_profit'], today['online_profit'], today['profit']), (160.0, 120.0, 280.0))
