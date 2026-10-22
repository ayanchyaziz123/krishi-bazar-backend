"""Staff roles, per business.

A person's role comes from their Membership in the business a request is about
(see base/tenancy.py), not from a global flag:

- Owner and Manager: everything in their business.
- Cashier: the shop counter, today's sales and money owed. No profit or buying prices.
- Stock keeper: stock, receiving and suppliers. No sales or customers.
- Online orders: website orders and contact messages.

Being staff in one business gives no access to any other business.
"""
from rest_framework.permissions import BasePermission

from base.tenancy import membership_in, staff_membership

OWNER = 'owner'
MANAGER = 'manager'
CASHIER = 'cashier'
STOCK_KEEPER = 'stock'
ORDERS = 'orders'
MANAGER_ROLES = (OWNER, MANAGER)
ROLE_LABELS = {OWNER: 'Owner', MANAGER: 'Manager', CASHIER: 'Cashier', STOCK_KEEPER: 'Stock keeper', ORDERS: 'Online orders'}


def primary_membership(user):
    """The first business a person works in, used when nothing else says which one."""
    if not user or not user.is_authenticated:
        return None
    return user.memberships.select_related('business').filter(is_active=True, business__status='active').first()


def role_of(user, business=None):
    """The person's role in `business` (or their first business), or None."""
    membership = membership_in(user, business) if business is not None else primary_membership(user)
    return membership.role if membership else None


def is_manager_of(user, business):
    return role_of(user, business) in MANAGER_ROLES


def request_role(request):
    """The role for the business this staff request is about (set by the permission checks)."""
    membership = getattr(request, 'membership', None) or staff_membership(request)
    return membership.role if membership else None


def request_is_manager(request):
    return request_role(request) in MANAGER_ROLES


def _allow(*roles):
    class RolePermission(BasePermission):
        message = "You don't have access to this in the selected business."

        def has_permission(self, request, view):
            membership = staff_membership(request)
            return bool(membership) and membership.role in roles

    return RolePermission


AnyStaff = _allow(OWNER, MANAGER, CASHIER, STOCK_KEEPER, ORDERS)
CanSell = _allow(OWNER, MANAGER, CASHIER)
CanManageStock = _allow(OWNER, MANAGER, STOCK_KEEPER)
CanHandleOrders = _allow(OWNER, MANAGER, ORDERS)
IsManager = _allow(OWNER, MANAGER)
