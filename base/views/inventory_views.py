"""Back-office API for the physical shop: counter sales, stock received,
stock corrections and daily reports. Access depends on the staff role (see base/roles.py).

Stock is still stored on Product.countInStock, so the online store and the
shop counter always share one stock count.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from base import gst
from base.roles import AnyStaff, CanManageStock, CanSell, IsManager, request_is_manager, request_role, CASHIER, STOCK_KEEPER
from base.models import (
    SaleReturn, SaleReturnItem,
    ShippingAddress,
    Order, OrderItem, Product, Purchase, PurchaseItem, Sale, SaleItem,
    StockMovement, Supplier,
)

SHOP_TZ = ZoneInfo('Asia/Dhaka')
EXPIRY_WARNING_DAYS = 7


class InventoryError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decimal(value, field, default=None):
    if value in (None, ''):
        if default is not None:
            return default
        raise InventoryError(f'{field} is required.')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InventoryError(f'{field} must be a number.')
    if number < 0:
        raise InventoryError(f'{field} cannot be negative.')
    return number


def _whole_number(value, field):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise InventoryError(f'{field} must be a whole number.')
    if str(value).strip() not in (str(number), f'{number}.0'):
        raise InventoryError(f'{field} must be a whole number.')
    return number


def _quantity(value, product, field):
    """A quantity of a product. Whole numbers, unless the product is sold loose by weight."""
    if value in (None, ''):
        raise InventoryError(f'{field} is required.')
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise InventoryError(f'{field} must be a number.')
    if not number.is_finite():
        raise InventoryError(f'{field} must be a number.')
    if product.sold_by_weight:
        if number != number.quantize(Decimal('0.001')):
            raise InventoryError(f'{field} can have at most 3 decimal places.')
    elif number != number.to_integral_value():
        raise InventoryError(f'{field} must be a whole number.')
    return number


def _qty(value):
    """A quantity for JSON: 2 instead of 2.000, 1.5 instead of 1.500."""
    return float(value or 0)


def _qty_text(value):
    return f'{_qty(value):g}'


def _selling_price(product):
    price = product.price or Decimal('0')
    if product.is_offer and product.offer_percentage:
        price = price - price * Decimal(product.offer_percentage) / Decimal(100)
    return price.quantize(Decimal('0.01'))


def _money(value):
    return float(value or 0)


def _day_range(date_text=None):
    """Start and end of a calendar day in the shop's time zone."""
    if date_text:
        day = datetime.strptime(date_text, '%Y-%m-%d').date()
    else:
        day = timezone.now().astimezone(SHOP_TZ).date()
    start = datetime.combine(day, time.min, tzinfo=SHOP_TZ)
    return day, start, start + timedelta(days=1)


def _local(dt):
    return dt.astimezone(SHOP_TZ).isoformat() if dt else None


def _shelf_order(qs):
    """Batches in the order they leave the shelf: unexpired ones first, soonest expiry
    first, then the one received first. Expired batches go last so they aren't sold
    while good stock remains; they should be written off as wastage."""
    from django.db.models import Case, IntegerField, Value, When
    today = timezone.now().astimezone(SHOP_TZ).date()
    return qs.annotate(expired=Case(When(expiry_date__lt=today, then=Value(1)), default=Value(0),
                                    output_field=IntegerField())) \
        .order_by('expired', F('expiry_date').asc(nulls_last=True), 'purchase__createdAt', 'id')


def _consume_batches(product, qty, stock_before):
    """Take `qty` that left the shelf from the oldest stock first and return what it cost.

    Stock from before batches were tracked counts as the oldest and is costed at the
    product's last known buying price. Each batch is costed at what was paid for it.
    """
    qty = Decimal(qty)
    fallback = product.cost_price or Decimal('0')
    with transaction.atomic():
        batches = list(_shelf_order(PurchaseItem.objects.select_for_update().filter(product=product, remaining__gt=0)))
        tracked = sum((b.remaining for b in batches), Decimal('0'))
        untracked = max(Decimal(stock_before or 0) - tracked, Decimal('0'))
        from_untracked = min(qty, untracked)
        cost = from_untracked * fallback
        left = qty - from_untracked
        for batch in batches:
            if left <= 0:
                break
            take = min(batch.remaining, left)
            batch.remaining -= take
            batch.save(update_fields=['remaining'])
            cost += take * batch.unit_cost
            left -= take
        # Anything not covered by stock records (data out of step) uses the last price.
        cost += max(left, Decimal('0')) * fallback
    return cost


def _unit_cost(total_cost, qty):
    return (Decimal(total_cost) / Decimal(qty)).quantize(Decimal('0.001')) if qty else Decimal('0')


def _shelf(product):
    """What is on the shelf and what it cost: older stock plus each batch at its own price."""
    batches = list(PurchaseItem.objects.filter(product=product, remaining__gt=0))
    tracked = sum((b.remaining for b in batches), Decimal('0'))
    untracked = max((product.countInStock or Decimal('0')) - tracked, Decimal('0'))
    value = sum((b.remaining * b.unit_cost for b in batches), Decimal('0'))
    prices = [b.unit_cost for b in batches]
    if untracked > 0 and product.cost_price is not None:
        value += untracked * product.cost_price
        prices.append(product.cost_price)
    return {'value': value, 'prices': prices, 'untracked': untracked}


def _move_stock(product, change, reason, user=None, note='', unit_cost=None,
                sale=None, purchase=None, order=None):
    """Change stock on a product that is already locked with select_for_update."""
    stock_before = product.countInStock or 0
    new_stock = stock_before + change
    if new_stock < 0:
        raise InventoryError(
            f'Not enough stock for {product.name}. '
            f'Only {_qty_text(product.countInStock)} {product.unit or "unit"} left.')
    total_cost = None
    if change < 0:
        # What left the shelf is costed from the batches it came from.
        total_cost = _consume_batches(product, -change, stock_before)
        unit_cost = _unit_cost(total_cost, -change)
    product.countInStock = new_stock
    product.save(update_fields=['countInStock'])
    move = StockMovement.objects.create(
        business_id=product.business_id, product=product, change=change, stock_after=new_stock, reason=reason,
        note=note[:255], unit_cost=unit_cost, sale=sale, purchase=purchase,
        order=order, created_by=user if user and user.is_authenticated else None,
    )
    # The exact cost, before the per-unit figure is rounded for storage.
    move.total_cost = total_cost
    return move


def record_online_order_stock(order, product, qty, user=None):
    """Called by the online checkout (after it lowered stock) so web orders appear in
    the stock history and come out of the oldest batch."""
    cost = _consume_batches(product, qty, (product.countInStock or 0) + qty)
    StockMovement.objects.create(
        business_id=order.business_id, product=product, change=-qty, stock_after=product.countInStock,
        reason='online_sale', note=f'Online order #{order._id}',
        unit_cost=_unit_cost(cost, qty), order=order,
        created_by=user if user and user.is_authenticated else None,
    )


def _product_json(p):
    stock = p.countInStock or Decimal('0')
    return {
        '_id': p._id,
        'name': p.name,
        'category': p.category,
        'image': p.image.url if p.image else None,
        'unit': p.unit or 'piece',
        'price': _money(p.price),
        'selling_price': _money(_selling_price(p)),
        'is_offer': bool(p.is_offer),
        'offer_percentage': p.offer_percentage or 0,
        'cost_price': _money(p.cost_price) if p.cost_price is not None else None,
        'countInStock': _qty(stock),
        'reorder_level': p.reorder_level,
        'sell_online': p.sell_online,
        'sell_in_shop': p.sell_in_shop,
        'online_price': _money(p.online_price) if p.online_price is not None else None,
        'sold_by_weight': p.sold_by_weight,
        'hsn_code': p.hsn_code, 'gst_rate': float(p.gst_rate),
        'code': p.code or '',
        'low_stock': stock <= p.reorder_level,
        **_cost_summary(p, stock),
    }


