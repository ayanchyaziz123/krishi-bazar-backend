import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from base.testing import member, shop, place_order

from base.models import Product, PurchaseItem

TMP = tempfile.mkdtemp()
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 100


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@override_settings(RECEIPT_ROOT=TMP)
class ReceivingTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        self.manager = client_for(member('m@r.test', 'm@r.test', 'pw'))
        self.dal = Product.objects.create(business=shop(), name='Masoor Dal', price=Decimal('150'), countInStock=5, unit='pack')

    def receive(self, qty, expiry=None, batch='', paid=None, receipt=None):
        import json
        body = {'invoice_no': 'INV-1', 'items': [{'product': self.dal._id, 'qty': qty, 'unit_cost': '100',
                                                  'batch_no': batch, 'expiry_date': expiry}]}
        if paid is not None:
            body['amount_paid'] = paid
        if receipt is not None:
            return self.manager.post('/api/inventory/purchases/', {'payload': json.dumps(body), 'receipt': receipt},
                                     format='multipart')
        return self.manager.post('/api/inventory/purchases/', body, format='json')

    def sell(self, qty):
        res = self.manager.post('/api/inventory/sales/', {'payment_method': 'cash',
                                'items': [{'product': self.dal._id, 'qty': qty}]}, format='json')
        self.assertEqual(res.status_code, 201, res.data)

    def test_receipt_upload_and_private_download(self):
        res = self.receive(10, receipt=SimpleUploadedFile('memo.png', PNG, content_type='image/png'))
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['receipt'], 'image')
        pid = res.data['id']
        download = self.manager.get(f'/api/inventory/purchases/{pid}/receipt/')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b''.join(download.streaming_content), PNG)
        # Cashiers and visitors cannot see supplier receipts.
        cashier = User.objects.create_user('c@r.test', 'c@r.test', 'pw')
        self.manager.post('/api/users/staff/', {'name': 'C', 'email': 'c2@r.test', 'password': 'longpassword', 'role': 'cashier'}, format='json')
        self.assertEqual(client_for(User.objects.get(username='c2@r.test')).get(f'/api/inventory/purchases/{pid}/receipt/').status_code, 403)
        self.assertIn(APIClient().get(f'/api/inventory/purchases/{pid}/receipt/').status_code, (401, 403))
        self.assertEqual(client_for(cashier).get(f'/api/inventory/purchases/{pid}/receipt/').status_code, 403)

    def test_receipt_added_later_and_wrong_type_refused(self):
        pid = self.receive(4).data['id']
        bad = self.manager.post(f'/api/inventory/purchases/{pid}/receipt/',
                                {'receipt': SimpleUploadedFile('virus.exe', b'MZ', content_type='application/octet-stream')}, format='multipart')
        self.assertEqual(bad.status_code, 400)
        good = self.manager.post(f'/api/inventory/purchases/{pid}/receipt/',
                                 {'receipt': SimpleUploadedFile('memo.pdf', b'%PDF-1.4', content_type='application/pdf')}, format='multipart')
        self.assertEqual((good.status_code, good.data['receipt']), (200, 'pdf'))

    def test_supplier_payment(self):
        res = self.receive(10, paid='300')
        self.assertEqual((res.data['status'], res.data['due']), ('partly_paid', 700.0))
        pid = res.data['id']
        self.assertEqual(self.manager.get('/api/inventory/purchases/').data['owed_total'], 700.0)
        self.assertEqual(self.manager.post(f'/api/inventory/purchases/{pid}/pay/', {'amount': '800'}, format='json').status_code, 400)
        done = self.manager.post(f'/api/inventory/purchases/{pid}/pay/', {'amount': '700'}, format='json')
        self.assertEqual(done.data['status'], 'paid')
        self.assertEqual(self.receive(1).data['status'], 'paid')  # paid in full by default

    def test_oldest_stock_sells_first(self):
        soon, later = (date.today() + timedelta(days=5)).isoformat(), (date.today() + timedelta(days=90)).isoformat()
        self.receive(10, expiry=later, batch='B-LATE')
        self.receive(10, expiry=soon, batch='B-SOON')
        self.sell(5)   # the 5 packs from before batches go first
        self.sell(4)   # then the batch that expires first
        batches = {b.batch_no: b.remaining for b in PurchaseItem.objects.all()}
        self.assertEqual(batches, {'B-LATE': Decimal('10'), 'B-SOON': Decimal('6')})
        shelf = self.manager.get(f'/api/inventory/products/{self.dal._id}/batches/').data
        self.assertEqual(shelf['untracked'], 0)
        self.assertEqual([b['batch_no'] for b in shelf['batches']], ['B-SOON', 'B-LATE'])
        expiring = self.manager.get('/api/inventory/summary/').data['expiring']
        self.assertEqual([(e['batch_no'], e['batch_qty']) for e in expiring], [('B-SOON', 6)])

    def test_online_orders_and_wastage_use_batches_too(self):
        self.receive(10, batch='B1')
        self.manager.post('/api/inventory/adjust/', {'product': self.dal._id, 'reason': 'wastage', 'qty': 7}, format='json')
        self.assertEqual(PurchaseItem.objects.get().remaining, Decimal('8'))
        buyer = client_for(User.objects.create_user('b@r.test', 'b@r.test', 'pw'))
        place_order(buyer, {'orderItems': [{'product': self.dal._id, 'qty': 3, 'price': '150'}],
                   'paymentMethod': 'cash', 'taxPrice': 0, 'shippingPrice': 0, 'totalPrice': 450,
                   'shippingAddress': {'address': '1 Road', 'city': 'Silchar', 'state': 'Assam', 'postalCode': '788001', 'country': 'India', 'phone': '9876543210'}})
        self.assertEqual(PurchaseItem.objects.get().remaining, Decimal('5'))

    def test_search_and_future_date(self):
        self.receive(2, batch='LOT-77')
        self.assertEqual(len(self.manager.get('/api/inventory/purchases/?q=LOT-77').data['deliveries']), 1)
        self.assertEqual(len(self.manager.get('/api/inventory/purchases/?q=nothing').data['deliveries']), 0)
        future = (date.today() + timedelta(days=3)).isoformat()
        res = self.manager.post('/api/inventory/purchases/', {'received_on': future, 'items': [
            {'product': self.dal._id, 'qty': 1, 'unit_cost': '1'}]}, format='json')
        self.assertEqual(res.status_code, 400)


