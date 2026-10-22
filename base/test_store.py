from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from base.testing import member, shop


class StoreSettingsTests(TestCase):
    def test_everyone_reads_only_managers_change(self):
        res = APIClient().get('/api/store/')
        self.assertEqual((res.status_code, res.data['name'], res.data['accent']), (200, 'Krishi Bazar', '#059669'))
        self.assertIn(APIClient().put('/api/store/', {'name': 'X'}, format='json').status_code, (401, 403))
        manager = APIClient()
        manager.force_authenticate(member('m@s.test', 'm@s.test', 'pw'))
        res = manager.put('/api/store/', {'name': 'Sylhet Spice House', 'accent': '#7C3AED', 'location': 'Dhaka'}, format='json')
        self.assertEqual((res.data['name'], res.data['accent'], res.data['location']), ('Sylhet Spice House', '#7c3aed', 'Dhaka'))
        self.assertEqual(APIClient().get('/api/store/').data['name'], 'Sylhet Spice House')

    def test_bad_values_refused(self):
        manager = APIClient()
        manager.force_authenticate(member('m2@s.test', 'm2@s.test', 'pw'))
        self.assertEqual(manager.put('/api/store/', {'accent': 'green'}, format='json').status_code, 400)
        self.assertEqual(manager.put('/api/store/', {'name': '  '}, format='json').status_code, 400)