def _cost_summary(p, stock):
    shelf = _shelf(p)
    prices = shelf['prices']
    return {
        'stock_value': _money(shelf['value']),
        # Average buying price of what is on the shelf, and the cheapest and dearest batch.
        'cost_avg': _money(shelf['value'] / stock) if stock and prices else None,
        'cost_min': _money(min(prices)) if prices else None,
        'cost_max': _money(max(prices)) if prices else None,
    }


def _sale_json(sale, with_items=True, show_profit=True):
    data = {
        'id': sale.pk,
        'customer_name': sale.customer_name,
        'customer_phone': sale.customer_phone,
        'payment_method': sale.payment_method,
        'payment_label': sale.get_payment_method_display(),
        'subtotal': _money(sale.subtotal),
        'discount': _money(sale.discount),
        'total': _money(sale.total),
        'amount_paid': _money(sale.amount_paid),
        'due': _money(_owed(sale)),
        'returned': _money(sale.returned),
        'net_total': _money(sale.total - sale.returned),
        'status': _sale_status(sale),
        'voided': sale.voided,
        'profit': _money(sale.total - sale.total_cost),
        'sold_by': (sale.created_by.first_name or sale.created_by.username) if sale.created_by else '',
        'createdAt': _local(sale.createdAt),
        'entered_later': sale.entered_later,
        'entered_at': _local(sale.entered_at),
        'gst_mode': sale.gst_mode,
        'tax_total': _money(sale.tax_total), 'cgst': _money(sale.cgst), 'sgst': _money(sale.sgst), 'igst': _money(sale.igst),
    }
    if not show_profit:
        data.pop('profit')
    if with_items:
        data['returns'] = [_return_json(r) for r in sale.returns.all()]
        data['items'] = [{
            'id': i.pk,
            'returned_qty': _qty(i.returned_qty),
            'returnable': _qty(i.qty - i.returned_qty),
            'hsn_code': i.hsn_code, 'gst_rate': float(i.gst_rate),
            'taxable_value': _money(i.taxable_value), 'tax_amount': _money(i.tax_amount),
            'product': i.product_id,
            'name': i.name,
            'unit': i.unit,
            'qty': _qty(i.qty),
            'unit_price': _money(i.unit_price),
            'line_total': _money((i.unit_price * i.qty).quantize(Decimal('0.01'))),
        } for i in sale.items.all()]
    else:
        data['item_count'] = _qty(sum((i.qty for i in sale.items.all()), Decimal('0')))
    return data


def _owed(sale):
    return max(sale.total - sale.returned - sale.amount_paid, Decimal('0'))


def _sale_status(sale):
    if sale.voided:
        return 'voided'
    if sale.returned >= sale.total and sale.total > 0:
        return 'returned'
    if sale.returned > 0:
        return 'partly_returned'
    return 'completed'


def _return_json(r):
    return {
        'id': r.pk, 'sale': r.sale_id, 'kind': r.kind, 'kind_label': r.get_kind_display(),
        'refund_method': r.refund_method, 'refund_label': r.get_refund_method_display(),
        'value': _money(r.value), 'refund_amount': _money(r.refund_amount), 'restocked': r.restocked, 'reason': r.reason,
        'by': (r.created_by.first_name or r.created_by.username) if r.created_by else '', 'createdAt': _local(r.createdAt),
        'items': [{'name': i.name, 'unit': i.unit, 'qty': _qty(i.qty), 'amount': _money(i.amount)} for i in r.items.all()],
    }


def _supplier_json(s):
    return {'id': s.pk, 'name': s.name, 'phone': s.phone, 'address': s.address, 'notes': s.notes}


def _error(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': message}, status=code)


# ---------------------------------------------------------------------------
# Products and stock
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AnyStaff])
def inventory_products(request):
    products = Product.objects.filter(business=request.business).order_by('category', 'name')
    data = [_product_json(p) for p in products]
    if request_role(request) == CASHIER:
        for item in data:
            for key in ('cost_price', 'stock_value', 'cost_avg', 'cost_min', 'cost_max'):
                item.pop(key)
    return Response(data)


@api_view(['PATCH'])
@permission_classes([CanManageStock])
def update_inventory_product(request, pk):
    """Edit the inventory settings of a product: unit, sold by weight, buying price, reorder level."""
    try:
        product = Product.objects.get(business=request.business, _id=pk)
    except Product.DoesNotExist:
        return _error('Product not found.', status.HTTP_404_NOT_FOUND)
    data = request.data
    try:
        if 'unit' in data:
            unit = str(data['unit']).strip()
            if not unit:
                raise InventoryError('Unit cannot be empty.')
            product.unit = unit[:40]
        if 'cost_price' in data:
            product.cost_price = None if data['cost_price'] in (None, '') else _decimal(data['cost_price'], 'Buying price')
        if 'reorder_level' in data:
            level = _whole_number(data['reorder_level'], 'Reorder level')
            if level < 0:
                raise InventoryError('Reorder level cannot be negative.')
            product.reorder_level = level
        if 'sold_by_weight' in data:
            loose = data['sold_by_weight'] in (True, 'true', 'True', 1, '1')
            stock = product.countInStock or Decimal('0')
            if not loose and stock != stock.to_integral_value():
                raise InventoryError(f'{_qty_text(stock)} {product.unit or ""} is in stock. Count it to a whole number before selling {product.name} in whole units.'.replace('  ', ' '))
            product.sold_by_weight = loose
    except InventoryError as e:
        return _error(str(e))
    product.save(update_fields=['unit', 'cost_price', 'reorder_level', 'sold_by_weight'])
    return Response(_product_json(product))


@api_view(['POST'])
@permission_classes([CanManageStock])
def adjust_stock(request):
    """Record wastage, a customer return or a stock count correction."""
    data = request.data
    reason = data.get('reason')
    if reason not in ('wastage', 'return', 'correction'):
        return _error('Choose wastage, return or correction.')
    try:
        with transaction.atomic():
            product = Product.objects.select_for_update().get(business=request.business, _id=_int_or_none(data.get('product')))
            qty = _quantity(data.get('qty'), product, 'Quantity')
            if reason == 'wastage':
                if qty <= 0:
                    raise InventoryError('Wastage quantity must be more than zero.')
                change = -qty
            elif reason == 'return':
                if qty <= 0:
                    raise InventoryError('Returned quantity must be more than zero.')
                change = qty
            else:
                # For a correction, qty is the stock actually counted on the shelf.
                if qty < 0:
                    raise InventoryError('Counted stock cannot be negative.')
                change = qty - (product.countInStock or 0)
                if change == 0:
                    raise InventoryError('The counted stock already matches the system.')
            _move_stock(product, change, reason, user=request.user,
                        note=data.get('note', ''), unit_cost=product.cost_price)
    except Product.DoesNotExist:
        return _error('Product not found.', status.HTTP_404_NOT_FOUND)
    except InventoryError as e:
        return _error(str(e))
    return Response(_product_json(product))


@api_view(['GET'])
@permission_classes([CanManageStock])
def stock_movements(request):
    moves = StockMovement.objects.filter(business=request.business).select_related('product', 'created_by')
    product_id = request.query_params.get('product')
    if product_id:
        moves = moves.filter(product___id=product_id)
    moves = moves[:200]
    return Response([{
        'id': m.pk,
        'product': m.product_id,
        'product_name': m.product.name,
        'unit': m.product.unit,
        'change': _qty(m.change),
        'stock_after': _qty(m.stock_after),
        'reason': m.reason,
        'reason_label': m.get_reason_display(),
        'note': m.note,
        'by': (m.created_by.first_name or m.created_by.username) if m.created_by else '',
        'createdAt': _local(m.createdAt),
    } for m in moves])


