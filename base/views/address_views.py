"""A customer's saved delivery addresses in the shop they are visiting. Each customer
only ever sees and changes their own, and addresses are kept separately for each shop."""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from base.delivery import INDIAN_STATES, AddressError, clean_address, delivers_to
from base.models import CustomerAddress
from base.tenancy import public_business

MAX_ADDRESSES = 20


def _json(a, business):
    return {'id': a.id, 'label': a.label, 'full_name': a.full_name, 'phone': a.phone, 'line1': a.line1, 'area': a.area,
            'landmark': a.landmark, 'city': a.city, 'state': a.state, 'pincode': a.pincode, 'country': a.country,
            'is_default': a.is_default, 'delivers_here': delivers_to(business, a.pincode)}


def _make_default(address):
    CustomerAddress.objects.filter(business_id=address.business_id, user_id=address.user_id).exclude(pk=address.pk).update(is_default=False)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def addresses(request):
    shop = public_business(request)
    mine = CustomerAddress.objects.filter(business=shop, user=request.user)
    if request.method == 'GET':
        return Response({'addresses': [_json(a, shop) for a in mine], 'states': INDIAN_STATES})
    if mine.count() >= MAX_ADDRESSES:
        return Response({'detail': f'You can save up to {MAX_ADDRESSES} addresses.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        data = clean_address(request.data)
    except AddressError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    with transaction.atomic():
        first = not mine.exists()
        address = CustomerAddress.objects.create(business=shop, user=request.user, **data,
                                                 is_default=first or bool(request.data.get('is_default')))
        if address.is_default:
            _make_default(address)
    return Response(_json(address, shop), status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def address_detail(request, pk):
    shop = public_business(request)
    address = CustomerAddress.objects.filter(business=shop, user=request.user, pk=pk).first()
    if not address:
        return Response({'detail': 'Address not found.'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'DELETE':
        with transaction.atomic():
            was_default = address.is_default
            address.delete()
            if was_default:
                nxt = CustomerAddress.objects.filter(business=shop, user=request.user).first()
                if nxt:
                    nxt.is_default = True
                    nxt.save(update_fields=['is_default'])
        return Response(status=status.HTTP_204_NO_CONTENT)
    if set(request.data) - {'is_default'}:
        try:
            data = clean_address({**_json(address, shop), **request.data})
        except AddressError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        for field, value in data.items():
            setattr(address, field, value)
    with transaction.atomic():
        if request.data.get('is_default'):
            address.is_default = True
            _make_default(address)
        address.save()
    return Response(_json(address, shop))
