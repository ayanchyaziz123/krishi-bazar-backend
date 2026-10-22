from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from base.testing import member, shop

from base.models import Category, Product


class CategoryApiTests(TestCase):
    def setUp(self):
        self.admin = APIClient()
        self.admin.force_authenticate(member('boss', 'boss@example.com', 'pw'))
        self.customer = APIClient()
        self.customer.force_authenticate(User.objects.create_user('cust', 'cust@example.com', 'pw'))
        Product.objects.create(business=shop(), name='Mango Pickle', category='Pickle', price=Decimal('320'), countInStock=5)

    def test_starter_categories_exist_and_are_public(self):
        res = APIClient().get('/api/categories/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('Pickles', [c['name'] for c in res.data])
        self.assertNotIn('product_count', res.data[0])

    def test_admin_sees_product_counts(self):
        # Counts come with the admin's business context (the X-Business header the admin pages send).
        pickles = next(c for c in self.admin.get('/api/categories/', HTTP_X_BUSINESS='krishi-bazar').data if c['name'] == 'Pickles')
        self.assertEqual(pickles['product_count'], 1)

    def test_admin_adds_a_category(self):
        res = self.admin.post('/api/categories/', {'name': 'Honey', 'icon': 'gift'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data['keyword'], res.data['icon']), ('honey', 'gift'))
        self.assertEqual(self.admin.post('/api/categories/', {'name': 'honey'}, format='json').status_code, 400)

    def test_customer_cannot_change_categories(self):
        self.assertEqual(self.customer.post('/api/categories/', {'name': 'X'}, format='json').status_code, 403)
        cat = Category.objects.get(name='Tea')
        self.assertEqual(self.customer.delete(f'/api/categories/{cat.pk}/').status_code, 403)

    def test_rename_moves_products_with_the_old_name(self):
        Product.objects.create(business=shop(), name='Green Tea', category='Tea', price=Decimal('380'), countInStock=2)
        cat = Category.objects.get(name='Tea')
        res = self.admin.patch(f'/api/categories/{cat.pk}/', {'name': 'Tea and Coffee'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(Product.objects.get(name='Green Tea').category, 'Tea and Coffee')
        self.assertEqual(res.data['product_count'], 1)

    def test_delete_refused_while_products_use_it(self):
        pickles = Category.objects.get(name='Pickles')
        res = self.admin.delete(f'/api/categories/{pickles.pk}/')
        self.assertEqual(res.status_code, 400)
        self.assertIn('1 item still use', res.data['detail'])
        empty = Category.objects.get(name='Handicraft')
        self.assertEqual(self.admin.delete(f'/api/categories/{empty.pk}/').status_code, 204)