# ---------------------------------------------------------------------------
# Counter sales
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([CanSell])
def sales(request):
    if request.method == 'GET':
        day, start, end = _day_range(request.query_params.get('date'))
        qs = Sale.objects.filter(business=request.business, createdAt__gte=start, createdAt__lt=end) \
            .select_related('created_by').prefetch_related('items')
        manager = request_is_manager(request)
        return Response({'date': day.isoformat(),
                         'sales': [_sale_json(s, with_items=False, show_profit=manager) for s in qs]})
    return _create_sale(request)


def _create_sale(request):
    data = request.data
    items = data.get('items') or []
    if not items:
        return _error('Add at least one item to the sale.')
    payment = data.get('payment_method', 'cash')
    if payment not in dict(Sale.PAYMENT_CHOICES):
        return _error('Unknown payment method.')
    customer_name = (data.get('customer_name') or '').strip()[:150]
    customer_phone = (data.get('customer_phone') or '').strip()[:40]
    if payment == 'due' and not (customer_name and customer_phone):
        return _error('A due sale needs the customer name and phone number.')
    sold_at = None
    if data.get('sold_at'):
        # A past sale from a paper record. Managers only, and never in the future.
        if not request_is_manager(request):
            return _error('Only managers can record past sales.', status.HTTP_403_FORBIDDEN)
        try:
            sold_at = datetime.fromisoformat(str(data['sold_at']))
        except ValueError:
            return _error('The sale date and time could not be read.')
        if sold_at.tzinfo is None:
            sold_at = sold_at.replace(tzinfo=SHOP_TZ)
        now = timezone.now()
        if sold_at > now:
            return _error('A past sale cannot be in the future.')
        if sold_at < now - timedelta(days=366):
            return _error('Past sales can be recorded for up to a year back.')

    try:
        with transaction.atomic():
            ids = [i.get('product') for i in items]
            products = {p._id: p for p in Product.objects.select_for_update().filter(business=request.business, _id__in=ids)}
            sale = Sale.objects.create(
                business=request.business,
                customer_name=customer_name, customer_phone=customer_phone,
                payment_method=payment, created_by=request.user,
            )
            subtotal = Decimal('0')
            total_cost = Decimal('0')
            sale_items = []
            for line in items:
                product = products.get(_int_or_none(line.get('product')))
                if not product:
                    raise InventoryError('One of the items no longer exists. Refresh and try again.')
                if not product.sell_in_shop:
                    raise InventoryError(f'{product.name} is not sold in the shop.')
                qty = _quantity(line.get('qty'), product, f'Quantity for {product.name}')
                if qty <= 0:
                    raise InventoryError(f'Quantity for {product.name} must be more than zero.')
                # Counter staff cannot change prices. The shop's own price, offer included, always applies.
                unit_price = _selling_price(product)
                # The cost is what was paid for the batches these items came from.
                move = _move_stock(product, -qty, 'shop_sale', user=request.user,
                                   note=f'Shop sale #{sale.pk}', sale=sale)
                sale_items.append(SaleItem.objects.create(
                    sale=sale, product=product, name=product.name, unit=product.unit or '',
                    qty=qty, unit_price=unit_price, unit_cost=move.unit_cost or Decimal('0'),
                    hsn_code=product.hsn_code, gst_rate=product.gst_rate if gst.charges_gst(request.business) else 0,
                ))
                subtotal += (unit_price * qty).quantize(Decimal('0.01'))
                total_cost += move.total_cost

            discount = _decimal(data.get('discount'), 'Discount', default=Decimal('0'))
            if discount > subtotal:
                raise InventoryError('Discount cannot be more than the subtotal.')
            total = subtotal - discount
            # GST: the counter always sells within the shop's state, so CGST + SGST.
            sale.gst_mode = request.business.gst_mode
            if gst.charges_gst(request.business):
                values = [((i.unit_price * i.qty).quantize(Decimal('0.01')), i.gst_rate) for i in sale_items]
                per_line, tax, _, added = gst.tax_lines(values, discount, request.business.prices_include_gst)
                for item, (taxable, line_tax) in zip(sale_items, per_line):
                    item.taxable_value, item.tax_amount = taxable, line_tax
                    item.save(update_fields=['taxable_value', 'tax_amount'])
                total += added
                sale.tax_total = tax
                sale.cgst, sale.sgst, sale.igst = gst.split(tax, interstate=False)
            if payment == 'due':
                paid = _decimal(data.get('amount_paid'), 'Amount paid', default=Decimal('0'))
                if paid > total:
                    raise InventoryError('Amount paid cannot be more than the total.')
            else:
                paid = total
            sale.subtotal, sale.discount, sale.total = subtotal, discount, total
            sale.amount_paid, sale.total_cost = paid, total_cost
            sale.save()
            if sold_at:
                # File the bill, and its stock changes, under the time it was really sold.
                Sale.objects.filter(business=request.business, pk=sale.pk).update(createdAt=sold_at, entered_later=True, entered_at=timezone.now())
                moves = StockMovement.objects.filter(business=request.business, sale=sale)
                for move in moves:
                    moves.filter(pk=move.pk).update(createdAt=sold_at, note=f'{move.note} (entered later)'[:255])
                sale.refresh_from_db()
    except InventoryError as e:
        return _error(str(e))
    return Response(_sale_json(sale, show_profit=request_is_manager(request)), status=status.HTTP_201_CREATED)


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@api_view(['GET'])
@permission_classes([CanSell])
def sale_detail(request, pk):
    try:
        sale = Sale.objects.select_related('created_by').prefetch_related('items', 'returns__items', 'returns__created_by').get(business=request.business, pk=pk)
    except Sale.DoesNotExist:
        return _error('Sale not found.', status.HTTP_404_NOT_FOUND)
    return Response(_sale_json(sale, show_profit=request_is_manager(request)))


@api_view(['POST'])
@permission_classes([CanSell])
def collect_due(request, pk):
    """Record money received later for a due sale."""
    try:
        with transaction.atomic():
            sale = Sale.objects.select_for_update().get(business=request.business, pk=pk)
            outstanding = _owed(sale)
            if outstanding <= 0:
                raise InventoryError('This sale is already fully paid.')
            amount = _decimal(request.data.get('amount'), 'Amount')
            if amount <= 0:
                raise InventoryError('Amount must be more than zero.')
            if amount > outstanding:
                raise InventoryError(f'Only ₹{outstanding:.2f} is still due.')
            sale.amount_paid += amount
            sale.save(update_fields=['amount_paid'])
    except Sale.DoesNotExist:
        return _error('Sale not found.', status.HTTP_404_NOT_FOUND)
    except InventoryError as e:
        return _error(str(e))
    return Response(_sale_json(sale, show_profit=request_is_manager(request)))


