"""Phase 3: memberships, the business switcher, ownership and paused access."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from base.models import Business, Membership
from base.testing import member, shop


def client_for(user, business=None):
    c = APIClient()
    c.force_authenticate(user)
    if business:
        c.credentials(HTTP_X_BUSINESS=business.slug)
    return c


class MembershipTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b = shop(), shop('spice-house', 'Spice House')
        self.owner = member('owner@t.test', business=self.a, role='owner')
        self.a.owner = self.owner
        self.a.save()
        self.manager = member('mgr@t.test', business=self.a, role='manager')
        self.cashier = member('till@t.test', business=self.a, role='cashier')
        self.O, self.M = client_for(self.owner, self.a), client_for(self.manager, self.a)

    def update(self, client, user, **data):
        body = {'name': user.first_name or user.email, 'email': user.email, **data}
        return client.put(f'/api/users/update/{user.id}/', body, format='json')

    # ------------------------------------------------------------ switcher

    def test_business_list_shows_each_role_and_follows_changes(self):
        Membership.objects.create(user=self.cashier, business=self.b, role='manager')
        c = client_for(self.cashier)
        self.assertEqual({b['slug']: b['role'] for b in c.get('/api/users/businesses/').data},
                         {'krishi-bazar': 'cashier', 'spice-house': 'manager'})
        self.update(self.M, self.cashier, role='stock')
        self.assertEqual({b['slug']: b['role'] for b in c.get('/api/users/businesses/').data},
                         {'krishi-bazar': 'stock', 'spice-house': 'manager'})
        self.b.status = 'suspended'
        self.b.save()
        self.assertEqual([b['slug'] for b in c.get('/api/users/businesses/').data], ['krishi-bazar'])
        self.assertEqual(APIClient().get('/api/users/businesses/').status_code, 401)

    def test_switching_business_switches_permissions(self):
        Membership.objects.create(user=self.cashier, business=self.b, role='manager')
        in_a, in_b = client_for(self.cashier, self.a), client_for(self.cashier, self.b)
        self.assertEqual(in_a.get('/api/users/').status_code, 403)
        self.assertEqual(in_b.get('/api/users/').status_code, 200)
        self.assertEqual(in_a.get('/api/inventory/sales/').status_code, 200)

    def test_role_change_applies_on_the_next_request(self):
        till = client_for(self.cashier, self.a)
        self.assertEqual(till.get('/api/inventory/sales/').status_code, 200)
        self.update(self.M, self.cashier, role='orders')
        self.assertEqual(till.get('/api/inventory/sales/').status_code, 403)
        self.assertEqual(till.get('/api/orders/').status_code, 200)

    # ------------------------------------------------------------ ownership

    def test_owner_hands_over_the_business(self):
        res = self.update(self.O, self.manager, role='owner')
        self.assertEqual(res.status_code, 200, res.data)
        self.a.refresh_from_db()
        self.assertEqual(self.a.owner, self.manager)
        roles = dict(Membership.objects.filter(business=self.a).values_list('user__email', 'role'))
        self.assertEqual(roles, {'owner@t.test': 'manager', 'mgr@t.test': 'owner', 'till@t.test': 'cashier'})
        self.assertEqual(Membership.objects.filter(business=self.a, role='owner').count(), 1)
        # The old owner is now a manager and can't take it back.
        self.assertEqual(self.update(self.O, self.owner, role='owner').status_code, 400)

    def test_only_the_owner_hands_over_and_only_to_active_staff(self):
        self.assertEqual(self.update(self.M, self.manager, role='owner').status_code, 400)   # own role
        self.assertEqual(self.update(self.M, self.cashier, role='owner').status_code, 403)
        self.assertEqual(self.update(self.M, self.owner, role='manager').status_code, 403)
        customer = User.objects.create_user('c@t.test', 'c@t.test', 'pw')
        from base.models import Order
        Order.objects.create(business=self.a, user=customer, totalPrice=0)
        self.assertEqual(self.update(self.O, customer, role='owner').status_code, 400)
        self.update(self.O, self.cashier, active=False)
        self.assertEqual(self.update(self.O, self.cashier, role='owner').status_code, 400)
        self.a.refresh_from_db()
        self.assertEqual(self.a.owner, self.owner)

    def test_owner_cannot_demote_or_remove_themselves(self):
        self.assertEqual(self.update(self.O, self.owner, role='manager').status_code, 400)
        self.assertEqual(self.O.delete(f'/api/users/delete/{self.owner.id}/').status_code, 400)
        self.assertEqual(self.M.delete(f'/api/users/delete/{self.owner.id}/').status_code, 400)
        self.assertEqual(Membership.objects.get(user=self.owner, business=self.a).role, 'owner')

    def test_staff_cannot_change_their_own_role(self):
        self.assertEqual(self.update(self.M, self.manager, role='cashier').status_code, 400)
        self.assertEqual(self.update(self.M, self.manager, role='manager').status_code, 200)  # unchanged is fine

    # ------------------------------------------------------------ paused access

    def test_paused_staff_lose_access_but_keep_their_place(self):
        till = client_for(self.cashier, self.a)
        res = self.update(self.M, self.cashier, active=False)
        self.assertEqual((res.status_code, res.data['role'], res.data['active']), (200, 'cashier', False))
        self.assertEqual(till.get('/api/inventory/sales/').status_code, 403)
        self.assertEqual(client_for(self.cashier).get('/api/users/businesses/').data, [])
        listed = {u['email']: (u['role'], u['active']) for u in self.M.get('/api/users/').data}
        self.assertEqual(listed['till@t.test'], ('cashier', False))
        # A role change while paused keeps them paused; restoring brings access back.
        self.update(self.M, self.cashier, role='stock')
        self.assertFalse(Membership.objects.get(user=self.cashier, business=self.a).is_active)
        self.update(self.M, self.cashier, active=True)
        self.assertEqual(till.get('/api/inventory/products/').status_code, 200)
        self.assertEqual(Membership.objects.get(user=self.cashier, business=self.a).role, 'stock')

    def test_pause_rules(self):
        self.assertEqual(self.update(self.M, self.manager, active=False).status_code, 400)
        self.assertEqual(self.update(self.M, self.owner, active=False).status_code, 400)
        customer = User.objects.create_user('c@t.test', 'c@t.test', 'pw')
        from base.models import Order
        Order.objects.create(business=self.a, user=customer, totalPrice=0)
        self.assertEqual(self.update(self.M, customer, active=False).status_code, 400)
        cashier_client = client_for(self.cashier, self.a)
        self.assertEqual(self.update(cashier_client, self.manager, active=False).status_code, 403)

    def test_pausing_in_one_business_leaves_the_other(self):
        Membership.objects.create(user=self.cashier, business=self.b, role='manager')
        self.update(self.M, self.cashier, active=False)
        self.assertEqual(client_for(self.cashier, self.b).get('/api/users/').status_code, 200)
        self.assertEqual([b['slug'] for b in client_for(self.cashier).get('/api/users/businesses/').data], ['spice-house'])
        # With one business left, no choice is needed.
        self.assertEqual(client_for(self.cashier).get('/api/inventory/products/').status_code, 200)

    def test_paused_person_cannot_sign_in_to_admin_role(self):
        self.update(self.M, self.cashier, active=False)
        login = APIClient().post('/api/users/login/', {'username': 'till@t.test', 'password': 'pw'}, format='json').data
        self.assertEqual((login['role'], login['businesses']), (None, []))