@override_settings(RECEIPT_ROOT=TMP)
class BatchCostTests(TestCase):
    """10 bought at ₹10, then 10 at ₹20, sold at ₹30."""

    def setUp(self):
        self.manager = client_for(member('cost@r.test', 'cost@r.test', 'pw'))
        self.item = Product.objects.create(business=shop(), name='Beans', price=Decimal('30'), countInStock=0, unit='pack')
        for price, expiry in (('10', None), ('20', None)):
            res = self.manager.post('/api/inventory/purchases/', {'items': [
                {'product': self.item._id, 'qty': 10, 'unit_cost': price, 'expiry_date': expiry}]}, format='json')
            self.assertEqual(res.status_code, 201, res.data)

    def product(self):
        return next(p for p in self.manager.get('/api/inventory/products/').data if p['_id'] == self.item._id)

    def sell(self, qty):
        return self.manager.post('/api/inventory/sales/', {'payment_method': 'cash',
                                 'items': [{'product': self.item._id, 'qty': qty}]}, format='json').data

    def test_stock_value_and_price_range(self):
        p = self.product()
        self.assertEqual((p['stock_value'], p['cost_min'], p['cost_max'], p['cost_avg']), (300.0, 10.0, 20.0, 15.0))
        self.assertEqual(p['cost_price'], 20.0)  # latest price, still suggested when receiving

    def test_each_sale_costs_what_its_batch_cost(self):
        self.assertEqual(self.sell(10)['profit'], 200.0)   # all from the ₹10 batch
        self.assertEqual(self.sell(10)['profit'], 100.0)   # all from the ₹20 batch

    def test_a_sale_spanning_two_batches(self):
        sale = self.sell(12)                                # 10 × ₹10 + 2 × ₹20 = ₹140
        self.assertEqual(sale['profit'], 360.0 - 140.0)
        p = self.product()
        self.assertEqual((p['stock_value'], p['cost_min'], p['cost_max']), (160.0, 20.0, 20.0))
        report = self.manager.get('/api/inventory/summary/').data
        self.assertEqual(report['shop']['profit'], 220.0)
        self.assertEqual(report['stock_value'], 160.0)

    def test_wastage_and_online_orders_use_batch_cost(self):
        self.manager.post('/api/inventory/adjust/', {'product': self.item._id, 'reason': 'wastage', 'qty': 5}, format='json')
        self.assertEqual(self.manager.get('/api/inventory/summary/').data['wastage_cost'], 50.0)
        buyer = client_for(User.objects.create_user('ob@r.test', 'ob@r.test', 'pw'))
        place_order(buyer, {'orderItems': [{'product': self.item._id, 'qty': 10, 'price': '30'}],
                   'paymentMethod': 'cash', 'taxPrice': 0, 'shippingPrice': 0, 'totalPrice': 300,
                   'shippingAddress': {'address': '1 Road', 'city': 'Silchar', 'state': 'Assam', 'postalCode': '788001', 'country': 'India', 'phone': '9876543210'}})
        # 5 left at ₹10 + 5 at ₹20 = ₹150 cost, ₹300 goods
        self.assertEqual(self.manager.get('/api/inventory/summary/').data['online']['profit'], 150.0)

    def test_expired_batches_are_sold_last(self):
        past = (date.today() - timedelta(days=2)).isoformat()
        other = Product.objects.create(business=shop(), name='Pickle', price=Decimal('50'), countInStock=0)
        for price, expiry in (('5', past), ('8', None)):
            self.manager.post('/api/inventory/purchases/', {'items': [
                {'product': other._id, 'qty': 4, 'unit_cost': price, 'expiry_date': expiry}]}, format='json')
        sale = self.manager.post('/api/inventory/sales/', {'items': [{'product': other._id, 'qty': 3}]}, format='json').data
        self.assertEqual(sale['profit'], 150.0 - 24.0)     # from the good ₹8 batch, not the expired one
        batches = self.manager.get(f'/api/inventory/products/{other._id}/batches/').data['batches']
        self.assertEqual([b['remaining'] for b in batches], [1, 4])  # good batch first, expired last
