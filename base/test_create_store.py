"""Opening a new shop: an empty catalog, an owner who can sign in and run it, and a public
shop that shows only its own items and name."""
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.test import APIClient

from base.models import Business, Membership, Product
from base.testing import shop


def create(**extra):
    out = StringIO()
    args = {'name': 'GBI', 'slug': 'gbi', 'owner_name': 'Abdullah', 'owner_email': 'owner@gbi.local', **extra}
    call_command('create_store', stdout=out, **args)
    return out.getvalue()


class CreateStoreTests(TestCase):
    def setUp(self):
        self.first = shop()
        Product.objects.create(business=self.first, name='Rice', price=50, countInStock=5)

    def test_creates_empty_shop_with_owner(self):
        out = create()
        business = Business.objects.get(slug='gbi')
        owner = User.objects.get(username='owner@gbi.local')
        self.assertEqual(business.owner, owner)
        self.assertEqual(Membership.objects.get(business=business).role, 'owner')
        self.assertFalse(Product.objects.filter(business=business).exists())
        password = [l for l in out.splitlines() if l.startswith('Password: ')][0].split(': ', 1)[1]
        self.assertTrue(owner.check_password(password))

    def test_owner_signs_in_and_works_in_own_shop(self):
        out = create()
        password = [l for l in out.splitlines() if l.startswith('Password: ')][0].split(': ', 1)[1]
        res = APIClient().post('/api/users/login/', {'username': 'owner@gbi.local', 'password': password}, format='json')
        self.assertEqual(res.status_code, 200)
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['token']}", HTTP_X_BUSINESS='gbi')
        res = c.post('/api/products/create/', {'name': 'Basmati', 'price': '90'}, format='json')
        self.assertIn(res.status_code, (200, 201))
        self.assertTrue(Product.objects.filter(business__slug='gbi').exists())
        self.assertEqual(Product.objects.filter(business=self.first).count(), 1)

    def test_public_shop_by_slug(self):
        create()
        gbi = APIClient()
        gbi.credentials(HTTP_X_BUSINESS='gbi')
        self.assertEqual(gbi.get('/api/store/').data['name'], 'GBI')
        products = gbi.get('/api/products/').data
        self.assertEqual(len(products['products'] if isinstance(products, dict) else products), 0)
        default = APIClient().get('/api/products/').data
        self.assertEqual(len(default['products'] if isinstance(default, dict) else default), 1)
        bad = APIClient()
        bad.credentials(HTTP_X_BUSINESS='nope')
        self.assertEqual(bad.get('/api/store/').status_code, 404)

    def test_taken_slug_changes_nothing(self):
        create()
        with self.assertRaises(CommandError):
            create(owner_email='other@gbi.local')
        self.assertFalse(User.objects.filter(username='other@gbi.local').exists())

    def test_existing_account_keeps_password(self):
        user = User.objects.create_user(username='owner@gbi.local', email='owner@gbi.local', password='Old-pass-123')
        out = create()
        user.refresh_from_db()
        self.assertTrue(user.check_password('Old-pass-123'))
        self.assertNotIn('Password: ', out)

    def test_chosen_password(self):
        out = create(password='Shop-Chosen-2026')
        self.assertTrue(User.objects.get(username='owner@gbi.local').check_password('Shop-Chosen-2026'))
        self.assertNotIn('Shop-Chosen-2026', out)

    def test_weak_chosen_password_refused(self):
        with self.assertRaises(CommandError):
            create(password='123')
        self.assertFalse(Business.objects.filter(slug='gbi').exists())
