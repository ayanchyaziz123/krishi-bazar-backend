"""A store's name, details and colour. The public shop reads the business it is
showing; managers change only their own business's settings."""
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from base.roles import MANAGER_ROLES
from base.tenancy import public_business, staff_membership

HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
FIELDS = {'name': 80, 'tagline': 160, 'location': 80, 'address': 255, 'phone': 40, 'email': 120, 'receipt_footer': 160}


def _shipping(b):
    """Delivery and pick-up rules, public: the checkout shows them to customers."""
    return {
        'delivery_enabled': b.delivery_enabled, 'pickup_enabled': b.pickup_enabled,
        'delivery_fee': str(b.delivery_fee), 'free_delivery_over': None if b.free_delivery_over is None else str(b.free_delivery_over),
        'min_order': str(b.min_order), 'tax_rate': str(b.tax_rate),
        'delivery_time': b.delivery_time, 'pickup_time': b.pickup_time, 'delivery_pincodes': b.delivery_pincodes,
    }


def _amount(value, label, allow_empty=False, most=Decimal('1000000')):
    if allow_empty and (value is None or str(value).strip() == ''):
        return None
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f'{label} must be a number.')
    if amount < 0 or amount > most:
        raise ValueError(f'{label} must be between 0 and {most:g}.')
    return amount.quantize(Decimal('0.01'))


def _apply_shipping(business, data):
    """Check and set the delivery settings sent by a manager. Raises ValueError."""
    for flag in ('delivery_enabled', 'pickup_enabled'):
        if flag in data:
            setattr(business, flag, bool(data[flag]))
    if 'delivery_fee' in data:
        business.delivery_fee = _amount(data['delivery_fee'], 'Delivery fee')
    if 'free_delivery_over' in data:
        business.free_delivery_over = _amount(data['free_delivery_over'], 'Free delivery limit', allow_empty=True)
    if 'min_order' in data:
        business.min_order = _amount(data['min_order'], 'Minimum order')
    if 'tax_rate' in data:
        business.tax_rate = _amount(data['tax_rate'], 'Tax rate', most=Decimal('100'))
    for field, limit in (('delivery_time', 80), ('pickup_time', 80)):
        if field in data:
            setattr(business, field, str(data[field] or '').strip()[:limit])
    if 'delivery_pincodes' in data:
        patterns = [p for p in re.split(r'[\s,;]+', str(data['delivery_pincodes'] or '')) if p]
        bad = [p for p in patterns if not re.fullmatch(r'\d{1,6}\*|\d{6}', p)]
        if bad:
            raise ValueError(f'Use 6-digit PIN codes, or the start of one with * (e.g. 7880*). Not valid: {", ".join(bad[:5])}')
        business.delivery_pincodes = ', '.join(patterns)[:5000]
    if not business.delivery_enabled and not business.pickup_enabled:
        raise ValueError('Keep at least one of delivery or store pick-up on, or customers can\'t order.')


def _gst(b):
    return {'mode': b.gst_mode, 'gstin': b.gstin, 'state': b.state, 'prices_include_gst': b.prices_include_gst}


def _apply_gst(business, data):
    """Check and set the GST registration sent by a manager. Raises ValueError."""
    from base.gst import STATE_CODES, gstin_ok
    mode = data.get('mode', business.gst_mode)
    if mode not in ('none', 'regular', 'composition'):
        raise ValueError('Choose not registered, regular GST or composition scheme.')
    state = str(data.get('state', business.state) or '').strip()
    if state and state not in STATE_CODES:
        raise ValueError('Choose the state from the list.')
    gstin = str(data.get('gstin', business.gstin) or '').strip().upper()
    if mode != 'none':
        if not state:
            raise ValueError("Choose your shop's state.")
        if not gstin_ok(gstin):
            raise ValueError('That GSTIN is not valid. It has 15 characters, like 27AAPFU0939F1ZV.')
        if gstin[:2] != STATE_CODES[state]:
            raise ValueError(f'This GSTIN is registered in another state (code {gstin[:2]}), not {state}.')
    business.gst_mode, business.state, business.gstin = mode, state, gstin
    if 'prices_include_gst' in data:
        business.prices_include_gst = bool(data['prices_include_gst'])


def _json(b):
    return {**{f: getattr(b, f) for f in FIELDS}, 'slug': b.slug, 'accent': b.accent, 'gst': _gst(b),
            'logo': b.logo.url if b.logo else '', 'shipping': _shipping(b),
            'updatedAt': b.updated_at.isoformat() if b.updated_at else None}


@api_view(['GET', 'PUT'])
def store(request):
    if request.method == 'GET':
        # Staff see the business they are working in; visitors see the shop they visit.
        membership = staff_membership(request) if request.headers.get('X-Business') else None
        return Response(_json(membership.business if membership else public_business(request)))

    membership = staff_membership(request)
    if not membership or membership.role not in MANAGER_ROLES:
        return Response({'detail': 'Only managers can change the store settings.'}, status=status.HTTP_403_FORBIDDEN)
    business = membership.business
    data = request.data
    if 'name' in data and not (data.get('name') or '').strip():
        return Response({'detail': 'The store needs a name.'}, status=status.HTTP_400_BAD_REQUEST)
    if 'accent' in data and not HEX.match(str(data.get('accent') or '')):
        return Response({'detail': 'Choose a colour like #059669.'}, status=status.HTTP_400_BAD_REQUEST)
    for field, limit in FIELDS.items():
        if field in data:
            setattr(business, field, (data.get(field) or '').strip()[:limit])
    if 'accent' in data:
        business.accent = data['accent'].lower()
    if 'logo' in data:
        # The store logo (admin, counter, receipts): a photo uploaded to this business, or none.
        logo = str(data.get('logo') or '').strip()
        prefix = f'{settings.MEDIA_URL}business/{business.pk}/'
        if logo and not (logo.startswith(prefix) and '..' not in logo):
            return Response({'detail': 'Upload the logo here: it must be a photo uploaded to your store.'}, status=status.HTTP_400_BAD_REQUEST)
        business.logo.name = logo[len(settings.MEDIA_URL):] if logo else None
    if isinstance(data.get('gst'), dict):
        try:
            _apply_gst(business, data['gst'])
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(data.get('shipping'), dict):
        try:
            _apply_shipping(business, data['shipping'])
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    business.save()
    return Response(_json(business))
