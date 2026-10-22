"""Counter returns, refunds and voids, and how they show in stock and reports."""
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from base.models import Product, Sale, StockMovement
from base.testing import member, shop


def client_for(user, business):
    c = APIClient()
    c.force_authenticate(user)
    c.credentials(HTTP_X_BUSINESS=business.slug)
    return c


class ReturnTests(TestCase):
    def setUp(self):
        cache.clear()
        self.biz = shop()
        self.manager = client_for(member('m@t.test', business=self.biz), self.biz)
        self.cashier = client_for(member('c@t.test', business=self.biz, role='cashier'), self.biz)
        self.tea = Product.objects.create(business=self.biz, name='Tea', price=Decimal('100'), cost_price=Decimal('60'), countInStock=20)
        self.rice = Product.objects.create(business=self.biz, name='Rice', price=Decimal('80'), cost_price=Decimal('50'),
                                           countInStock=Decimal('10'), unit='kg', sold_by_weight=True)

    def sell(self, items, client=None, **extra):
        res = (client or self.cashier).post('/api/inventory/sales/', {'payment_method': 'cash', 'items': items, **extra}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data

    def item_id(self, sale, product):
        detail = self.cashier.get(f"/api/inventory/sales/{sale['id']}/").data
        return next(i['id'] for i in detail['items'] if i['product'] == product._id)

    def give_back(self, sale, lines, client=None, **extra):
        body = {'items': [{'item': self.item_id(sale, p), 'qty': q} for p, q in lines], **extra}
        return (client or self.cashier).post(f"/api/inventory/sales/{sale['id']}/return/", body, format='json')

    def test_part_return_refunds_and_restocks(self):
        sale = self.sell([{'product': self.tea._id, 'qty': 2}])
        res = self.give_back(sale, [(self.tea, 1)], reason='Wrong flavour')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['return']['value'], res.data['return']['refund_amount'], res.data['return']['refund_label']), (100.0, 100.0, 'Cash'))
        self.assertEqual((res.data['sale']['status'], res.data['sale']['net_total'], res.data['sale']['due']), ('partly_returned', 100.0, 0.0))
        self.tea.refresh_from_db()
        self.assertEqual(self.tea.countInStock, 19)                       # 20 - 2 + 1
        back = StockMovement.objects.get(reason='return')
        self.assertEqual((back.change, back.unit_cost), (Decimal('1'), Decimal('60')))
        self.assertEqual(self.give_back(sale, [(self.tea, 2)]).status_code, 400)   # only 1 left to return

    def test_refund_shares_the_bill_discount_and_ends_exact(self):
        sale = self.sell([{'product': self.tea._id, 'qty': 3}], discount='10')          # 300 - 10 = 290
        first = self.give_back(sale, [(self.tea, 1)]).data['return']
        self.assertEqual(first['value'], 96.67)                                         # 100 x 290/300
        last = self.give_back(sale, [(self.tea, 2)]).data
        self.assertEqual(last['return']['value'], 193.33)                               # exactly what was left
        self.assertEqual((last['sale']['status'], last['sale']['net_total']), ('returned', 0.0))

    def test_returns_on_credit_lower_what_is_owed_first(self):
        sale = self.sell([{'product': self.tea._id, 'qty': 2}], payment_method='due', customer_name='Ravi',
                         customer_phone='9876543210', amount_paid='50')            # owes 150
        part = self.give_back(sale, [(self.tea, 1)]).data
        self.assertEqual((part['return']['refund_amount'], part['return']['refund_method'], part['sale']['due']), (0.0, 'none', 50.0))
        rest = self.give_back(sale, [(self.tea, 1)], refund_method='upi').data
        self.assertEqual((rest['return']['refund_amount'], rest['return']['refund_label'], rest['sale']['due']), (50.0, 'UPI', 0.0))
        self.assertEqual(self.cashier.get('/api/inventory/dues/').data, [])

    def test_damaged_items_are_not_put_back(self):
        sale = self.sell([{'product': self.tea._id, 'qty': 1}])
        res = self.give_back(sale, [(self.tea, 1)], restock=False, reason='Broken jar')
        self.assertFalse(res.data['return']['restocked'])
        self.tea.refresh_from_db()
        self.assertEqual(self.tea.countInStock, 19)
        # Profit loses the whole refund: the goods did not come back.
        self.assertEqual(self.manager.get('/api/inventory/summary/').data['shop']['profit'], -60.0)

    def test_weighed_and_whole_quantities(self):
        sale = self.sell([{'product': self.rice._id, 'qty': 1.5}, {'product': self.tea._id, 'qty': 2}])
        self.assertEqual(self.give_back(sale, [(self.rice, 0.5)]).data['return']['value'], 40.0)
        self.assertEqual(self.give_back(sale, [(self.tea, 0.5)]).status_code, 400)
        self.assertEqual(self.give_back(sale, []).status_code, 400)

    def test_void_is_for_managers_and_takes_everything_back(self):
        sale = self.sell([{'product': self.tea._id, 'qty': 2}])
        self.assertEqual(self.cashier.post(f"/api/inventory/sales/{sale['id']}/void/", {'reason': 'Mistake'}, format='json').status_code, 403)
        self.assertEqual(self.manager.post(f"/api/inventory/sales/{sale['id']}/void/", {}, format='json').status_code, 400)  # reason needed
        res = self.manager.post(f"/api/inventory/sales/{sale['id']}/void/", {'reason': 'Rang up twice'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['sale']['status'], res.data['return']['value'], res.data['return']['kind']), ('voided', 200.0, 'void'))
        self.tea.refresh_from_db()
        self.assertEqual(self.tea.countInStock, 20)
        self.assertEqual(self.manager.post(f"/api/inventory/sales/{sale['id']}/void/", {'reason': 'Again'}, format='json').status_code, 400)
        self.assertEqual(self.give_back(sale, [(self.tea, 1)]).status_code, 400)

    def test_reports_count_refunds(self):
        self.sell([{'product': self.tea._id, 'qty': 3}])                                   # 300 cash
        sale = self.sell([{'product': self.tea._id, 'qty': 1}], payment_method='upi')     # 100 UPI
        self.give_back(sale, [(self.tea, 1)])                                            # refunded by UPI
        s = self.manager.get('/api/inventory/summary/').data
        self.assertEqual((s['shop']['gross'], s['shop']['refunds'], s['shop']['total']), (400.0, 100.0, 300.0))
        self.assertEqual(s['shop']['profit'], 120.0)                                      # 3 x (100 - 60); the refund cancels the 4th
        self.assertEqual((s['payments']['cash'], s['payments']['upi']), (300.0, 0.0))
        self.assertEqual(s['refunds'], {'count': 1, 'value': 100.0, 'money': 100.0})
        self.assertEqual(s['all']['total'], 300.0)
        rows = self.manager.get('/api/inventory/transactions/').data['transactions']
        refund = next(r for r in rows if r['kind'] == 'refund')
        self.assertEqual((refund['total'], refund['sale'], refund['status_label']), (-100.0, sale['id'], 'Refund'))
        self.assertEqual(self.manager.get('/api/inventory/home/').data['today']['net_sales'], 300.0)

    def test_another_business_cannot_return_my_bills(self):
        sale = self.sell([{'product': self.tea._id, 'qty': 1}])
        other = shop('spice-house', 'Spice House')
        outsider = client_for(member('o@t.test', business=other), other)
        item = self.item_id(sale, self.tea)
        self.assertEqual(outsider.post(f"/api/inventory/sales/{sale['id']}/return/", {'items': [{'item': item, 'qty': 1}]}, format='json').status_code, 404)
        self.assertEqual(Sale.objects.get(pk=sale['id']).returned, 0)