def _return_items(request, sale, wanted, restock, reason, refund_method, kind):
    """Take items back from a bill (sale is locked). `wanted` maps sale item -> quantity."""
    if sale.voided:
        raise InventoryError('This bill was voided.')
    items = {i.pk: i for i in sale.items.select_related('product')}
    value, cost, lines = Decimal('0'), Decimal('0'), []
    share = (sale.total / sale.subtotal) if sale.subtotal else Decimal('1')   # each line's part of any bill discount
    for item_id, qty in wanted.items():
        item = items.get(item_id)
        if not item:
            raise InventoryError('One of the items is not on this bill.')
        left = item.qty - item.returned_qty
        if qty <= 0:
            continue
        if qty > left:
            raise InventoryError(' '.join(f'Only {_qty_text(left)} {item.unit or ""} of {item.name} can still be returned.'.split()))
        if item.product and not item.product.sold_by_weight and qty != qty.to_integral_value():
            raise InventoryError(f'{item.name} is returned in whole {item.unit or "piece"}s.')
        amount = (item.unit_price * qty * share).quantize(Decimal('0.01'))
        lines.append((item, qty, amount))
        value += amount
        cost += item.unit_cost * qty
    if not lines:
        raise InventoryError('Choose at least one item to return.')
    # When this takes back everything still on the bill, give back exactly what is left of it.
    taking = {it.pk: q for it, q, _ in lines}
    if all(i.qty - i.returned_qty == taking.get(i.pk, Decimal('0')) for i in items.values()):
        value = sale.total - sale.returned
    # The return first lowers what is still owed; money is handed back only for the rest.
    refund = max(sale.amount_paid - (sale.total - sale.returned - value), Decimal('0'))
    if refund > 0 and refund_method not in ('cash', 'upi', 'card'):
        raise InventoryError('Choose how the money is given back: cash, UPI or card.')
    record = SaleReturn.objects.create(
        business=sale.business, sale=sale, kind=kind, refund_method=refund_method if refund > 0 else 'none',
        value=value, refund_amount=refund, cost_recovered=cost if restock else Decimal('0'), restocked=restock,
        reason=reason, created_by=request.user)
    for item, qty, amount in lines:
        SaleReturnItem.objects.create(sale_return=record, sale_item=item, name=item.name, unit=item.unit, qty=qty, amount=amount)
        item.returned_qty += qty
        item.save(update_fields=['returned_qty'])
        if restock and item.product_id:
            product = Product.objects.select_for_update().get(pk=item.product_id, business=sale.business)
            _move_stock(product, qty, 'return', user=request.user, unit_cost=item.unit_cost,
                        note=f'{"Void" if kind == "void" else "Return"} on bill #{sale.pk}' + (f': {reason}' if reason else ''), sale=sale)
    sale.returned += value
    sale.returned_cost += cost if restock else Decimal('0')
    sale.amount_paid -= refund
    sale.voided = sale.voided or kind == 'void'
    sale.save(update_fields=['returned', 'returned_cost', 'amount_paid', 'voided'])
    return record


def _locked_sale(request, pk):
    return Sale.objects.select_for_update().select_related('business').get(business=request.business, pk=_int_or_none(pk))


def _sale_after(request, sale, manager_view=None):
    sale = Sale.objects.prefetch_related('items', 'returns__items', 'returns__created_by').select_related('created_by').get(
        business=request.business, pk=sale.pk)
    return _sale_json(sale, show_profit=request_is_manager(request) if manager_view is None else manager_view)


@api_view(['POST'])
@permission_classes([CanSell])
def return_items(request, pk):
    """A customer brings items back: refund them and (unless damaged) put them back on the shelf."""
    data = request.data
    try:
        wanted = {}
        for line in data.get('items') or []:
            item_id = _int_or_none(line.get('item'))
            qty = _decimal(line.get('qty'), 'Quantity', default=Decimal('0'))
            if item_id is not None:
                wanted[item_id] = wanted.get(item_id, Decimal('0')) + qty
        with transaction.atomic():
            sale = _locked_sale(request, pk)
            refund_method = data.get('refund_method') or (sale.payment_method if sale.payment_method in ('cash', 'upi', 'card') else 'cash')
            record = _return_items(request, sale, wanted, restock=data.get('restock', True) is not False,
                                   reason=str(data.get('reason') or '').strip()[:200], refund_method=refund_method, kind='return')
    except Sale.DoesNotExist:
        return _error('Bill not found.', status.HTTP_404_NOT_FOUND)
    except InventoryError as e:
        return _error(str(e))
    return Response({'return': _return_json(record), 'sale': _sale_after(request, sale)}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsManager])
def void_sale(request, pk):
    """A manager cancels a whole bill entered by mistake: everything goes back and is refunded."""
    reason = str(request.data.get('reason') or '').strip()[:200]
    if not reason:
        return _error('Say why the bill is being voided.')
    try:
        with transaction.atomic():
            sale = _locked_sale(request, pk)
            left = {i.pk: i.qty - i.returned_qty for i in sale.items.all() if i.qty > i.returned_qty}
            if sale.voided or not left:
                raise InventoryError('This bill was already voided or fully returned.')
            refund_method = request.data.get('refund_method') or (sale.payment_method if sale.payment_method in ('cash', 'upi', 'card') else 'cash')
            record = _return_items(request, sale, left, restock=True, reason=reason, refund_method=refund_method, kind='void')
    except Sale.DoesNotExist:
        return _error('Bill not found.', status.HTTP_404_NOT_FOUND)
    except InventoryError as e:
        return _error(str(e))
    return Response({'return': _return_json(record), 'sale': _sale_after(request, sale, True)}, status=status.HTTP_201_CREATED)


ONLINE_STATUS = dict(Order.STATUS_CHOICES)


@api_view(['GET'])
@permission_classes([CanSell])
def transactions(request):
    """Every sale of one day across all channels, newest first: counter bills and online
    orders (cancelled ones included but marked, and not counted as sales)."""
    try:
        day, start, end = _day_range(request.query_params.get('date'))
    except ValueError:
        return _error('Date must look like 2026-10-01.')
    business, manager = request.business, request_is_manager(request)
    rows = []
    for s in Sale.objects.filter(business=business, createdAt__gte=start, createdAt__lt=end) \
            .select_related('created_by').prefetch_related('items'):
        due = _owed(s)
        row = {
            'kind': 'shop', 'id': s.pk, 'createdAt': _local(s.createdAt), 'channel': 'Counter',
            'customer': s.customer_name, 'phone': s.customer_phone,
            'payment': s.get_payment_method_display(),
            'status': 'voided' if s.voided else 'due' if due > 0 else 'returned' if s.returned else 'paid',
            'status_label': 'Voided' if s.voided else 'Due' if due > 0 else ('Returned' if s.returned >= s.total else 'Part returned') if s.returned else 'Paid',
            'due': _money(due), 'total': _money(s.total), 'counted': True,
            'items': _qty(sum((i.qty for i in s.items.all()), Decimal('0'))),
            'by': (s.created_by.first_name or s.created_by.username) if s.created_by else '',
            'entered_later': s.entered_later,
        }
        if manager:
            row['profit'] = _money(s.total - s.total_cost)
        rows.append(row)
    orders = list(Order.objects.filter(business=business, createdAt__gte=start, createdAt__lt=end)
                  .select_related('user', 'shippingaddress').prefetch_related('orderitem_set'))
    costs = {}
    if manager:
        for m in StockMovement.objects.filter(business=business, reason='online_sale', order__in=orders):
            costs[m.order_id] = costs.get(m.order_id, Decimal('0')) + abs(m.change) * (m.unit_cost or 0)
    for o in orders:
        cancelled = o.order_status == 'cancelled'
        try:
            address = o.shippingaddress
        except ShippingAddress.DoesNotExist:
            address = None
        goods = sum(((i.price or 0) * (i.qty or 0) for i in o.orderitem_set.all()), Decimal('0'))
        pickup = o.delivery_method == 'pickup'
        status_label = 'Collected' if pickup and o.order_status == 'delivered' else ONLINE_STATUS.get(o.order_status, 'Placed')
        row = {
            'kind': 'online', 'id': o._id, 'createdAt': _local(o.createdAt), 'channel': 'Online · Pick-up' if pickup else 'Online · Delivery',
            'customer': (address.full_name if address and address.full_name else '') or (o.user.first_name or o.user.email if o.user else ''),
            'phone': address.phone if address else '',
            'payment': 'Paid online' if o.isPaid and o.payment_id else ('Paid' if o.isPaid else 'Not paid'),
            'status': o.order_status, 'status_label': ('Refunded' if o.isPaid else 'Cancelled') if cancelled else status_label,
            'due': _money(0 if o.isPaid or cancelled else (o.totalPrice or 0)), 'total': _money(o.totalPrice or 0),
            'counted': not cancelled, 'items': _qty(sum((i.qty or 0 for i in o.orderitem_set.all()), Decimal('0'))), 'by': '',
        }
        if manager:
            row['profit'] = _money(0 if cancelled else goods - costs.get(o._id, Decimal('0')))
        rows.append(row)
    for r in SaleReturn.objects.filter(business=business, createdAt__gte=start, createdAt__lt=end) \
            .select_related('sale', 'created_by').prefetch_related('items'):
        row = {
            'kind': 'refund', 'id': r.pk, 'sale': r.sale_id, 'createdAt': _local(r.createdAt), 'channel': 'Counter',
            'customer': r.sale.customer_name, 'phone': r.sale.customer_phone,
            'payment': r.get_refund_method_display() if r.refund_amount else 'Taken off what is owed',
            'status': r.kind, 'status_label': 'Void' if r.kind == 'void' else 'Refund',
            'due': 0, 'total': -_money(r.value), 'counted': True, 'reason': r.reason,
            'items': _qty(sum((i.qty for i in r.items.all()), Decimal('0'))),
            'by': (r.created_by.first_name or r.created_by.username) if r.created_by else '',
        }
        if manager:
            row['profit'] = -_money(r.value - r.cost_recovered)
        rows.append(row)
    rows.sort(key=lambda r: r['createdAt'], reverse=True)
    return Response({'date': day.isoformat(), 'transactions': rows})


