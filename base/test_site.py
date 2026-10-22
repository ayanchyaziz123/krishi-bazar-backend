"""The website editor: drafts, publishing, sections, pages and photos, per business."""
import io
import shutil
import tempfile

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from base.models import SiteImage, SitePage
from base.testing import member, shop

MEDIA = tempfile.mkdtemp()


def png(name='banner.png'):
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), 'green').save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def client_for(user=None, business=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    if business:
        c.credentials(HTTP_X_BUSINESS=business.slug)
    return c


@override_settings(MEDIA_ROOT=MEDIA)
class WebsiteEditorTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.biz = shop()
        self.manager = client_for(member('w@t.test', business=self.biz), self.biz)
        self.visitor = APIClient()

    def draft(self):
        return self.manager.get('/api/site/draft/').data['draft']

    def test_starts_with_todays_home_page(self):
        site = self.visitor.get('/api/site/').data
        self.assertEqual([s['type'] for s in site['sections']], ['categories', 'catalog'])
        self.assertEqual(site['menu'][0], {'label': 'Shop', 'link': '/'})
        self.assertEqual((site['seo'], site['social']['facebook']), ({'title': '', 'description': ''}, ''))

    def test_draft_is_private_until_published(self):
        content = self.draft()
        content['sections'].insert(0, {'type': 'hero', 'title': 'Winter harvest', 'button_label': 'Shop', 'button_link': '/priceRange'})
        content['announcement'] = {'enabled': True, 'text': 'Free delivery over ₹1,000', 'link': '/contact'}
        content['seo'] = {'title': 'Krishi Bazar · Fresh food', 'description': 'Rice, spices and tea from local farms.'}
        content['social'] = {'facebook': 'https://facebook.com/krishibazar'}
        res = self.manager.put('/api/site/draft/', content, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data['unpublished'])
        self.assertEqual(self.visitor.get('/api/site/').data['sections'][0]['type'], 'categories')
        self.manager.post('/api/site/publish/')
        live = self.visitor.get('/api/site/').data
        self.assertEqual((live['sections'][0]['title'], live['announcement']['text']), ('Winter harvest', 'Free delivery over ₹1,000'))
        self.assertEqual((live['seo']['title'], live['social']['facebook']), ('Krishi Bazar · Fresh food', 'https://facebook.com/krishibazar'))
        self.assertFalse(self.manager.get('/api/site/draft/').data['unpublished'])

    def test_discard_goes_back_to_the_live_site(self):
        content = self.draft()
        content['sections'] = []
        self.manager.put('/api/site/draft/', content, format='json')
        self.manager.post('/api/site/discard/')
        self.assertEqual(len(self.draft()['sections']), 2)

    def test_bad_content_is_refused(self):
        content = self.draft()
        for bad in ({'type': 'video'}, {'type': 'hero', 'button_link': 'javascript:alert(1)'},
                    {'type': 'promo', 'image': 'data:image/png;base64,xx'},
                    {'type': 'promo', 'image': '/images/business/999/site/x.png'}):
            body = {**content, 'sections': [bad]}
            self.assertEqual(self.manager.put('/api/site/draft/', body, format='json').status_code, 400, bad)
        two_catalogs = {**content, 'sections': [{'type': 'catalog'}, {'type': 'catalog'}]}
        self.assertEqual(self.manager.put('/api/site/draft/', two_catalogs, format='json').status_code, 400)
        bad_social = {**content, 'social': {'instagram': 'javascript:alert(1)'}}
        self.assertEqual(self.manager.put('/api/site/draft/', bad_social, format='json').status_code, 400)

    def test_website_colour_is_for_the_shop_only(self):
        content = self.draft()
        self.assertEqual(content['theme'], {'accent': self.biz.accent, 'header': 'solid', 'logo': '', 'show_name': True})  # starts as the store colour
        content['theme'] = {'accent': '#2563EB', 'header': 'gradient'}
        res = self.manager.put('/api/site/draft/', content, format='json')
        self.assertEqual(res.data['draft']['theme'], {'accent': '#2563eb', 'header': 'gradient', 'logo': '', 'show_name': True})
        self.manager.post('/api/site/publish/')
        self.assertEqual(self.visitor.get('/api/site/').data['theme']['accent'], '#2563eb')
        self.biz.refresh_from_db()
        self.assertNotEqual(self.biz.accent, '#2563eb')   # the admin and receipts keep the store colour
        for bad in ({'accent': 'red'}, {'accent': '#12345g'}, {'accent': 'javascript:x'}):
            self.assertEqual(self.manager.put('/api/site/draft/', {**content, 'theme': bad}, format='json').status_code, 400)
        odd = self.manager.put('/api/site/draft/', {**content, 'theme': {'accent': '', 'header': 'rainbow'}}, format='json').data['draft']['theme']
        self.assertEqual(odd, {'accent': self.biz.accent, 'header': 'solid', 'logo': '', 'show_name': True})

    def test_store_colour_and_logo_are_set_in_settings(self):
        url = self.manager.post('/api/site/images/', {'image': png('logo.png')}, format='multipart').data['url']
        res = self.manager.put('/api/store/', {'accent': '#7C3AED', 'logo': url}, format='json')
        self.assertEqual((res.status_code, res.data['accent'], res.data['logo']), (200, '#7c3aed', url))
        self.assertEqual(self.manager.put('/api/store/', {'logo': 'https://evil.example/x.png'}, format='json').status_code, 400)
        self.assertEqual(self.manager.put('/api/store/', {'logo': ''}, format='json').data['logo'], '')
        # The website's own theme is untouched.
        self.assertEqual(self.visitor.get('/api/site/').data['theme']['accent'], '')

    def test_website_logo_is_for_the_shop_only(self):
        url = self.manager.post('/api/site/images/', {'image': png('logo.png')}, format='multipart').data['url']
        content = self.draft()
        content['theme'] = {**content['theme'], 'logo': url, 'show_name': False}
        self.assertEqual(self.manager.put('/api/site/draft/', content, format='json').status_code, 200)
        self.manager.post('/api/site/publish/')
        live = self.visitor.get('/api/site/').data['theme']
        self.assertEqual((live['logo'], live['show_name']), (url, False))
        self.assertEqual(self.visitor.get('/api/store/').data['logo'], '')     # store logo unchanged
        for bad in ('https://example.com/logo.png', '/images/business/999/site/x.png', '/images/business/1/../x.png', 'javascript:x'):
            body = {**content, 'theme': {**content['theme'], 'logo': bad}}
            self.assertEqual(self.manager.put('/api/site/draft/', body, format='json').status_code, 400, bad)

    def test_banner_slider(self):
        url = self.manager.post('/api/site/images/', {'image': png()}, format='multipart').data['url']
        content = {**self.draft(), 'sections': [{'type': 'slides', 'slides': [
            {'image': url, 'link': '/priceRange', 'alt': 'Pickle week'}, {'image': '', 'link': '/'}]}]}
        res = self.manager.put('/api/site/draft/', content, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['draft']['sections'][0]['slides'], [{'image': url, 'link': '/priceRange', 'alt': 'Pickle week'}])
        for bad in ([{'image': url, 'link': 'javascript:x'}], [{'image': 'data:x'}], [{'image': url}] * 9):
            body = {**content, 'sections': [{'type': 'slides', 'slides': bad}]}
            self.assertEqual(self.manager.put('/api/site/draft/', body, format='json').status_code, 400)

    def test_only_managers_edit(self):
        self.assertIn(self.visitor.put('/api/site/draft/', {}, format='json').status_code, (401, 403))
        self.assertIn(self.visitor.post('/api/site/publish/').status_code, (401, 403))
        cashier = client_for(member('c@t.test', business=self.biz, role='cashier'), self.biz)
        for method, url in (('get', '/api/site/draft/'), ('post', '/api/site/publish/'), ('get', '/api/site/pages/'),
                            ('post', '/api/site/images/')):
            self.assertEqual(getattr(cashier, method)(url).status_code, 403, url)

    def test_pages_are_cleaned_and_hidden_until_published(self):
        res = self.manager.post('/api/site/pages/', {
            'title': 'About us', 'body': '<h2>Our farms</h2><p onclick="steal()">Hello<script>alert(1)</script></p>'
                                         '<a href="javascript:x()">bad</a><a href="/contact">Contact</a>'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['slug'], 'about-us')
        body = res.data['body']
        self.assertNotIn('script', body); self.assertNotIn('onclick', body); self.assertNotIn('javascript', body)
        self.assertIn('<h2>Our farms</h2>', body); self.assertIn('href="/contact"', body)
        self.assertEqual(self.visitor.get('/api/site/pages/by-slug/about-us/').status_code, 404)
        self.assertEqual(self.manager.get('/api/site/pages/by-slug/about-us/').status_code, 200)  # manager preview
        self.manager.patch(f"/api/site/pages/{res.data['id']}/", {'published': True}, format='json')
        self.assertEqual(self.visitor.get('/api/site/pages/by-slug/about-us/').data['title'], 'About us')
        self.assertEqual([p['slug'] for p in self.visitor.get('/api/site/').data['pages']], ['about-us'])
        dup = self.manager.post('/api/site/pages/', {'title': 'About us'}, format='json')
        self.assertEqual(dup.status_code, 400)

    def test_image_upload(self):
        res = self.manager.post('/api/site/images/', {'image': png()}, format='multipart')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertTrue(res.data['url'].startswith(f'/images/business/{self.biz.pk}/site/'))
        self.assertEqual(SiteImage.objects.get().business, self.biz)
        content = {**self.draft(), 'sections': [{'type': 'promo', 'title': 'Sale', 'image': res.data['url']}]}
        self.assertEqual(self.manager.put('/api/site/draft/', content, format='json').status_code, 200)
        bad = SimpleUploadedFile('x.svg', b'<svg onload=alert(1)>', content_type='image/svg+xml')
        self.assertEqual(self.manager.post('/api/site/images/', {'image': bad}, format='multipart').status_code, 400)
        renamed = SimpleUploadedFile('fake.png', b'not really a picture', content_type='image/png')
        self.assertEqual(self.manager.post('/api/site/images/', {'image': renamed}, format='multipart').status_code, 400)


@override_settings(MEDIA_ROOT=MEDIA)
class WebsitePerBusinessTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.a, self.b = shop(), shop('spice-house', 'Spice House')
        self.A = client_for(member('a@t.test', business=self.a), self.a)
        self.B = client_for(member('b@t.test', business=self.b), self.b)

    def test_each_shop_has_its_own_website(self):
        draft = self.B.get('/api/site/draft/').data['draft']
        draft['announcement'] = {'enabled': True, 'text': 'Spice week', 'link': ''}
        self.B.put('/api/site/draft/', draft, format='json')
        self.B.post('/api/site/publish/')
        self.assertEqual(client_for(business=self.b).get('/api/site/').data['announcement']['text'], 'Spice week')
        self.assertFalse(client_for(business=self.a).get('/api/site/').data['announcement']['enabled'])
        self.assertFalse(APIClient().get('/api/site/').data['announcement']['enabled'])  # the default shop is A
        self.assertFalse(self.A.get('/api/site/draft/').data['draft']['announcement']['enabled'])

    def test_theme_colour_changes_only_its_own_business(self):
        draft = self.B.get('/api/site/draft/').data['draft']
        draft['theme'] = {'accent': '#7c3aed', 'header': 'solid'}
        self.B.put('/api/site/draft/', draft, format='json')
        self.B.post('/api/site/publish/')
        self.assertEqual(client_for(business=self.b).get('/api/site/').data['theme']['accent'], '#7c3aed')
        self.assertNotEqual(client_for(business=self.a).get('/api/site/').data['theme']['accent'], '#7c3aed')

    def test_pages_and_photos_stay_in_their_business(self):
        page = self.B.post('/api/site/pages/', {'title': 'About us', 'body': '<p>Spice</p>', 'published': True}, format='json').data
        # The same address is free in another business.
        self.assertEqual(self.A.post('/api/site/pages/', {'title': 'About us', 'body': '<p>Krishi</p>'}, format='json').status_code, 201)
        self.assertEqual(client_for(business=self.b).get('/api/site/pages/by-slug/about-us/').data['body'], '<p>Spice</p>')
        self.assertEqual(client_for(business=self.a).get('/api/site/pages/by-slug/about-us/').status_code, 404)  # A's is a draft
        for method in ('get', 'patch', 'delete'):
            self.assertEqual(getattr(self.A, method)(f"/api/site/pages/{page['id']}/", {'title': 'x'}, format='json').status_code, 404)
        self.assertEqual(SitePage.objects.get(pk=page['id']).title, 'About us')
        self.assertEqual({p['title'] for p in self.A.get('/api/site/pages/').data}, {'About us'})
        self.assertEqual(len(self.A.get('/api/site/pages/').data), 1)
        # A photo uploaded by B can't be used in A's website.
        url = self.B.post('/api/site/images/', {'image': png()}, format='multipart').data['url']
        draft = self.A.get('/api/site/draft/').data['draft']
        draft['sections'] = [{'type': 'promo', 'title': 'x', 'image': url}]
        self.assertEqual(self.A.put('/api/site/draft/', draft, format='json').status_code, 400)
        page_with_photo = self.A.post('/api/site/pages/', {'title': 'Photo', 'body': f'<img src="{url}">'}, format='json').data
        self.assertNotIn(url, page_with_photo['body'])

    def test_manager_of_one_shop_cannot_preview_anothers_drafts(self):
        self.B.post('/api/site/pages/', {'title': 'Secret plans', 'body': '<p>x</p>'}, format='json')
        a_manager_at_b = client_for(member('x@t.test', business=self.a))
        a_manager_at_b.credentials(HTTP_X_BUSINESS='spice-house')
        self.assertEqual(a_manager_at_b.get('/api/site/pages/by-slug/secret-plans/').status_code, 404)
        self.assertEqual(a_manager_at_b.get('/api/site/draft/').status_code, 403)