class PastSaleTests(TestCase):
    """Sales written on paper and typed in later go into the day they happened."""

    def setUp(self):
        cache.clear()
        self.biz = shop()
        self.manager = client_for(member('m@t.test', business=self.biz), self.biz)
        self.cashier = client_for(member('c@t.test', business=self.biz, role='cashier'), self.biz)
        self.tea = Product.objects.create(business=self.biz, name='Tea', price=Decimal('100'), cost_price=Decimal('60'), countInStock=20)

    def sell(self, client, sold_at, qty=2):
        return client.post('/api/inventory/sales/', {'payment_method': 'cash', 'sold_at': sold_at,
                                                     'items': [{'product': self.tea._id, 'qty': qty}]}, format='json')

    def test_past_sale_goes_into_its_own_day(self):
        from datetime import timedelta
        from django.utils import timezone
        day = (timezone.now() - timedelta(days=3)).date().isoformat()
        res = self.sell(self.manager, f'{day}T18:30')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['entered_later'])
        self.assertTrue(res.data['createdAt'].startswith(f'{day}T18:30'))
        then = self.manager.get(f'/api/inventory/summary/?date={day}').data
        self.assertEqual((then['shop']['count'], then['shop']['total'], then['top_items'][0]['qty']), (1, 200.0, 2))
        self.assertEqual(self.manager.get('/api/inventory/summary/').data['shop']['count'], 0)   # not today
        rows = self.manager.get(f'/api/inventory/transactions/?date={day}').data['transactions']
        self.assertTrue(rows[0]['entered_later'])
        self.tea.refresh_from_db()
        self.assertEqual(self.tea.countInStock, 18)
        move = StockMovement.objects.get(reason='shop_sale')
        self.assertEqual(move.createdAt.isoformat()[:10], day)
        self.assertIn('entered later', move.note)

    def test_rules(self):
        from datetime import timedelta
        from django.utils import timezone
        yesterday = (timezone.now() - timedelta(days=1)).date().isoformat()
        self.assertEqual(self.sell(self.cashier, f'{yesterday}T10:00').status_code, 403)   # managers only
        tomorrow = (timezone.now() + timedelta(days=1)).date().isoformat()
        self.assertEqual(self.sell(self.manager, f'{tomorrow}T10:00').status_code, 400)
        self.assertEqual(self.sell(self.manager, '2020-01-01T10:00').status_code, 400)     # over a year back
        self.assertEqual(self.sell(self.manager, 'last tuesday').status_code, 400)
        self.tea.refresh_from_db()
        self.assertEqual(self.tea.countInStock, 20)


