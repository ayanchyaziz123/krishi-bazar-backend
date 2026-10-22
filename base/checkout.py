"""Checkout: check a cart and price it, then (once it is paid online) create the order.

1. prepare()   checks everything (items, stock, delivery, address, coupon, minimum order) and
               works out the prices. It reserves nothing and creates no order.
2. The customer pays that exact amount online (base/payments.py).
3. finish()    after the payment is verified: locks the stock and the coupon, creates the paid
               order at the prices that were charged, takes the stock, and uses the coupon.
               If that is no longer possible (sold out meanwhile), the payment is refunded.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from base import payments
from base.delivery import AddressError, clean_address, delivers_to, record as record_event
from base.models import CouponRedemption, CustomerAddress, Order, OrderItem, PaymentAttempt, Product, ShippingAddress
from base import gst
from base.pricing import money, online_unit_price, order_totals


class CheckoutError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coupon(business, user, coupon_id, lock=False):
    """A coupon this shop gave this customer and not used yet, or None."""
    if not coupon_id:
        return None
    rows = CouponRedemption.objects.select_for_update() if lock else CouponRedemption.objects
    return rows.filter(business=business, id=_int(coupon_id), user_id=user, is_used=False).first()


def _address(shop, user, data):
    """A saved address of this customer in this shop (addressId), or one typed at checkout."""
    address_id = data.get('addressId')
    if address_id:
        saved = CustomerAddress.objects.filter(business=shop, user=user, pk=_int(address_id)).first()
        if not saved:
            raise AddressError('That saved address was not found. Choose or add another address.')
        return {f: getattr(saved, f) for f in ('full_name', 'phone', 'line1', 'area', 'landmark', 'city', 'state', 'pincode', 'country')}
    raw = dict(data.get('shippingAddress') or {})
    # Older checkout pages send address / postalCode; the address form sends line1 / pincode.
    raw.setdefault('line1', raw.get('address'))
    raw.setdefault('pincode', raw.get('postalCode'))
    raw.setdefault('full_name', raw.get('name') or user.first_name or user.email)
    return clean_address(raw)


def prepare(shop, user, data):
    """Check a checkout and price it. Returns what will be paid for and ordered."""
    delivery = data.get('deliveryMethod') or 'deliver'
    if delivery not in ('deliver', 'pickup'):
        raise CheckoutError('Choose delivery or store pick-up.')
    pickup = delivery == 'pickup'
    if pickup and not shop.pickup_enabled:
        raise CheckoutError('This shop is not taking pick-up orders right now.')
    if not pickup and not shop.delivery_enabled:
        raise CheckoutError('This shop is not delivering right now. Choose store pick-up.')
    if pickup:
        # Collected from the shop: the shop's own address is kept on the order.
        address = {'full_name': user.first_name or user.email, 'phone': '', 'line1': f'Pick up at {shop.name}',
                   'area': '', 'landmark': '', 'city': shop.address or shop.location or '-', 'state': '', 'pincode': '-', 'country': '-'}
    else:
        try:
            address = _address(shop, user, data)
        except AddressError as e:
            raise CheckoutError(str(e))
        if not delivers_to(shop, address['pincode']):
            raise CheckoutError(f"Sorry, we don't deliver to PIN {address['pincode']} yet. Choose store pick-up or another address.")

    # Quantities per item, so the same item listed twice is counted once.
    wanted = {}
    for i in data.get('orderItems') or []:
        pid, qty = _int(i.get('product')), _qty(i.get('qty'))
        if pid is None or qty is None:
            raise CheckoutError('One of the items in your cart is not valid.')
        if qty <= 0:
            raise CheckoutError('Choose a quantity for every item.')
        wanted[pid] = wanted.get(pid, Decimal('0')) + qty
    if not wanted:
        raise CheckoutError('Your cart is empty.')

    products = {p._id: p for p in Product.objects.filter(business=shop, _id__in=wanted)}
    items, items_price = [], Decimal('0')
    for pid, qty in wanted.items():
        product = products.get(pid)
        _check_available(product, qty)
        _check_steps(product, qty)
        unit = online_unit_price(product)
        items.append({'product': pid, 'qty': str(qty), 'price': str(unit),
                      'gst_rate': str(product.gst_rate if gst.charges_gst(shop) else 0), 'hsn_code': product.hsn_code})
        items_price += unit * qty
    if items_price < shop.min_order:
        raise CheckoutError(f'The minimum order is ₹{shop.min_order:g}. Add a little more to your cart.')
    with transaction.atomic():
        coupon = _coupon(shop, user, data.get('coupon_id'))
    if data.get('coupon_id') and not coupon:
        raise CheckoutError('That coupon is not valid for your account or has already been used.')
    discount = Decimal(str(coupon.total_discount)) if coupon else Decimal('0')
    totals = order_totals(items_price, discount, pickup=pickup, business=shop)
    tax = {'mode': shop.gst_mode, 'cgst': '0', 'sgst': '0', 'igst': '0', 'included': gst.charges_gst(shop) and shop.prices_include_gst}
    if shop.gst_mode != 'none':
        # Registered shops charge GST per item (composition: none); the flat online tax is not used.
        totals = order_totals(items_price, discount, pickup=pickup, business=shop, flat_tax=False)
    if gst.charges_gst(shop):
        values = [((Decimal(i['price']) * Decimal(i['qty'])).quantize(Decimal('0.01')), Decimal(i['gst_rate'])) for i in items]
        per_line, tax_total, _, added = gst.tax_lines(values, min(discount, items_price), shop.prices_include_gst)
        for item, (taxable, line_tax) in zip(items, per_line):
            item['taxable_value'], item['tax_amount'] = str(taxable), str(line_tax)
        interstate = not pickup and gst.is_interstate(shop, address.get('state'))
        cgst, sgst, igst = gst.split(tax_total, interstate)
        tax.update(cgst=str(cgst), sgst=str(sgst), igst=str(igst))
        totals['tax'] = money(tax_total)
        totals['total'] = money(totals['total'] + added)
    return {
        'gst': tax,
        'delivery': delivery, 'address': address, 'items': items, 'coupon_id': coupon.id if coupon else None,
        'note': str(data.get('deliveryNote') or '').strip()[:300],
        'totals': {k: str(v) for k, v in totals.items()},
    }


def _qty(value):
    try:
        qty = Decimal(str(value))
    except Exception:  # noqa: BLE001 - anything that isn't a number
        return None
    return qty if qty.is_finite() else None


def _check_steps(product, qty):
    """Online, loose items come in halves (0.5, 1, 1.5 kg...); everything else in whole units."""
    if product.sold_by_weight:
        if (qty * 2) % 1:
            raise CheckoutError(f'{product.name} can be ordered in steps of 0.5 {product.unit or "kg"}.')
    elif qty % 1:
        raise CheckoutError(f'{product.name} is sold in whole {product.unit or "piece"}s.')


def _check_available(product, qty):
    if not product or not product.sell_online:
        raise CheckoutError(f'{product.name if product else "An item"} is no longer sold online. Remove it from your cart.')
    if qty > (product.countInStock or 0):
        left = product.countInStock or 0
        raise CheckoutError(f'Only {left.normalize() if product.sold_by_weight else int(left)} {product.unit or ""} of {product.name} left in stock.'.replace('  ', ' '))


def _place(attempt, payment_id):
    """Create the paid order from a checked-out cart. Raises CheckoutError if it can't be."""
    from base.views.inventory_views import record_online_order_stock
    shop, user, p = attempt.business, attempt.user, attempt.payload
    with transaction.atomic():
        # Lock the items so two orders can't both take the last one.
        products = {x._id: x for x in Product.objects.select_for_update().filter(business=shop, _id__in=[i['product'] for i in p['items']])}
        for line in p['items']:
            _check_available(products.get(line['product']), Decimal(line['qty']))
        coupon = _coupon(shop, user, p.get('coupon_id'), lock=True)
        if p.get('coupon_id') and not coupon:
            raise CheckoutError('The coupon on this order was already used.')
        totals = {k: Decimal(v) for k, v in p['totals'].items()}
        g = p.get('gst') or {}
        order = Order.objects.create(
            gst_mode=g.get('mode', 'none'), cgst=Decimal(g.get('cgst', '0')), sgst=Decimal(g.get('sgst', '0')), igst=Decimal(g.get('igst', '0')),
            tax_included=bool(g.get('included')),
            business=shop, user=user, paymentMethod='Paid online (Razorpay)', payment_id=payment_id,
            isPaid=True, paidAt=timezone.now(), delivery_method=p['delivery'], delivery_note=p.get('note', ''), coupon=coupon,
            taxPrice=totals['tax'], shippingPrice=totals['shipping'], totalPrice=totals['total'],
        )
        a = p['address']
        ShippingAddress.objects.create(
            order=order, address=a['line1'][:200], area=a['area'], landmark=a['landmark'], city=a['city'][:200], state=a['state'],
            postalCode=a['pincode'], country=a['country'], full_name=a['full_name'][:120], phone=a['phone'])
        record_event(order, 'placed', user, f'Order placed and paid online ({payment_id})')
        for line in p['items']:
            product = products[line['product']]
            qty = Decimal(line['qty'])
            OrderItem.objects.create(product=product, order=order, name=product.name, qty=qty,
                                     price=Decimal(line['price']), image=product.image.url if product.image else '',
                                     hsn_code=line.get('hsn_code', ''), gst_rate=Decimal(line.get('gst_rate', '0')),
                                     taxable_value=Decimal(line.get('taxable_value', '0')), tax_amount=Decimal(line.get('tax_amount', '0')))
            product.countInStock -= qty
            product.save(update_fields=['countInStock'])
            record_online_order_stock(order, product, qty, user)
        if coupon:
            coupon.is_used = True
            coupon.status = False
            coupon.save(update_fields=['is_used', 'status'])
    return order


