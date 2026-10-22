from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from base.testing import member, shop

from base.models import Contact


class ContactMessageTests(TestCase):
    def setUp(self):
        self.manager = APIClient()
        self.manager.force_authenticate(member('cm@t.test', 'cm@t.test', 'pw'))

    def send(self, **body):
        base = {'first_name': 'Rina', 'email': 'rina@example.com', 'message': 'Do you deliver to Moulvibazar?'}
        base.update(body)
        return APIClient().post('/api/contact/create/', base, format='json')

    def test_visitor_sends_and_staff_reply(self):
        self.assertEqual(self.send().status_code, 201)
        listing = self.manager.get('/api/contact/').data
        self.assertEqual((listing['waiting'], listing['messages'][0]['name']), (1, 'Rina'))
        mid = listing['messages'][0]['id']
        res = self.manager.patch(f'/api/contact/{mid}/', {'replied': True, 'reply_note': 'Emailed delivery prices'}, format='json')
        self.assertEqual((res.data['replied'], res.data['reply_note']), (True, 'Emailed delivery prices'))
        self.assertEqual(self.manager.get('/api/contact/').data['waiting'], 0)
        self.assertEqual(len(self.manager.get('/api/contact/?status=waiting').data['messages']), 0)

    def test_bad_messages_are_refused(self):
        self.assertEqual(self.send(first_name='').status_code, 400)
        self.assertEqual(self.send(email='', phone='').status_code, 400)
        self.assertEqual(self.send(email='not-an-email').status_code, 400)
        self.assertEqual(self.send(message='x' * 900).status_code, 400)
        self.assertEqual(self.send(email='', phone='01711000000').status_code, 201)

    def test_bots_filling_the_hidden_field_are_ignored(self):
        self.assertEqual(self.send(website='spam.example').status_code, 201)
        self.assertEqual(Contact.objects.count(), 0)

    def test_only_staff_who_handle_orders_can_read(self):
        customer = APIClient()
        customer.force_authenticate(User.objects.create_user('cu@t.test', 'cu@t.test', 'pw'))
        self.assertEqual(customer.get('/api/contact/').status_code, 403)
        self.assertIn(APIClient().get('/api/contact/').status_code, (401, 403))
        self.manager.post('/api/users/staff/', {'name': 'Till', 'email': 'till@t.test', 'password': 'longpassword', 'role': 'cashier'}, format='json')
        cashier = APIClient()
        cashier.force_authenticate(User.objects.get(username='till@t.test'))
        self.assertEqual(cashier.get('/api/contact/').status_code, 403)
        self.manager.post('/api/users/staff/', {'name': 'Web', 'email': 'web@t.test', 'password': 'longpassword', 'role': 'orders'}, format='json')
        orders = APIClient()
        orders.force_authenticate(User.objects.get(username='web@t.test'))
        self.assertEqual(orders.get('/api/contact/').status_code, 200)