class StockEditWeightTests(TestCase):
    def setUp(self):
        cache.clear()
        self.biz = shop()
        self.manager = client_for(member('m@t.test', business=self.biz), self.biz)
        self.rice = Product.objects.create(business=self.biz, name='Rice', price=Decimal('80'), countInStock=10)

    def test_switch_to_weight_and_back(self):
        res = self.manager.patch(f'/api/inventory/products/{self.rice._id}/', {'unit': 'kg', 'sold_by_weight': True, 'cost_price': '62.5'}, format='json')
        self.assertEqual((res.status_code, res.data['sold_by_weight'], res.data['unit']), (200, True, 'kg'))
        self.assertEqual(self.manager.post('/api/inventory/adjust/', {'product': self.rice._id, 'reason': 'correction', 'qty': '9.75'}, format='json').status_code, 200)
        self.assertEqual(self.manager.patch(f'/api/inventory/products/{self.rice._id}/', {'sold_by_weight': False}, format='json').status_code, 400)
        self.manager.post('/api/inventory/adjust/', {'product': self.rice._id, 'reason': 'correction', 'qty': '9'}, format='json')
        self.assertEqual(self.manager.patch(f'/api/inventory/products/{self.rice._id}/', {'sold_by_weight': False}, format='json').status_code, 200)