@api_view(['GET'])
@permission_classes([CanSell])
def dues(request):
    qs = Sale.objects.filter(business=request.business, amount_paid__lt=F('total') - F('returned')).prefetch_related('items').select_related('created_by')
    return Response([_sale_json(s, with_items=False, show_profit=request_is_manager(request)) for s in qs])


# ---------------------------------------------------------------------------
# Suppliers and stock received
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([CanManageStock])
def suppliers(request):
    if request.method == 'GET':
        return Response([_supplier_json(s) for s in Supplier.objects.filter(business=request.business)])
    name = (request.data.get('name') or '').strip()
    if not name:
        return _error('Supplier name is required.')
    supplier = Supplier.objects.create(
        business=request.business,
        name=name[:150],
        phone=(request.data.get('phone') or '').strip()[:40],
        address=(request.data.get('address') or '').strip()[:255],
        notes=(request.data.get('notes') or '').strip(),
    )
    return Response(_supplier_json(supplier), status=status.HTTP_201_CREATED)


RECEIPT_TYPES = {'.jpg': 'image', '.jpeg': 'image', '.png': 'image', '.webp': 'image', '.heic': 'image', '.pdf': 'pdf'}
MAX_RECEIPT_BYTES = 10 * 1024 * 1024


def _purchase_status(p):
    due = p.total_cost - p.amount_paid
    if due <= 0:
        return 'paid', Decimal('0')
    return ('partly_paid' if p.amount_paid > 0 else 'unpaid'), due


def _receipt_kind(p):
    if not p.receipt:
        return None
    import os
    return RECEIPT_TYPES.get(os.path.splitext(p.receipt.name)[1].lower(), 'file')


def _purchase_json(p, with_items=True):
    status_key, due = _purchase_status(p)
    data = {
        'id': p.pk,
        'supplier': p.supplier.name if p.supplier else '',
        'supplier_id': p.supplier_id,
        'invoice_no': p.invoice_no,
        'note': p.note,
        'received_on': (p.received_on or p.createdAt.astimezone(SHOP_TZ).date()).isoformat(),
        'total_cost': _money(p.total_cost),
        'amount_paid': _money(p.amount_paid),
        'due': _money(due),
        'status': status_key,
        'receipt': _receipt_kind(p),
        'received_by': (p.created_by.first_name or p.created_by.username) if p.created_by else '',
        'createdAt': _local(p.createdAt),
    }
    if with_items:
        data['items'] = [{
            'product': i.product_id,
            'name': i.name,
            'unit': i.product.unit if i.product else '',
            'qty': _qty(i.qty),
            'remaining': _qty(i.remaining),
            'unit_cost': _money(i.unit_cost),
            'line_total': _money((i.unit_cost * i.qty).quantize(Decimal('0.01'))),
            'batch_no': i.batch_no,
            'expiry_date': i.expiry_date.isoformat() if i.expiry_date else None,
        } for i in p.items.all()]
    return data


def _check_receipt(upload):
    import os
    if upload is None:
        return None
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in RECEIPT_TYPES:
        raise InventoryError('The receipt must be a photo (JPG, PNG, WEBP, HEIC) or a PDF.')
    if upload.size > MAX_RECEIPT_BYTES:
        raise InventoryError('The receipt file is larger than 10 MB. Take a smaller photo.')
    return upload


def _request_data(request):
    """Deliveries arrive as JSON, or as a form with the receipt file and the rest as JSON in `payload`."""
    import json
    if 'payload' in request.data:
        try:
            return json.loads(request.data['payload'])
        except (TypeError, ValueError):
            raise InventoryError('The delivery details could not be read.')
    return request.data


@api_view(['GET', 'POST'])
@permission_classes([CanManageStock])
def purchases(request):
    if request.method == 'POST':
        return _create_purchase(request)
    qs = Purchase.objects.filter(business=request.business).select_related('supplier', 'created_by').prefetch_related('items__product')
    params = request.query_params
    query = (params.get('q') or '').strip()
    if query:
        from django.db.models import Q
        qs = qs.filter(Q(supplier__name__icontains=query) | Q(invoice_no__icontains=query) |
                       Q(items__name__icontains=query) | Q(items__batch_no__icontains=query)).distinct()
    if params.get('supplier'):
        qs = qs.filter(supplier_id=params['supplier'])
    rows = [_purchase_json(p) for p in qs[:200]]
    if params.get('status') in ('paid', 'unpaid', 'partly_paid'):
        rows = [r for r in rows if r['status'] == params['status']]
    owed = Purchase.objects.filter(business=request.business, amount_paid__lt=F('total_cost'))
    owed_total = sum((p.total_cost - p.amount_paid for p in owed), Decimal('0'))
    return Response({'deliveries': rows[:100], 'owed_total': _money(owed_total), 'owed_count': owed.count()})


@api_view(['GET'])
@permission_classes([CanManageStock])
def purchase_detail(request, pk):
    p = Purchase.objects.select_related('supplier', 'created_by').prefetch_related('items__product').filter(business=request.business, pk=pk).first()
    if not p:
        return _error('Delivery not found.', status.HTTP_404_NOT_FOUND)
    return Response(_purchase_json(p))


@api_view(['GET', 'POST'])
@permission_classes([CanManageStock])
def purchase_receipt(request, pk):
    """Download the receipt, or attach one to a delivery saved without it."""
    p = Purchase.objects.filter(business=request.business, pk=pk).first()
    if not p:
        return _error('Delivery not found.', status.HTTP_404_NOT_FOUND)
    if request.method == 'GET':
        if not p.receipt:
            return _error('No receipt was attached to this delivery.', status.HTTP_404_NOT_FOUND)
        import mimetypes
        from django.http import FileResponse
        kind = mimetypes.guess_type(p.receipt.name)[0] or 'application/octet-stream'
        return FileResponse(p.receipt.open('rb'), content_type=kind, filename=f'receipt-{p.pk}{p.receipt.name[p.receipt.name.rfind("."):]}')
    try:
        upload = _check_receipt(request.FILES.get('receipt'))
    except InventoryError as e:
        return _error(str(e))
    if not upload:
        return _error('Choose a photo or PDF of the receipt.')
    old = p.receipt.name if p.receipt else None
    p.receipt = upload
    p.save(update_fields=['receipt'])
    if old:
        p.receipt.storage.delete(old)
    return Response(_purchase_json(Purchase.objects.prefetch_related('items__product').get(business=request.business, pk=pk)))


