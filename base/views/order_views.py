from django.shortcuts import render

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from base.models import Product, Order, OrderItem, ShippingAddress, CouponRedemption
from base.serializers import ProductSerializer, OrderSerializer

from rest_framework import status
from rest_framework.decorators import throttle_classes
from rest_framework.throttling import ScopedRateThrottle
from datetime import datetime
from decimal import Decimal

from django.db import transaction

import json

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import authentication_classes
from rest_framework.permissions import AllowAny

from base import checkout, payments
from base.models import PaymentAttempt
from base.delivery import CUSTOMER_CANCEL_UNTIL, cancel as cancel_order, move as move_order, next_steps

from base.models import Membership
from base.roles import CanHandleOrders, MANAGER, ORDERS, OWNER
from base.tenancy import public_business

ORDER_ROLES = (OWNER, MANAGER, ORDERS)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _order_for(request, pk):
    """An order the person may see, and whether they see it as staff.

    Staff: orders of a business where they handle orders (checked by membership).
    Customers: their own orders from the shop they are visiting."""
    pk = _int(pk)
    if pk is None:
        return None, False
    staff_businesses = Membership.objects.filter(user=request.user, is_active=True, role__in=ORDER_ROLES,
                                                 business__status='active').values('business')
    order = Order.objects.filter(business__in=staff_businesses, _id=pk).first()
    if order:
        return order, True
    order = Order.objects.filter(business=public_business(request), user=request.user, _id=pk).first()
    return order, False


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
def startPayment(request):
    """Step 1 of paying: check the cart, work out the total here, and open a Razorpay payment
    for exactly that amount. No order is created until the payment is confirmed."""
    shop = public_business(request)
    if not payments.is_configured():
        return Response({'detail': "Online payment isn't set up for this shop yet, so orders can't be placed. Please try again later."},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        payload = checkout.prepare(shop, request.user, request.data)
    except checkout.CheckoutError as e:
        return Response({'detail': str(e)}, status=e.code)
    total = Decimal(payload['totals']['total'])
    if total < 1:
        return Response({'detail': 'The order total must be at least ₹1 to pay online.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        rzp = payments.create_order(int((total * 100).to_integral_value()), receipt=f'{shop.slug[:20]}-{request.user.id}',
                                    notes={'shop': shop.slug, 'customer': request.user.email})
    except payments.PaymentError as e:
        return Response({'detail': f'The payment could not be started. Please try again. ({e})'}, status=status.HTTP_502_BAD_GATEWAY)
    PaymentAttempt.objects.create(business=shop, user=request.user, amount=total, payload=payload, razorpay_order_id=rzp['id'])
    address = payload['address']
    return Response({
        'key_id': settings.RAZORPAY_KEY_ID, 'razorpay_order_id': rzp['id'], 'amount': rzp['amount'], 'currency': 'INR',
        'shop': shop.name, 'totals': payload['totals'],
        'prefill': {'name': address.get('full_name') or request.user.first_name, 'email': request.user.email, 'contact': address.get('phone') or ''},
    })

startPayment.cls.throttle_scope = 'checkout'


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirmPayment(request):
    """Step 2: the page reports a successful payment. The order is created only if Razorpay's
    signature is right for this customer's own payment."""
    data = request.data
    attempt = PaymentAttempt.objects.filter(business=public_business(request), user=request.user,
                                            razorpay_order_id=str(data.get('razorpay_order_id') or '')).first()
    if not attempt:
        return Response({'detail': 'Payment not found.'}, status=status.HTTP_404_NOT_FOUND)
    payment_id = str(data.get('razorpay_payment_id') or '')
    if not payment_id or not payments.signature_ok(attempt.razorpay_order_id, payment_id, data.get('razorpay_signature')):
        return Response({'detail': 'The payment could not be verified, so no order was placed. If money was taken it will be refunded.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        order = checkout.finish(attempt.pk, payment_id)
    except checkout.CheckoutError as e:
        return Response({'detail': str(e)}, status=e.code)
    return Response(OrderSerializer(order).data)


@csrf_exempt
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def razorpayWebhook(request):
    """Razorpay tells us about captured payments, so an order is still created if the customer
    closed the page right after paying. Only calls signed with the webhook secret are used."""
    if not payments.webhook_ok(request.body, request.headers.get('X-Razorpay-Signature')):
        return Response({'detail': 'Bad signature.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        event = json.loads(request.body)
        entity = event['payload']['payment']['entity']
    except (ValueError, KeyError, TypeError):
        return Response({'ok': True})
    if event.get('event') in ('payment.captured', 'order.paid') and entity.get('order_id'):
        attempt = checkout.attempt_for_razorpay_order(entity['order_id'])
        if attempt and int(entity.get('amount') or 0) == int((attempt.amount * 100).to_integral_value()):
            try:
                checkout.finish(attempt.pk, entity['id'])
            except checkout.CheckoutError:
                pass  # recorded on the attempt (refunded or needing a manual refund)
    return Response({'ok': True})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def addOrderItems(request):
    """Orders are no longer placed without payment: they are created by confirmPayment."""
    return Response({'detail': 'Please pay online to place your order.'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getMyOrders(request):
    # Order history is kept per shop: only orders from the shop being visited.
    orders = Order.objects.filter(business=public_business(request), user=request.user)
    serializer = OrderSerializer(orders, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([CanHandleOrders])
def getOrders(request):
    orders = Order.objects.filter(business=request.business)
    serializer = OrderSerializer(orders, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getOrderById(request, pk):

    order, _ = _order_for(request, pk)
    if not order:
        # Same answer whether it doesn't exist or belongs to someone else.
        return Response({'detail': 'Order does not exist'}, status=status.HTTP_404_NOT_FOUND)
    return Response(OrderSerializer(order, many=False).data)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def updateOrderToPaid(request, pk):
    # Only the customer who placed it, or staff of that business who handle orders.
    order, as_staff = _order_for(request, pk)
    if not order:
        return Response({'detail': 'Order does not exist'}, status=status.HTTP_404_NOT_FOUND)
    # Customers pay online at checkout (verified with Razorpay), so only staff mark older
    # orders as paid here, when they take the money themselves.
    if not as_staff:
        return Response({'detail': 'Orders are paid online at checkout.'}, status=status.HTTP_403_FORBIDDEN)

    order.isPaid = True
    order.paidAt = datetime.now()
    order.save()

    return Response('Order was paid')


@api_view(['PUT'])
@permission_classes([CanHandleOrders])
def updateOrderToDelivered(request, pk):
    order = Order.objects.filter(business=request.business, _id=_int(pk)).first()
    if not order:
        return Response({'detail': 'Order does not exist'}, status=status.HTTP_404_NOT_FOUND)

    if order.order_status == 'cancelled':
        return Response({'detail': 'This order was cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
    move_order(order, 'delivered', request.user)
    return Response('Order was delivered')


@api_view(['PUT'])
@permission_classes([CanHandleOrders])
def updateOrderStatus(request, pk):
    """Staff move an order to its next step (confirmed, packed, out for delivery or ready for
    pick-up, delivered), or cancel it."""
    order = Order.objects.filter(business=request.business, _id=_int(pk)).first()
    if not order:
        return Response({'detail': 'Order does not exist'}, status=status.HTTP_404_NOT_FOUND)
    target = request.data.get('status')
    note = str(request.data.get('note') or '').strip()
    if target == 'cancelled':
        if order.order_status in ('delivered', 'cancelled'):
            return Response({'detail': 'A delivered or cancelled order can\'t be cancelled.'}, status=status.HTTP_400_BAD_REQUEST)
        cancel_order(order, request.user, note or 'Cancelled by the shop')
    elif target in next_steps(order):
        move_order(order, target, request.user, note)
    else:
        return Response({'detail': 'This order can\'t move to that step.'}, status=status.HTTP_400_BAD_REQUEST)
    return Response(OrderSerializer(order).data)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def cancelMyOrder(request, pk):
    """A customer cancels their own order before it is packed. Orders already paid online
    are cancelled by the shop, which also arranges the refund."""
    order = Order.objects.filter(business=public_business(request), user=request.user, _id=_int(pk)).first()
    if not order:
        return Response({'detail': 'Order does not exist'}, status=status.HTTP_404_NOT_FOUND)
    if order.order_status not in CUSTOMER_CANCEL_UNTIL:
        return Response({'detail': 'This order is already being prepared, so it can\'t be cancelled here. Please contact the shop.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if order.isPaid and not order.payment_id:
        return Response({'detail': 'This order was paid to the shop directly. Please contact the shop to cancel it.'},
                        status=status.HTTP_400_BAD_REQUEST)
    reason = str(request.data.get('reason') or '').strip()
    try:
        cancel_order(order, request.user, f'Cancelled by the customer{": " + reason if reason else ""}', require_refund=True)
    except payments.PaymentError:
        return Response({'detail': "Your refund couldn't be started just now, so the order is still active. Please try again or contact the shop."},
                        status=status.HTTP_502_BAD_GATEWAY)
    return Response(OrderSerializer(order).data)
