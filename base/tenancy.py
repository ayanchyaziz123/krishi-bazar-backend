"""Which business a request is for.

Two kinds of request:

* Staff work (POS, stock, admin pages). The business comes from the person's
  *membership*. The X-Business header only says which of their businesses they want;
  it is checked against their memberships on every request and never trusted alone.
* The public shop (products, cart, checkout, contact form). The business comes from the
  shop the customer is visiting (the X-Business header the shop sends, or ?business=),
  falling back to the platform's first shop. Only that shop's public data is shown, and
  anything created (orders, messages) is attached to it.

Suspended businesses are treated as not found for both.
"""
from django.conf import settings
from rest_framework.exceptions import NotFound

HEADER = 'X-Business'


def _requested_slug(request):
    slug = request.headers.get(HEADER) or ''
    return slug.strip().lower()[:60]


def staff_membership(request):
    """The signed-in person's active membership for the business this request is about,
    or None. Cached on the request."""
    if hasattr(request, '_tenant_membership'):
        return request._tenant_membership
    from base.models import Membership
    membership = None
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        mine = Membership.objects.select_related('business').filter(
            user=user, is_active=True, business__status='active')
        slug = _requested_slug(request)
        if slug:
            membership = mine.filter(business__slug=slug).first()
        else:
            # No business chosen: only unambiguous when the person works in exactly one.
            first_two = list(mine[:2])
            membership = first_two[0] if len(first_two) == 1 else None
    request._tenant_membership = membership
    if membership:
        request.business = membership.business
        request.membership = membership
    return membership


def public_business(request):
    """The shop the customer is visiting. Raises 404 for an unknown or suspended shop."""
    if getattr(request, '_public_business', None) is not None:
        return request._public_business
    from base.models import Business
    slug = _requested_slug(request) or (getattr(request, 'query_params', {}).get('business') or '').strip().lower()
    slug = slug or settings.DEFAULT_BUSINESS_SLUG
    business = Business.objects.filter(slug=slug, status='active').first()
    if not business:
        raise NotFound('This shop does not exist.')
    request._public_business = business
    return business


def membership_in(user, business):
    """The person's active membership in a given business, or None."""
    if not user or not user.is_authenticated or business is None:
        return None
    from base.models import Membership
    return Membership.objects.filter(user=user, business=business, is_active=True).first()
