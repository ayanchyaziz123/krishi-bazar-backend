import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from base.testing import member, shop

from base.models import Product, StockMovement

MEDIA = tempfile.mkdtemp()
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 50


@override_settings(MEDIA_ROOT=MEDIA)
class ProductFormTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.manager = APIClient()
        self.manager.force_authenticate(member('pm@t.test', 'pm@t.test', 'pw'))

    def test_nothing_is_saved_until_the_form_is_complete(self):
        self.assertEqual(self.manager.post('/api/products/create/', {}, format='json').status_code, 400)
        self.assertEqual(self.manager.post('/api/products/create/', {'name': 'Honey'}, format='json').status_code, 400)
        self.assertEqual(Product.objects.count(), 0)

    def test_create_with_opening_stock(self):
        res = self.manager.post('/api/products/create/', {
            'name': 'Sundarbans Honey', 'category': 'Honey', 'price': '650', 'weight': '500 g jar',
            'countInStock': '12', 'code': '801', 'sell_online': True, 'sell_in_shop': True,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        product = Product.objects.get()
        self.assertEqual((product.name, product.countInStock, product.code), ('Sundarbans Honey', 12, '801'))
        self.assertEqual(StockMovement.objects.get().reason, 'correction')
        dup = self.manager.post('/api/products/create/', {'name': 'X', 'price': '1', 'code': '801'}, format='json')
        self.assertEqual(dup.status_code, 400)

    def test_bad_values_are_refused(self):
        for body in ({'name': 'A', 'price': '-5'}, {'name': 'A', 'price': '5', 'offer_percentage': 150},
                     {'name': 'A', 'price': '5', 'countInStock': '1.5'}):
            self.assertEqual(self.manager.post('/api/products/create/', body, format='json').status_code, 400, body)

    def test_image_upload_is_for_managers_only(self):
        product = Product.objects.create(business=shop(), name='Tea', price=Decimal('100'))
        photo = lambda: SimpleUploadedFile('tea.png', PNG, content_type='image/png')
        customer = APIClient()
        customer.force_authenticate(User.objects.create_user('c@t.test', 'c@t.test', 'pw'))
        self.assertEqual(customer.post('/api/products/upload/', {'product_id': product._id, 'image': photo()},
                                       format='multipart').status_code, 403)
        self.assertIn(APIClient().post('/api/products/upload/', {'product_id': product._id, 'image': photo()},
                                       format='multipart').status_code, (401, 403))
        res = self.manager.post('/api/products/upload/', {'product_id': product._id, 'image2': photo()}, format='multipart')
        self.assertEqual((res.status_code, res.data['field']), (200, 'image2'))
        self.assertTrue(res.data['url'].endswith('.png'))
        bad = self.manager.post('/api/products/upload/', {'product_id': product._id,
                                'image': SimpleUploadedFile('x.exe', b'MZ', content_type='application/octet-stream')}, format='multipart')
        self.assertEqual(bad.status_code, 400)


class AdminProductListTests(TestCase):
    def setUp(self):
        self.manager = APIClient()
        self.manager.force_authenticate(member('pl@t.test', 'pl@t.test', 'pw'))
        for i in range(12):
            Product.objects.create(business=shop(), name=f'Rice {i}', category='Rice', price=Decimal('10'), code=f'R{i}')
        Product.objects.create(business=shop(), name='Loose chilli', category='Spices', price=Decimal('5'), sell_online=False)
        Product.objects.create(business=shop(), name='Gift box', category='Tea', price=Decimal('5'), sell_in_shop=False)

    def get(self, query):
        return self.manager.get(f'/api/products/?scope=all&{query}').data

    def test_filters_page_size_and_code_search(self):
        self.assertEqual(self.get('page_size=50')['count'], 14)
        self.assertEqual(len(self.get('page_size=50')['products']), 14)
        self.assertEqual(self.get('category=rice&page_size=50')['count'], 12)
        self.assertEqual([p['name'] for p in self.get('channel=shop_only')['products']], ['Loose chilli'])
        self.assertEqual([p['name'] for p in self.get('channel=online_only')['products']], ['Gift box'])
        self.assertEqual([p['name'] for p in self.get('keyword=R7')['products']], ['Rice 7'])
        self.assertEqual(len(self.get('')['products']), 8)  # the shop's default page
