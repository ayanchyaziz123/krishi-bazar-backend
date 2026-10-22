"""Tenant isolation: one business must never see or change another business's data.

Two businesses, A (the platform's first shop) and B, each with staff, products, stock,
sales, deliveries, suppliers, orders, messages and coupons. Every staff and public entry
point is exercised from both sides.
"""
import ast
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from base.models import (Category, Contact, Coupon, CouponRedemption, Membership, Order, Product, Purchase,
                         Review, Sale, StockMovement, Supplier)
from base.testing import member, shop, place_order

TMP = tempfile.mkdtemp()
ADDRESS = {'address': '1 Road', 'city': 'Silchar', 'state': 'Assam', 'postalCode': '788001', 'country': 'India', 'phone': '9876543210'}


def client_for(user, business=None):
    c = APIClient()
    c.force_authenticate(user)
    if business:
        c.credentials(HTTP_X_BUSINESS=business.slug)
    return c


@override_settings(RECEIPT_ROOT=TMP, MEDIA_ROOT=TMP)
class TenantIsolationTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.a, self.b = shop(), shop('spice-house', 'Spice House')
        self.mgr_a = member('a@t.test', business=self.a)
        self.mgr_b = member('b@t.test', business=self.b)
        self.A, self.B = client_for(self.mgr_a, self.a), client_for(self.mgr_b, self.b)
        self.data = {biz.slug: self._seed(biz, mgr) for biz, mgr in ((self.a, self.mgr_a), (self.b, self.mgr_b))}

    def _seed(self, biz, mgr):
        c = client_for(mgr, biz)
        product = Product.objects.create(business=biz, name=f'{biz.slug} tea', price=Decimal('100'), countInStock=20, code='T1')
        hidden = Product.objects.create(business=biz, name=f'{biz.slug} secret', price=Decimal('5'), countInStock=5, sell_online=False)
        category, _ = Category.objects.get_or_create(business=biz, name='Tea', defaults={'keyword': 'tea'})
        supplier = c.post('/api/inventory/suppliers/', {'name': f'{biz.slug} supplier'}, format='json').data
        purchase = c.post('/api/inventory/purchases/', {'supplier': supplier['id'], 'amount_paid': 0, 'items': [
            {'product': product._id, 'qty': 5, 'unit_cost': '60', 'batch_no': f'{biz.slug}-B1'}]}, format='json').data
        sale = c.post('/api/inventory/sales/', {'payment_method': 'cash', 'items': [{'product': product._id, 'qty': 1}]}, format='json').data
        due = c.post('/api/inventory/sales/', {'payment_method': 'due', 'customer_name': 'X', 'customer_phone': '1',
                     'items': [{'product': product._id, 'qty': 1}]}, format='json').data
        buyer = User.objects.create_user(f'buyer-{biz.slug}@t.test', f'buyer-{biz.slug}@t.test', 'pw')
        order = place_order(client_for(buyer, biz), {'orderItems': [{'product': product._id, 'qty': 1}],
                                            'shippingAddress': ADDRESS}).data
        contact = Contact.objects.create(business=biz, first_name='Q', email='q@t.test', message='Hello there')
        coupon = Coupon.objects.create(business=biz, coupon_code=f'{biz.slug}-10', discount_amount=10, discount_type='flat',
                                       expiration_date=timezone.now())
        CouponRedemption.objects.create(business=biz, user_id=buyer, coupon_code=coupon, total_discount=10, redemption_date=timezone.now())
        return dict(product=product, hidden=hidden, category=category, supplier=supplier, purchase=purchase,
                    sale=sale, due=due, order=order, buyer=buyer, contact=contact)

    # ---------------------------------------------------------------- staff lists

    def test_staff_lists_only_show_own_business(self):
        for client, own, other in ((self.A, self.a, self.b), (self.B, self.b, self.a)):
            mine, theirs = self.data[own.slug], self.data[other.slug]
            ids = lambda url, key='_id': {x[key] for x in client.get(url).data}
            self.assertEqual(ids('/api/inventory/products/'), {mine['product']._id, mine['hidden']._id})
            self.assertEqual({m['product'] for m in client.get('/api/inventory/movements/').data}, {mine['product']._id})
            self.assertEqual({s['id'] for s in client.get('/api/inventory/sales/').data['sales']}, {mine['sale']['id'], mine['due']['id']})
            self.assertEqual({s['id'] for s in client.get('/api/inventory/dues/').data}, {mine['due']['id']})
            self.assertEqual(ids('/api/inventory/suppliers/', 'id'), {mine['supplier']['id']})
            self.assertEqual({p['id'] for p in client.get('/api/inventory/purchases/').data['deliveries']}, {mine['purchase']['id']})
            self.assertEqual(ids('/api/orders/'), {mine['order']['_id']})
            self.assertEqual({m['id'] for m in client.get('/api/contact/').data['messages']}, {mine['contact'].id})
            self.assertEqual({c['coupon_code'] for c in client.get('/api/products/coupons/').data['coupons']}, {f'{own.slug}-10'})
            self.assertEqual({c['id'] for c in client.get('/api/categories/').data},
                             set(Category.objects.filter(business=own).values_list('id', flat=True)))
            self.assertNotIn(theirs['category'].id, {c['id'] for c in client.get('/api/categories/').data})
            people = {u['email'] for u in client.get('/api/users/').data}
            self.assertNotIn(theirs['buyer'].email, people)
            self.assertNotIn((self.mgr_b if own == self.a else self.mgr_a).email, people)
            self.assertIn(mine['buyer'].email, people)
            admin_list = {p['_id'] for p in client.get('/api/products/?scope=all&page_size=100').data['products']}
            self.assertEqual(admin_list, {mine['product']._id, mine['hidden']._id})

    def test_reports_count_only_own_business(self):
        for client in (self.A, self.B):
            summary = client.get('/api/inventory/summary/').data
            self.assertEqual(summary['shop']['count'], 2)
            self.assertEqual(summary['online']['count'], 1)
            home = client.get('/api/inventory/home/').data
            self.assertEqual(home['today']['transactions'], 3)
            self.assertEqual(home['alerts']['online_to_ship'], 1)
            self.assertEqual(len(home['recent']), 3)

    # ---------------------------------------------------------------- staff by id

    def test_staff_cannot_touch_other_business_by_id(self):
        theirs = self.data['spice-house']
        p, sale, due, purchase, order = theirs['product'], theirs['sale'], theirs['due'], theirs['purchase'], theirs['order']
        A = self.A
        attempts = [
            A.patch(f'/api/inventory/products/{p._id}/', {'reorder_level': 1}, format='json'),
            A.post('/api/inventory/adjust/', {'product': p._id, 'reason': 'wastage', 'qty': 1}, format='json'),
            A.get(f'/api/inventory/products/{p._id}/batches/'),
            A.get(f'/api/inventory/sales/{sale["id"]}/'),
            A.post(f'/api/inventory/sales/{due["id"]}/collect/', {'amount': 1}, format='json'),
            A.get(f'/api/inventory/purchases/{purchase["id"]}/'),
            A.get(f'/api/inventory/purchases/{purchase["id"]}/receipt/'),
            A.post(f'/api/inventory/purchases/{purchase["id"]}/pay/', {'amount': 1}, format='json'),
            A.put(f'/api/products/update/{p._id}/', {'name': 'hacked', 'price': '1'}, format='json'),
            A.delete(f'/api/products/delete/{p._id}/'),
            A.patch(f'/api/categories/{theirs["category"].id}/', {'name': 'hacked'}, format='json'),
            A.delete(f'/api/categories/{theirs["category"].id}/'),
            A.put(f'/api/orders/{order["_id"]}/deliver/', {}, format='json'),
            A.get(f'/api/orders/{order["_id"]}/'),
            A.patch(f'/api/contact/{theirs["contact"].id}/', {'replied': True}, format='json'),
            A.get(f'/api/users/{theirs["buyer"].id}/'),
            A.put(f'/api/users/update/{self.mgr_b.id}/', {'role': 'customer'}, format='json'),
            A.delete(f'/api/users/delete/{self.mgr_b.id}/'),
        ]
        self.assertEqual([r.status_code for r in attempts], [404] * len(attempts))
        upload = A.post('/api/products/upload/', {'product_id': p._id, 'image': SimpleUploadedFile('x.png', b'\x89PNG\r\n\x1a\n0', content_type='image/png')},
                        format='multipart')
        self.assertEqual(upload.status_code, 404)
        # Nothing in B changed.
        p.refresh_from_db()
        self.assertEqual((p.name, p.countInStock, p.reorder_level), ('spice-house tea', Decimal('22'), 5))
        self.assertTrue(Product.objects.filter(pk=p.pk).exists())
        self.assertFalse(Order.objects.get(_id=order['_id']).isDelivered)
        self.assertTrue(Membership.objects.filter(user=self.mgr_b, business=self.b, role='manager').exists())

    def test_staff_cannot_use_other_business_items_in_new_records(self):
        theirs = self.data['spice-house']
        sale = self.A.post('/api/inventory/sales/', {'items': [{'product': theirs['product']._id, 'qty': 1}]}, format='json')
        self.assertEqual(sale.status_code, 400)
        delivery = self.A.post('/api/inventory/purchases/', {'items': [{'product': theirs['product']._id, 'qty': 1, 'unit_cost': '1'}]}, format='json')
        self.assertEqual(delivery.status_code, 400)
        with_supplier = self.A.post('/api/inventory/purchases/', {'supplier': theirs['supplier']['id'], 'items': [
            {'product': self.data['krishi-bazar']['product']._id, 'qty': 1, 'unit_cost': '1'}]}, format='json')
        self.assertEqual(with_supplier.status_code, 400)
        created = self.A.post('/api/products/create/', {'name': 'New', 'price': '5', 'business': self.b.pk}, format='json')
        self.assertEqual(Product.objects.get(_id=created.data['_id']).business, self.a)  # business comes from the server

    def test_same_code_and_category_name_allowed_in_each_business(self):
        self.assertEqual(Product.objects.filter(code='T1').count(), 2)
        self.assertEqual(Category.objects.filter(name='Tea').count(), 2)
        dup = self.A.post('/api/products/create/', {'name': 'Dup', 'price': '1', 'code': 'T1'}, format='json')
        self.assertEqual(dup.status_code, 400)

    def test_store_settings_change_only_own_business(self):
        self.B.put('/api/store/', {'name': 'Spice House Dhaka'}, format='json')
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual((self.a.name, self.b.name), ('Krishi Bazar', 'Spice House Dhaka'))

    # ---------------------------------------------------------------- choosing a business

    def test_header_cannot_reach_a_business_you_dont_belong_to(self):
        spoof = client_for(self.mgr_a, self.b)
        for url in ('/api/inventory/products/', '/api/inventory/sales/', '/api/orders/', '/api/users/', '/api/contact/'):
            self.assertEqual(spoof.get(url).status_code, 403, url)

    def test_person_in_two_businesses_gets_each_role_separately(self):
        both = member('both@t.test', business=self.a, role='owner')
        Membership.objects.create(user=both, business=self.b, role='cashier')
        as_owner, as_cashier, unchosen = client_for(both, self.a), client_for(both, self.b), client_for(both)
        self.assertEqual(as_owner.get('/api/users/').status_code, 200)
        self.assertEqual(as_cashier.get('/api/users/').status_code, 403)          # cashier in B
        self.assertEqual(as_cashier.get('/api/inventory/sales/').status_code, 200)
        self.assertNotIn('cost_price', as_cashier.get('/api/inventory/products/').data[0])
        self.assertEqual(unchosen.get('/api/inventory/products/').status_code, 403)  # must choose one
        login = APIClient().post('/api/users/login/', {'username': 'both@t.test', 'password': 'pw'}, format='json').data
        self.assertEqual({b['slug']: b['role'] for b in login['businesses']}, {'krishi-bazar': 'owner', 'spice-house': 'cashier'})

    def test_suspended_business_is_closed(self):
        self.b.status = 'suspended'
        self.b.save()
        self.assertEqual(self.B.get('/api/inventory/products/').status_code, 403)
        public = APIClient(); public.credentials(HTTP_X_BUSINESS='spice-house')
        self.assertEqual(public.get('/api/products/').status_code, 404)
        self.assertEqual(self.A.get('/api/inventory/products/').status_code, 200)

    # ---------------------------------------------------------------- public shop

    def visitor(self, business, user=None):
        c = client_for(user) if user else APIClient()
        c.credentials(HTTP_X_BUSINESS=business.slug)
        return c

    def test_each_shop_shows_only_its_products(self):
        mine, theirs = self.data['krishi-bazar'], self.data['spice-house']
        b_shop = self.visitor(self.b)
        names = {p['name'] for p in b_shop.get('/api/products/').data['products']}
        self.assertEqual(names, {'spice-house tea'})
        self.assertEqual({p['name'] for p in b_shop.get('/api/products/all/').data}, {'spice-house tea'})
        self.assertEqual(b_shop.get(f'/api/products/{mine["product"]._id}/').status_code, 404)
        self.assertEqual(b_shop.get(f'/api/products/{theirs["hidden"]._id}/').status_code, 404)
        self.assertEqual({c['name'] for c in b_shop.get('/api/categories/').data}, {'Tea'})
        self.assertEqual(b_shop.get('/api/store/').data['slug'], 'spice-house')
        # No shop named: the platform's first shop.
        self.assertEqual({p['name'] for p in APIClient().get('/api/products/').data['products']}, {'krishi-bazar tea'})
        self.assertEqual(self.visitor(shop('nope')).get('/api/products/').status_code, 200)  # exists now via helper
        missing = APIClient(); missing.credentials(HTTP_X_BUSINESS='does-not-exist')
        self.assertEqual(missing.get('/api/products/').status_code, 404)

    def test_checkout_review_and_messages_stay_in_the_shop(self):
        mine, theirs = self.data['krishi-bazar'], self.data['spice-house']
        buyer = mine['buyer']
        at_b = self.visitor(self.b, buyer)
        cross = place_order(at_b, {'orderItems': [{'product': mine['product']._id, 'qty': 1}], 'shippingAddress': ADDRESS})
        self.assertEqual(cross.status_code, 400)
        own_coupon_elsewhere = at_b.post('/api/products/coupon_check/', {'coupon_code': 'krishi-bazar-10'}, format='json')
        self.assertEqual(own_coupon_elsewhere.data['status'], 2)
        placed = place_order(at_b, {'orderItems': [{'product': theirs['product']._id, 'qty': 1}], 'shippingAddress': ADDRESS})
        self.assertEqual(Order.objects.get(_id=placed.data['_id']).business, self.b)
        # Order history is per shop, with one login across shops.
        self.assertEqual({o['_id'] for o in at_b.get('/api/orders/myorders/').data}, {placed.data['_id']})
        self.assertEqual({o['_id'] for o in self.visitor(self.a, buyer).get('/api/orders/myorders/').data}, {mine['order']['_id']})
        self.assertEqual(at_b.get(f'/api/orders/{mine["order"]["_id"]}/').status_code, 404)
        self.assertEqual(at_b.post(f'/api/products/{mine["product"]._id}/reviews/', {'rating': 5, 'comment': 'x'}, format='json').status_code, 404)
        self.visitor(self.b).post('/api/contact/create/', {'first_name': 'R', 'email': 'r@t.test', 'message': 'For Spice House'}, format='json')
        self.assertEqual(Contact.objects.get(message='For Spice House').business, self.b)

    # ---------------------------------------------------------------- files and jobs

    def test_receipts_are_stored_and_served_per_business(self):
        png = lambda: SimpleUploadedFile('memo.png', b'\x89PNG\r\n\x1a\n' + b'0' * 20, content_type='image/png')
        pid = self.data['spice-house']['purchase']['id']
        self.assertEqual(self.B.post(f'/api/inventory/purchases/{pid}/receipt/', {'receipt': png()}, format='multipart').status_code, 200)
        purchase = Purchase.objects.get(pk=pid)
        self.assertTrue(purchase.receipt.name.startswith(f'{self.b.pk}/'))
        self.assertEqual(self.B.get(f'/api/inventory/purchases/{pid}/receipt/').status_code, 200)
        self.assertEqual(self.A.get(f'/api/inventory/purchases/{pid}/receipt/').status_code, 404)

    def test_sample_review_job_touches_only_the_chosen_business(self):
        call_command('add_sample_reviews', business='spice-house', per_product=1, verbosity=0)
        self.assertFalse(Review.objects.filter(product__business=self.a, is_sample=True).exists())
        self.assertTrue(Review.objects.filter(product__business=self.b, is_sample=True).exists())
        call_command('remove_sample_reviews', business='krishi-bazar', verbosity=0)
        self.assertTrue(Review.objects.filter(product__business=self.b, is_sample=True).exists())

    def test_every_business_row_has_a_business(self):
        for model in (Product, Category, Supplier, Purchase, Sale, StockMovement, Order, Contact, Coupon, CouponRedemption):
            self.assertFalse(model.objects.filter(business__isnull=True).exists(), model.__name__)