def finish(attempt_id, payment_id):
    """Turn a verified payment into an order. Safe to call twice (the page and the webhook):
    the second call returns the same order. If the order can't be placed, the payment is refunded."""
    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().select_related('business', 'user').get(pk=attempt_id)
        if attempt.status == 'paid' and attempt.order_id:
            return attempt.order
        if attempt.status in ('refunded', 'failed'):
            raise CheckoutError(attempt.error or 'This payment could not be turned into an order.', 409)
        try:
            order = _place(attempt, payment_id)
        except CheckoutError as e:
            # Saved now, so a second call (page and webhook together) can't refund twice.
            problem = str(e)
            attempt.status, attempt.razorpay_payment_id, attempt.error = 'failed', payment_id, problem
            attempt.save(update_fields=['status', 'razorpay_payment_id', 'error', 'updated_at'])
        else:
            attempt.status, attempt.order, attempt.razorpay_payment_id = 'paid', order, payment_id
            attempt.save(update_fields=['status', 'order', 'razorpay_payment_id', 'updated_at'])
            return order
    # The order couldn't be placed: give the money back.
    amount = money(attempt.amount)
    try:
        payments.refund(payment_id)
        status_, message = 'refunded', f'{problem} Your payment of ₹{amount} is being refunded.'
    except payments.PaymentError as refund_error:
        status_, message = 'failed', (f'{problem} Your payment of ₹{amount} could not be refunded automatically '
                                      f'({refund_error}); the shop will refund it.')
    PaymentAttempt.objects.filter(pk=attempt.pk).update(status=status_, error=message[:300])
    raise CheckoutError(message, 409)


def attempt_for_razorpay_order(razorpay_order_id):
    """The checkout a Razorpay order belongs to (for the webhook, which has no shop context).
    Razorpay order ids are created by this server and only arrive in signed webhooks."""
    return PaymentAttempt.objects.filter(razorpay_order_id=razorpay_order_id).first()