@api_view(['POST'])
@permission_classes([CanManageStock])
def pay_supplier(request, pk):
    """Record money paid to the supplier for a delivery."""
    try:
        with transaction.atomic():
            p = Purchase.objects.select_for_update().get(business=request.business, pk=pk)
            due = p.total_cost - p.amount_paid
            if due <= 0:
                raise InventoryError('This delivery is already paid in full.')
            amount = _decimal(request.data.get('amount'), 'Amount')
            if amount <= 0:
                raise InventoryError('Amount must be more than zero.')
            if amount > due:
                raise InventoryError(f'Only ₹{_qty_text(due)} is still owed for this delivery.')
            p.amount_paid += amount
            p.save(update_fields=['amount_paid'])
    except Purchase.DoesNotExist:
        return _error('Delivery not found.', status.HTTP_404_NOT_FOUND)
    except InventoryError as e:
        return _error(str(e))
    return Response(_purchase_json(Purchase.objects.select_related('supplier').prefetch_related('items__product').get(business=request.business, pk=pk)))


@api_view(['GET'])
@permission_classes([CanManageStock])
def product_batches(request, pk):
    """What is on the shelf for one product, batch by batch, oldest first."""
    product = Product.objects.filter(business=request.business, _id=pk).first()
    if not product:
        return _error('Product not found.', status.HTTP_404_NOT_FOUND)
    batches = list(_shelf_order(PurchaseItem.objects.filter(product=product, remaining__gt=0)
                                .select_related('purchase__supplier')))
    tracked = sum((b.remaining for b in batches), Decimal('0'))
    today = timezone.now().astimezone(SHOP_TZ).date()
    return Response({
        'product': product._id,
        'in_stock': _qty(product.countInStock),
        'untracked': _qty(max((product.countInStock or 0) - tracked, Decimal('0'))),
        'batches': [{
            'delivery': b.purchase_id,
            'supplier': b.purchase.supplier.name if b.purchase.supplier else '',
            'received_on': (b.purchase.received_on or b.purchase.createdAt.astimezone(SHOP_TZ).date()).isoformat(),
            'batch_no': b.batch_no,
            'qty': _qty(b.qty),
            'remaining': _qty(b.remaining),
            'unit_cost': _money(b.unit_cost),
            'expiry_date': b.expiry_date.isoformat() if b.expiry_date else None,
            'days_left': (b.expiry_date - today).days if b.expiry_date else None,
        } for b in batches],
    })


def _create_purchase(request):
    try:
        data = _request_data(request)
        receipt = _check_receipt(request.FILES.get('receipt'))
    except InventoryError as e:
        return _error(str(e))
    items = data.get('items') or []
    if not items:
        return _error('Add at least one item you received.')
    supplier = None
    if data.get('supplier'):
        supplier = Supplier.objects.filter(business=request.business, pk=_int_or_none(data.get('supplier'))).first()
        if not supplier:
            return _error('Supplier not found.')
    try:
        received_on = None
        if data.get('received_on'):
            try:
                received_on = datetime.strptime(data['received_on'], '%Y-%m-%d').date()
            except ValueError:
                raise InventoryError('The delivery date is not a valid date.')
            if received_on > timezone.now().astimezone(SHOP_TZ).date():
                raise InventoryError('The delivery date cannot be in the future.')
        with transaction.atomic():
            ids = [i.get('product') for i in items]
            products = {p._id: p for p in Product.objects.select_for_update().filter(business=request.business, _id__in=ids)}
            purchase = Purchase.objects.create(
                business=request.business,
                supplier=supplier,
                invoice_no=(data.get('invoice_no') or '').strip()[:80],
                note=(data.get('note') or '').strip()[:255],
                received_on=received_on or timezone.now().astimezone(SHOP_TZ).date(),
                created_by=request.user,
            )
            total = Decimal('0')
            for line in items:
                product = products.get(_int_or_none(line.get('product')))
                if not product:
                    raise InventoryError('One of the items no longer exists. Refresh and try again.')
                qty = _quantity(line.get('qty'), product, f'Quantity for {product.name}')
                if qty <= 0:
                    raise InventoryError(f'Quantity for {product.name} must be more than zero.')
                unit_cost = _decimal(line.get('unit_cost'), f'Buying price for {product.name}')
                expiry = None
                if line.get('expiry_date'):
                    try:
                        expiry = datetime.strptime(line['expiry_date'], '%Y-%m-%d').date()
                    except ValueError:
                        raise InventoryError(f'Expiry date for {product.name} is not a valid date.')
                PurchaseItem.objects.create(
                    purchase=purchase, product=product, name=product.name,
                    qty=qty, remaining=qty, unit_cost=unit_cost, expiry_date=expiry,
                    batch_no=(line.get('batch_no') or '').strip()[:60],
                )
                note = f'Received from {supplier.name}' if supplier else 'Stock received'
                _move_stock(product, qty, 'purchase', user=request.user, note=note,
                            unit_cost=unit_cost, purchase=purchase)
                # The latest buying price is used to work out profit on future sales.
                product.cost_price = unit_cost
                product.save(update_fields=['cost_price'])
                total += (unit_cost * qty).quantize(Decimal('0.01'))
            # What was paid when the goods arrived: everything, nothing or part.
            paid = data.get('amount_paid')
            paid = total if paid in (None, '') or paid == 'all' else _decimal(paid, 'Amount paid')
            if paid > total:
                raise InventoryError('Amount paid cannot be more than the delivery total.')
            purchase.total_cost, purchase.amount_paid = total, paid
            if receipt:
                purchase.receipt = receipt
            purchase.save()
    except InventoryError as e:
        return _error(str(e))
    return Response(_purchase_json(Purchase.objects.select_related('supplier').prefetch_related('items__product').get(business=request.business, pk=purchase.pk)),
                    status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AnyStaff])