class TenantQueryAuditTests(TestCase):
    """Every query on a business-owned table in the views must be limited to a business.

    A static check: it reads the view code and fails if a `<Model>.objects...` chain
    for a business-owned model doesn't mention `business` (as a filter or when creating).
    """
    TENANT = {'Product', 'Order', 'Coupon', 'CouponRedemption', 'Contact', 'Farmer', 'Supplier',
              'Purchase', 'Sale', 'StockMovement', 'Category', 'SiteContent', 'SitePage', 'SiteImage', 'CustomerAddress', 'PaymentAttempt', 'SaleReturn'}
    REVERSE = ('order_set', 'sale_set', 'purchase_set', 'contact_set', 'product_set')

    def test_views_scope_every_business_query(self):
        problems = []
        for path in sorted((Path(settings.BASE_DIR) / 'base' / 'views').glob('*.py')):
            source = path.read_text()
            tree = ast.parse(source)
            parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == 'objects' and isinstance(node.value, ast.Name) \
                        and node.value.id in self.TENANT:
                    top = node
                    while isinstance(parents.get(top), (ast.Attribute, ast.Call)) and \
                            (getattr(parents[top], 'value', None) is top or getattr(parents[top], 'func', None) is top):
                        top = parents[top]
                    segment = ast.get_source_segment(source, top) or ''
                    if 'business' not in segment:
                        problems.append(f'{path.name}:{node.lineno}: {segment[:90]}')
                if isinstance(node, ast.Attribute) and node.attr in self.REVERSE:
                    problems.append(f'{path.name}:{node.lineno}: reverse relation {node.attr} skips the business filter')
        self.assertEqual(problems, [], 'Unscoped queries:\n' + '\n'.join(problems))


