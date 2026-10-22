"""What the online shop charges. The server is the only place prices are decided:
anything the browser sends about prices, tax, shipping or totals is ignored.

Each business sets its own delivery fee, free-delivery limit and tax rate (Business
fields); without a business the original fixed rules apply."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal('0.01')
FREE_SHIPPING_OVER = Decimal('100')
SHIPPING_FEE = Decimal('10')
TAX_RATE = Decimal('5')


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def online_unit_price(product):
    """The website price: the online price if one is set, else the shop price, less any offer."""
    base = product.online_price if product.online_price is not None else (product.price or Decimal('0'))
    base = Decimal(base)
    if product.is_offer and product.offer_percentage:
        base -= base * Decimal(product.offer_percentage) / Decimal(100)
    return money(base)


def delivery_rules(business=None):
    """(fee, free_over or None, tax percent) for a business, or the original fixed rules."""
    if business is None:
        return SHIPPING_FEE, FREE_SHIPPING_OVER, TAX_RATE
    return Decimal(business.delivery_fee), (Decimal(business.free_delivery_over) if business.free_delivery_over is not None else None), \
        Decimal(business.tax_rate)


def order_totals(items_price, discount=Decimal('0'), pickup=False, business=None, flat_tax=True):
    """Shipping, tax and total for an order whose goods cost `items_price`. Pick-up has no
    delivery charge; delivery is free when the goods cost more than the shop's limit."""
    fee, free_over, tax_rate = delivery_rules(business)
    items_price = money(items_price)
    free = pickup or (free_over is not None and items_price > free_over)
    shipping = Decimal('0') if free else fee
    # The flat online tax applies only to shops not registered for GST (see base/gst.py).
    tax = money(items_price * tax_rate / Decimal(100)) if flat_tax else Decimal('0')
    discount = min(money(discount), items_price + shipping + tax)
    total = money(items_price + shipping + tax - discount)
    return {'items': items_price, 'shipping': money(shipping), 'tax': tax, 'discount': discount, 'total': total}