def summary(request):
    """Figures for one day, plus stock warnings."""
    try:
        day, start, end = _day_range(request.query_params.get('date'))
    except ValueError:
        return _error('Date must look like 2026-10-01.')

    shop = Sale.objects.filter(business=request.business, createdAt__gte=start, createdAt__lt=end).prefetch_related('returns')
    shop_total = shop.aggregate(v=Sum('total'))['v'] or Decimal('0')
    shop_cost = shop.aggregate(v=Sum('total_cost'))['v'] or Decimal('0')
    # Refunds count on the day they are given, like a till: the sale day keeps what it took.
    refunds = list(SaleReturn.objects.filter(business=request.business, createdAt__gte=start, createdAt__lt=end))
    refund_value = sum((r.value for r in refunds), Decimal('0'))
    refund_money = sum((r.refund_amount for r in refunds), Decimal('0'))
    refund_profit = sum((r.value - r.cost_recovered for r in refunds), Decimal('0'))
    shop_paid = sum((_taken(s) for s in shop), Decimal('0')) - refund_money

    day_orders = Order.objects.filter(business=request.business, createdAt__gte=start, createdAt__lt=end)
    # Cancelled orders are not sales; they are reported on their own (with any refund).
    online = day_orders.exclude(order_status='cancelled')
    cancelled = day_orders.filter(order_status='cancelled')
    online_total = online.aggregate(v=Sum('totalPrice'))['v'] or Decimal('0')
    # Profit on web orders counts the goods only, not shipping or tax.
    online_goods = Decimal('0')
    online_cost = Decimal('0')
    for item in OrderItem.objects.filter(order__in=online):
        online_goods += (item.price or 0) * (item.qty or 0)
    for m in StockMovement.objects.filter(business=request.business, reason='online_sale', order__in=online):
        online_cost += abs(m.change) * (m.unit_cost or 0)

    wastage_moves = StockMovement.objects.filter(
        business=request.business, reason='wastage', createdAt__gte=start, createdAt__lt=end)
    wastage_cost = sum((abs(m.change) * (m.unit_cost or 0) for m in wastage_moves), Decimal('0'))

    # Best sellers across both the counter and the website.
    sold = {}
    for m in StockMovement.objects.filter(business=request.business, reason__in=('shop_sale', 'online_sale'),
                                          createdAt__gte=start, createdAt__lt=end).select_related('product'):
        entry = sold.setdefault(m.product_id, {'name': m.product.name, 'unit': m.product.unit, 'qty': Decimal('0')})
        entry['qty'] += -m.change
    top = sorted(sold.values(), key=lambda e: -e['qty'])[:5]
    for entry in top:
        entry['qty'] = _qty(entry['qty'])

    products = list(Product.objects.filter(business=request.business))
    low_stock = [_product_json(p) for p in products if (p.countInStock or 0) <= p.reorder_level]
    low_stock.sort(key=lambda p: p['countInStock'])

    today = timezone.now().astimezone(SHOP_TZ).date()
    soon = today + timedelta(days=EXPIRY_WARNING_DAYS)
    expiring = []
    for item in PurchaseItem.objects.filter(purchase__business=request.business, expiry_date__isnull=False, expiry_date__lte=soon,
                                            remaining__gt=0, product__isnull=False) \
            .select_related('product').order_by('expiry_date'):
        expiring.append({
            'product': item.product_id,
            'name': item.name,
            'unit': item.product.unit,
            'batch_no': item.batch_no,
            'batch_qty': _qty(item.remaining),
            'in_stock': _qty(item.product.countInStock),
            'expiry_date': item.expiry_date.isoformat(),
            'days_left': (item.expiry_date - today).days,
        })

    outstanding = Sale.objects.filter(business=request.business, amount_paid__lt=F('total') - F('returned'))
    due_total = sum((_owed(s) for s in outstanding), Decimal('0'))

    report = {
        'date': day.isoformat(),
        'shop': {
            'count': shop.count(),
            'gross': _money(shop_total),
            'refunds': _money(refund_value),
            'total': _money(shop_total - refund_value),
            'collected': _money(shop_paid),
            'profit': _money(shop_total - shop_cost - refund_profit),
        },
        'refunds': {'count': len(refunds), 'value': _money(refund_value), 'money': _money(refund_money)},
        'online': {
            'count': online.count(),
            'total': _money(online_total),
            'profit': _money(online_goods - online_cost),
            'delivery_count': online.exclude(delivery_method='pickup').count(),
            'pickup_count': online.filter(delivery_method='pickup').count(),
        },
        'all': {
            'count': shop.count() + online.count(),
            'total': _money(shop_total - refund_value + online_total),
        },
        'cancelled': {
            'count': cancelled.count(),
            'refunded': _money(cancelled.filter(isPaid=True).aggregate(v=Sum('totalPrice'))['v'] or Decimal('0')),
        },
        'payments': _payment_split(shop, online, refunds),
        'wastage_cost': _money(wastage_cost),
        'top_items': top,
        'low_stock': low_stock,
        'expiring': expiring,
        'dues': {'count': outstanding.count(), 'total': _money(due_total)},
        'stock_value': _money(sum((_shelf(p)['value'] for p in products), Decimal('0'))),
        'missing_cost_price': sum(1 for p in products if p.cost_price is None),
    }
    return Response(_report_for_role(report, request_role(request)))


def _taken(sale):
    """Money a bill brought in, before any later refund (refunds count on their own day)."""
    return sale.amount_paid + sum((r.refund_amount for r in sale.returns.all()), Decimal('0'))


def _payment_split(shop_sales, online_orders, refunds=()):
    """Money taken in each way that day: counter cash, UPI and card (less refunds given that
    day), paid online, and what is still owed."""
    split = {'cash': Decimal('0'), 'upi': Decimal('0'), 'card': Decimal('0'), 'other': Decimal('0'),
             'online': Decimal('0'), 'due': Decimal('0')}
    for sale in shop_sales:
        method = sale.payment_method if sale.payment_method in ('cash', 'upi', 'card') else 'other'
        split['due'] += _owed(sale)
        # A "due" bill may already be part paid; what came in counts as cash.
        split['cash' if sale.payment_method == 'due' else method] += _taken(sale)
    for r in refunds:
        if r.refund_amount and r.refund_method in ('cash', 'upi', 'card'):
            split[r.refund_method] -= r.refund_amount
    for order in online_orders:
        if order.isPaid:
            split['online'] += order.totalPrice or 0
        else:
            split['due'] += order.totalPrice or 0
    return {k: _money(v) for k, v in split.items()}


def _report_for_role(report, role):
    """Cashiers see sales but no profit or costs. Stock keepers see stock but no sales money."""
    if role == CASHIER:
        report['shop'].pop('profit')
        report['online'].pop('profit')
        report.get('all', {}).pop('profit', None)
        for key in ('wastage_cost', 'stock_value', 'missing_cost_price'):
            report.pop(key)
    elif role == STOCK_KEEPER:
        for key in ('shop', 'online', 'dues', 'all', 'cancelled', 'payments'):
            report.pop(key, None)
    return report


# ---------------------------------------------------------------------------
# Home dashboard
# ---------------------------------------------------------------------------

def _day_figures(business, start, end):
    """Sales across the counter and the website for one period, for one business."""
    shop = list(Sale.objects.filter(business=business, createdAt__gte=start, createdAt__lt=end))
    online = list(Order.objects.filter(business=business, createdAt__gte=start, createdAt__lt=end).exclude(order_status='cancelled'))
    refunds = list(SaleReturn.objects.filter(business=business, createdAt__gte=start, createdAt__lt=end))
    shop_total = sum((s.total for s in shop), Decimal('0')) - sum((r.value for r in refunds), Decimal('0'))
    online_total = sum((o.totalPrice or 0 for o in online), Decimal('0'))
    count = len(shop) + len(online)
    shop_profit = shop_total - sum((s.total_cost for s in shop), Decimal('0')) + sum((r.cost_recovered for r in refunds), Decimal('0'))
    # Online profit counts the goods only (not delivery or tax), less what they cost to buy.
    online_goods = sum(((i.price or 0) * (i.qty or 0) for i in OrderItem.objects.filter(order__in=online)), Decimal('0'))
    online_cost = sum((abs(m.change) * (m.unit_cost or 0) for m in StockMovement.objects.filter(
        business=business, reason='online_sale', order__in=online)), Decimal('0'))
    online_profit = online_goods - online_cost
    return shop, online, {
        'net_sales': _money(shop_total + online_total),
        'transactions': count,
        'average_sale': _money((shop_total + online_total) / count) if count else 0,
        'shop_total': _money(shop_total),
        'shop_count': len(shop),
        'online_total': _money(online_total),
        'online_count': len(online),
        'profit': _money(shop_profit + online_profit),
        'shop_profit': _money(shop_profit),
        'online_profit': _money(online_profit),
    }


