"""GST (India): registration settings, GST on counter bills and online orders, and the report."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from base.models import Order, Product, Sale
from base.testing import member, place_order, shop

GSTIN_MH = '27AAPFU0939F1ZV'   # a valid GSTIN registered in Maharashtra (27)


def client_for(user, business=None):
    c = APIClient()
    c.force_authenticate(user)
    if business:
        c.credentials(HTTP_X_BUSINESS=business.slug)
    return c


class GSTTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.biz = shop()
        self.manager = client_for(member('m@t.test', business=self.biz), self.biz)
        self.tea = Product.objects.create(business=self.biz, name='Tea', price=Decimal('105'), countInStock=50, gst_rate=5, hsn_code='0902')
        self.rice = Product.objects.create(business=self.biz, name='Loose rice', price=Decimal('80'), countInStock=50, gst_rate=0, hsn_code='1006')
        self.soap = Product.objects.create(business=self.biz, name='Soap', price=Decimal('118'), countInStock=50, gst_rate=18, hsn_code='3401')

    def register(self, **gst):
        return self.manager.put('/api/store/', {'gst': {'mode': 'regular', 'gstin': GSTIN_MH, 'state': 'Maharashtra', **gst}}, format='json')

    def sell(self, items, **extra):
        res = self.manager.post('/api/inventory/sales/', {'payment_method': 'cash', 'items': items, **extra}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data


class SettingsTests(GSTTestCase):
    def test_registration_is_checked(self):
        self.assertEqual(self.register().status_code, 200)
        self.assertEqual(self.manager.get('/api/store/', HTTP_X_BUSINESS='krishi-bazar').data['gst'],
                         {'mode': 'regular', 'gstin': GSTIN_MH, 'state': 'Maharashtra', 'prices_include_gst': True})
        self.assertEqual(self.register(gstin='27AAPFU0939F1ZX').status_code, 400)                   # wrong check digit
        self.assertIn('another state', self.register(state='Karnataka').data['detail'])            # GSTIN is from 27
        self.assertEqual(self.register(state='Atlantis').status_code, 400)
        self.assertEqual(self.manager.put('/api/store/', {'gst': {'mode': 'none', 'gstin': ''}}, format='json').status_code, 200)

    def test_item_rate_and_hsn_are_checked(self):
        ok = self.manager.put(f'/api/products/update/{self.tea._id}/', {'name': 'Tea', 'price': '105', 'gst_rate': '18', 'hsn_code': '090230'}, format='json')
        self.assertEqual(ok.status_code, 200, ok.data)
        for bad in ({'gst_rate': '7'}, {'hsn_code': '12'}, {'hsn_code': 'abcd'}):
            res = self.manager.put(f'/api/products/update/{self.tea._id}/', {'name': 'Tea', 'price': '105', **bad}, format='json')
            self.assertEqual(res.status_code, 400, bad)


class CounterTests(GSTTestCase):
    def test_not_registered_charges_nothing(self):
        sale = self.sell([{'product': self.tea._id, 'qty': 1}])
        self.assertEqual((sale['total'], sale['tax_total'], sale['gst_mode']), (105.0, 0.0, 'none'))

    def test_prices_include_gst(self):
        self.register()
        sale = self.sell([{'product': self.tea._id, 'qty': 2}, {'product': self.rice._id, 'qty': 1}])
        # Tea 210 incl. 5% = 200 + 10 GST; rice 80 at 0%. Total stays 290.
        self.assertEqual((sale['total'], sale['tax_total'], sale['cgst'], sale['sgst'], sale['igst']), (290.0, 10.0, 5.0, 5.0, 0.0))
        tea = next(i for i in sale['items'] if i['name'] == 'Tea')
        self.assertEqual((tea['taxable_value'], tea['tax_amount'], tea['hsn_code'], tea['gst_rate']), (200.0, 10.0, '0902', 5.0))

    def test_prices_exclude_gst_and_discount_before_tax(self):
        self.register(prices_include_gst=False)
        sale = self.sell([{'product': self.soap._id, 'qty': 1}], discount='18')
        # 118 - 18 = 100 taxable, 18% = 18 GST, total 118.
        self.assertEqual((sale['subtotal'], sale['discount'], sale['tax_total'], sale['total']), (118.0, 18.0, 18.0, 118.0))

    def test_composition_charges_no_gst(self):
        self.register(mode='composition')
        sale = self.sell([{'product': self.soap._id, 'qty': 1}])
        self.assertEqual((sale['total'], sale['tax_total'], sale['gst_mode']), (118.0, 0.0, 'composition'))


ADDRESS = {'line1': '1 Road', 'city': 'Pune', 'state': 'Maharashtra', 'pincode': '411001', 'phone': '9876543210'}


class OnlineTests(GSTTestCase):
    def setUp(self):
        super().setUp()
        self.buyer = client_for(User.objects.create_user('b@t.test', 'b@t.test', 'pw'))

    def order(self, state, **extra):
        res = place_order(self.buyer, {'orderItems': [{'product': self.tea._id, 'qty': 2}],
                                       'shippingAddress': {**ADDRESS, 'state': state, 'pincode': '411001' if state == 'Maharashtra' else '560001'}, **extra})
        self.assertEqual(res.status_code, 200, res.data)
        return Order.objects.get(_id=res.data['_id'])

    def test_same_state_cgst_sgst_other_state_igst(self):
        self.register()
        local = self.order('Maharashtra')
        self.assertEqual((local.taxPrice, local.cgst, local.sgst, local.igst), (Decimal('10'), Decimal('5'), Decimal('5'), Decimal('0')))
        self.assertEqual(local.totalPrice, Decimal('210'))         # 210 incl. GST, free delivery over 100, no flat tax
        far = self.order('Karnataka')
        self.assertEqual((far.cgst, far.igst), (Decimal('0'), Decimal('10')))
        item = far.orderitem_set.get()
        self.assertEqual((item.taxable_value, item.tax_amount, item.hsn_code), (Decimal('200'), Decimal('10'), '0902'))

    def test_exclusive_prices_add_gst(self):
        self.register(prices_include_gst=False)
        self.assertEqual(self.order('Maharashtra').totalPrice, Decimal('220.50'))   # 210 + 5%

    def test_not_registered_keeps_the_flat_online_tax(self):
        order = self.order('Maharashtra')
        self.assertEqual((order.taxPrice, order.gst_mode), (Decimal('10.50'), 'none'))   # the old 5% on goods


class ReportTests(GSTTestCase):
    def test_report_by_rate_and_hsn_with_returns(self):
        self.register()
        sale = self.sell([{'product': self.tea._id, 'qty': 2}, {'product': self.soap._id, 'qty': 1}])
        tea_item = next(i['id'] for i in sale['items'] if i['name'] == 'Tea')
        self.manager.post(f"/api/inventory/sales/{sale['id']}/return/", {'items': [{'item': tea_item, 'qty': 1}]}, format='json')
        buyer = client_for(User.objects.create_user('b@t.test', 'b@t.test', 'pw'))
        place_order(buyer, {'orderItems': [{'product': self.tea._id, 'qty': 1}],
                            'shippingAddress': {**ADDRESS, 'state': 'Karnataka', 'pincode': '560001'}})
        r = self.manager.get('/api/inventory/gst/').data
        rows = {row['rate']: row for row in r['by_rate']}
        # Tea 5%: counter 2 sold - 1 returned = 100 taxable, 5 CGST+SGST; online 1 = 100 taxable, 5 IGST.
        self.assertEqual((rows[5.0]['taxable'], rows[5.0]['cgst'], rows[5.0]['sgst'], rows[5.0]['igst']), (200.0, 2.5, 2.5, 5.0))
        # Soap 18%: 118 incl. = 100 + 18.
        self.assertEqual((rows[18.0]['taxable'], rows[18.0]['tax']), (100.0, 18.0))
        self.assertEqual(r['totals']['tax'], 28.0)
        tea = next(h for h in r['by_hsn'] if h['hsn'] == '0902')
        self.assertEqual((tea['qty'], tea['taxable'], tea['tax']), (2, 200.0, 10.0))
        self.assertEqual(r['invoices'], {'counter': 1, 'online': 1, 'credit_notes': 1})
        cashier = client_for(member('c@t.test', business=self.biz, role='cashier'), self.biz)
        self.assertEqual(cashier.get('/api/inventory/gst/').status_code, 403)
