"""Delivery addresses, delivery areas and the steps an online order goes through."""
import re

from django.db import transaction
from django.utils import timezone

INDIAN_STATES = (
    'Andaman and Nicobar Islands', 'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chandigarh', 'Chhattisgarh',
    'Dadra and Nagar Haveli and Daman and Diu', 'Delhi', 'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jammu and Kashmir',
    'Jharkhand', 'Karnataka', 'Kerala', 'Ladakh', 'Lakshadweep', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya',
    'Mizoram', 'Nagaland', 'Odisha', 'Puducherry', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana', 'Tripura',
    'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
)

# The steps of each kind of order, in order. Staff can move an order to any later step.
FLOWS = {
    'deliver': ['placed', 'confirmed', 'packed', 'out_for_delivery', 'delivered'],
    'pickup': ['placed', 'confirmed', 'packed', 'ready_for_pickup', 'delivered'],
}
# Customers can cancel until the order has been packed, if they haven't paid online.
CUSTOMER_CANCEL_UNTIL = ('placed', 'confirmed')


class AddressError(ValueError):
    pass


def _text(value, limit):
    return str(value or '').strip()[:limit]


def clean_phone(value):
    """An Indian mobile number as 10 digits (a leading +91, 91 or 0 is dropped)."""
    digits = re.sub(r'\D', '', str(value or ''))
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    if not re.fullmatch(r'[6-9]\d{9}', digits):
        raise AddressError('Enter a 10-digit mobile number, e.g. 98765 43210.')
    return digits


def is_india(country):
    return (country or 'India').strip().lower() in ('india', 'in', 'bharat')


def clean_pincode(value, country='India'):
    pin = re.sub(r'\s', '', str(value or ''))
    if is_india(country) and not re.fullmatch(r'[1-9]\d{5}', pin):
        raise AddressError('Enter a 6-digit PIN code.')
    if not pin:
        raise AddressError('Enter the PIN or postal code.')
    return pin[:10]


def clean_address(data):
    """A delivery address from a form, checked. Raises AddressError with a plain message."""
    country = _text(data.get('country'), 60) or 'India'
    address = {
        'label': _text(data.get('label'), 20) or 'Home',
        'full_name': _text(data.get('full_name'), 120),
        'phone': clean_phone(data.get('phone')),
        'line1': _text(data.get('line1'), 200),
        'area': _text(data.get('area'), 200),
        'landmark': _text(data.get('landmark'), 200),
        'city': _text(data.get('city'), 100),
        'state': _text(data.get('state'), 80),
        'pincode': clean_pincode(data.get('pincode'), country),
        'country': country,
    }
    if not address['full_name']:
        raise AddressError('Enter the name of the person receiving the order.')
    if not address['line1']:
        raise AddressError('Enter the house, building and street.')
    if not address['city']:
        raise AddressError('Enter the town or city.')
    if is_india(country) and address['state'] not in INDIAN_STATES:
        raise AddressError('Choose the state.')
    return address


def served_patterns(business):
    return [p for p in re.split(r'[\s,;]+', business.delivery_pincodes or '') if p]


def delivers_to(business, pincode):
    """Whether the shop delivers to this PIN code. No list means everywhere.
    "7880*" covers every PIN starting with 7880."""
    patterns = served_patterns(business)
    if not patterns:
        return True
    pin = str(pincode or '').strip()
    return any(pin.startswith(p[:-1]) if p.endswith('*') else pin == p for p in patterns)


def record(order, status, user=None, note=''):
    from base.models import OrderEvent
    return OrderEvent.objects.create(order=order, status=status, note=note[:300],
                                     created_by=user if user and user.is_authenticated else None)


def next_steps(order):
    """The steps staff can move this order to now."""
    if order.order_status in ('delivered', 'cancelled'):
        return []
    flow = FLOWS['pickup' if order.delivery_method == 'pickup' else 'deliver']
    here = flow.index(order.order_status) if order.order_status in flow else 0
    return flow[here + 1:]


def move(order, status, user, note=''):
    """Move an order to a later step. Delivered also marks it delivered."""
    order.order_status = status
    fields = ['order_status']
    if status == 'delivered':
        order.isDelivered = True
        order.deliveredAt = timezone.now()
        fields += ['isDelivered', 'deliveredAt']
    order.save(update_fields=fields)
    record(order, status, user, note)


def refund_order(order):
    """Start a full refund of an order paid online. Returns a note for the timeline.
    Raises payments.PaymentError if Razorpay refuses or can't be reached."""
    from base import payments
    result = payments.refund(order.payment_id)
    return f'Refund of ₹{order.totalPrice:.2f} started ({result.get("id", "refund")}). It reaches the customer in 5-7 working days.'


def cancel(order, user, note='', require_refund=False):
    """Cancel an order: its items go back on the shelf, any coupon can be used again, and an
    online payment is refunded. With require_refund, nothing is cancelled unless the refund
    starts (a customer cancelling); otherwise a failed refund is noted for the shop to do by hand."""
    from base import payments
    from base.models import CouponRedemption, Product, StockMovement
    refund_note = ''
    if order.isPaid and order.payment_id:
        try:
            refund_note = refund_order(order)
        except payments.PaymentError as e:
            if require_refund:
                raise
            refund_note = f'The refund could not be started automatically ({e}). Refund it from the Razorpay dashboard.'
    with transaction.atomic():
        for item in order.orderitem_set.select_related('product'):
            if not item.product_id:
                continue
            product = Product.objects.select_for_update().get(pk=item.product_id)
            product.countInStock = (product.countInStock or 0) + item.qty
            product.save(update_fields=['countInStock'])
            StockMovement.objects.create(
                business_id=order.business_id, product=product, change=item.qty, stock_after=product.countInStock,
                reason='online_cancel', note=f'Online order #{order._id} cancelled', order=order,
                created_by=user if user and user.is_authenticated else None)
        # The coupon used on this order can be used again.
        if order.coupon_id:
            CouponRedemption.objects.filter(pk=order.coupon_id, business_id=order.business_id).update(is_used=False, status=True)
        order.order_status = 'cancelled'
        order.save(update_fields=['order_status'])
        record(order, 'cancelled', user, ' '.join(x for x in (note, refund_note) if x))
    return refund_note