@api_view(['GET'])
@permission_classes([IsManager])
def home(request):
    """Everything the admin home page shows: today against the same day last week."""
    try:
        day, start, end = _day_range(request.query_params.get('date'))
    except ValueError:
        return _error('Date must look like 2026-10-01.')
    shop, online, today = _day_figures(request.business, start, end)
    _, _, last_week = _day_figures(request.business, start - timedelta(days=7), end - timedelta(days=7))

    hourly = [{'hour': h, 'shop': 0.0, 'online': 0.0} for h in range(24)]
    for s in shop:
        hourly[s.createdAt.astimezone(SHOP_TZ).hour]['shop'] += _money(s.total)
    for o in online:
        hourly[o.createdAt.astimezone(SHOP_TZ).hour]['online'] += _money(o.totalPrice)

    labels = dict(Sale.PAYMENT_CHOICES)
    payments = {}
    for s in shop:
        entry = payments.setdefault(s.payment_method, {'method': labels.get(s.payment_method, s.payment_method), 'total': 0.0, 'count': 0})
        entry['total'] += _money(s.total)
        entry['count'] += 1
    if online:
        payments['online'] = {'method': 'Online orders', 'total': today['online_total'], 'count': len(online)}

    recent = [{
        'kind': 'shop', 'id': s.pk, 'label': f'Counter sale #{s.pk}', 'customer': s.customer_name,
        'method': labels.get(s.payment_method, s.payment_method), 'total': _money(s.total),
        'due': _money(max(s.total - s.amount_paid, Decimal('0'))), 'createdAt': _local(s.createdAt),
    } for s in Sale.objects.filter(business=request.business).order_by('-createdAt')[:8]]
    recent += [{
        'kind': 'online', 'id': o._id, 'label': f'Online order #{o._id}',
        'customer': (o.user.first_name or o.user.username) if o.user else '',
        'method': o.paymentMethod or 'Online', 'total': _money(o.totalPrice), 'due': 0,
        'createdAt': _local(o.createdAt), 'paid': o.isPaid, 'delivered': o.isDelivered,
    } for o in Order.objects.filter(business=request.business).select_related('user').order_by('-createdAt')[:8]]
    recent.sort(key=lambda r: r['createdAt'] or '', reverse=True)

    sold = {}
    for m in StockMovement.objects.filter(business=request.business, reason__in=('shop_sale', 'online_sale'),
                                          createdAt__gte=start, createdAt__lt=end).select_related('product'):
        entry = sold.setdefault(m.product_id, {'name': m.product.name, 'unit': m.product.unit, 'qty': Decimal('0')})
        entry['qty'] += -m.change
    top = sorted(sold.values(), key=lambda e: -e['qty'])[:5]
    for entry in top:
        entry['qty'] = _qty(entry['qty'])

    products = list(Product.objects.filter(business=request.business))
    outstanding = Sale.objects.filter(business=request.business, amount_paid__lt=F('total') - F('returned'))
    due_total = sum((_owed(s) for s in outstanding), Decimal('0'))
    soon = timezone.now().astimezone(SHOP_TZ).date() + timedelta(days=EXPIRY_WARNING_DAYS)

    return Response({
        'date': day.isoformat(),
        'today': today,
        'last_week': last_week,
        'hourly': hourly,
        'payments': sorted(payments.values(), key=lambda p: -p['total']),
        'recent': recent[:8],
        'top_items': top,
        'alerts': {
            'low_stock': sum(1 for p in products if 0 < (p.countInStock or 0) <= p.reorder_level),
            'out_of_stock': sum(1 for p in products if (p.countInStock or 0) <= 0),
            'expiring': PurchaseItem.objects.filter(purchase__business=request.business, expiry_date__isnull=False, expiry_date__lte=soon,
                                                    remaining__gt=0).count(),
            'dues_count': outstanding.count(),
            'dues_total': _money(due_total),
            'online_to_ship': Order.objects.filter(business=request.business, isDelivered=False).count(),
        },
    })


# ---------------------------------------------------------------------------
# GST report
# ---------------------------------------------------------------------------

def _range(params):
    """Start and end (exclusive) of a date range in the shop's time zone. Defaults to this month."""
    today = timezone.now().astimezone(SHOP_TZ).date()
    first = datetime.strptime(params['from'], '%Y-%m-%d').date() if params.get('from') else today.replace(day=1)
    last = datetime.strptime(params['to'], '%Y-%m-%d').date() if params.get('to') else today
    if last < first:
        raise ValueError
    return first, last, datetime.combine(first, time.min, tzinfo=SHOP_TZ), datetime.combine(last + timedelta(days=1), time.min, tzinfo=SHOP_TZ)


@api_view(['GET'])
@permission_classes([IsManager])
def gst_report(request):
    """GST charged in a date range, by rate and by HSN code, counter and online together.
    Returns on the counter are taken off on the day they were given (credit notes)."""
    try:
        first, last, start, end = _range(request.query_params)
    except ValueError:
        return _error('Choose a valid date range, like 2026-10-01 to 2026-10-31.')
    business = request.business
    rates, hsns = {}, {}

    def add(rate, hsn, name, unit, qty, taxable, tax, interstate, sign=1):
        rate = Decimal(rate)
        cgst, sgst, igst = gst.split(tax, interstate)
        row = rates.setdefault(rate, {'rate': float(rate), 'taxable': Decimal('0'), 'cgst': Decimal('0'), 'sgst': Decimal('0'), 'igst': Decimal('0')})
        for key, value in (('taxable', taxable), ('cgst', cgst), ('sgst', sgst), ('igst', igst)):
            row[key] += sign * value
        h = hsns.setdefault((hsn or '-', rate), {'hsn': hsn or '', 'rate': float(rate), 'name': name, 'unit': unit or '',
                                                 'qty': Decimal('0'), 'taxable': Decimal('0'), 'tax': Decimal('0')})
        h['qty'] += sign * Decimal(qty)
        h['taxable'] += sign * taxable
        h['tax'] += sign * tax

    counter_bills = Sale.objects.filter(business=business, createdAt__gte=start, createdAt__lt=end).prefetch_related('items')
    untaxed = Decimal('0')
    taxed_count = 0
    for sale in counter_bills:
        if sale.gst_mode != 'regular':
            untaxed += sale.total
            continue
        taxed_count += 1
        for i in sale.items.all():
            add(i.gst_rate, i.hsn_code, i.name, i.unit, i.qty, i.taxable_value, i.tax_amount, interstate=False)
    refunds = SaleReturnItem.objects.filter(sale_return__business=business, sale_return__createdAt__gte=start,
                                            sale_return__createdAt__lt=end).select_related('sale_item', 'sale_return__sale')
    credit_notes = set()
    for r in refunds:
        sale, item = r.sale_return.sale, r.sale_item
        if sale.gst_mode != 'regular':
            untaxed -= r.amount
            continue
        credit_notes.add(r.sale_return_id)
        part = (r.qty / item.qty) if item.qty else Decimal('0')
        add(item.gst_rate, item.hsn_code, item.name, item.unit, r.qty, money_round(item.taxable_value * part), money_round(item.tax_amount * part),
            interstate=False, sign=-1)
    orders = Order.objects.filter(business=business, createdAt__gte=start, createdAt__lt=end).exclude(order_status='cancelled') \
        .prefetch_related('orderitem_set__product')
    online_count = 0
    for o in orders:
        if o.gst_mode != 'regular':
            untaxed += o.totalPrice or 0
            continue
        online_count += 1
        for i in o.orderitem_set.all():
            add(i.gst_rate, i.hsn_code, i.name, i.product.unit if i.product else '', i.qty or 0, i.taxable_value, i.tax_amount,
                interstate=bool(o.igst))

    by_rate = sorted(rates.values(), key=lambda r: r['rate'])
    for r in by_rate:
        r['tax'] = r['cgst'] + r['sgst'] + r['igst']
        for key in ('taxable', 'cgst', 'sgst', 'igst', 'tax'):
            r[key] = _money(r[key])
    by_hsn = sorted(hsns.values(), key=lambda h: (h['hsn'] or 'zzzz', h['rate']))
    for h in by_hsn:
        h['qty'] = _qty(h['qty'])
        h['taxable'], h['tax'] = _money(h['taxable']), _money(h['tax'])
    totals = {key: _money(sum((Decimal(str(r[key])) for r in by_rate), Decimal('0'))) for key in ('taxable', 'cgst', 'sgst', 'igst', 'tax')}
    return Response({
        'from': first.isoformat(), 'to': last.isoformat(),
        'gst': {'mode': business.gst_mode, 'gstin': business.gstin, 'state': business.state},
        'invoices': {'counter': taxed_count, 'online': online_count, 'credit_notes': len(credit_notes)},
        'by_rate': by_rate, 'by_hsn': by_hsn, 'totals': totals,
        'untaxed_sales': _money(untaxed),
    })


def money_round(value):
    return Decimal(value).quantize(Decimal('0.01'))