class CouponCodePerBusinessTests(TestCase):
    def test_two_businesses_can_use_the_same_code(self):
        from django.db import IntegrityError, transaction
        a, b = shop(), shop('spice-house', 'Spice House')
        when = timezone.now()
        mk = lambda biz, code: Coupon.objects.create(business=biz, coupon_code=code, discount_amount=10,
                                                     discount_type='flat', expiration_date=when)
        coupon_a, coupon_b = mk(a, 'SAVE10'), mk(b, 'SAVE10')
        with self.assertRaises(IntegrityError), transaction.atomic():
            mk(a, 'save10')  # same code in the same business, any capitals
        buyer = User.objects.create_user('buyer@t.test', 'buyer@t.test', 'pw')
        red_a = CouponRedemption.objects.create(business=a, user_id=buyer, coupon_code=coupon_a, total_discount=10, redemption_date=when)
        red_b = CouponRedemption.objects.create(business=b, user_id=buyer, coupon_code=coupon_b, total_discount=25, redemption_date=when)
        for biz, red, amount in ((a, red_a, 10), (b, red_b, 25)):
            c = client_for(buyer); c.credentials(HTTP_X_BUSINESS=biz.slug)
            res = c.post('/api/products/coupon_check/', {'coupon_code': 'save10'}, format='json').data
            self.assertEqual((res['status'], res['coupon_id'], res['total_discount']), (3, red.id, amount))
        mgr = member('m@t.test', business=b)
        listed = client_for(mgr, b).get('/api/products/coupons/').data
        self.assertEqual([r['coupon_code'] for r in listed['coupon_redemptions']], ['SAVE10'])
        self.assertEqual([c['id'] for c in listed['coupons']], [coupon_b.id])
