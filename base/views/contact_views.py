"""Messages from the shop's contact form. Anyone can send one; staff who handle
online orders (and managers) read them and mark them replied."""
import re

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.response import Response

from base.models import Contact
from base.roles import CanHandleOrders
from base.tenancy import public_business

EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _json(c):
    return {
        'id': c.pk,
        'name': f'{c.first_name} {c.last_name}'.strip(),
        'email': c.email,
        'phone': f'{c.area_code} {c.phone}'.strip() if c.phone else '',
        'wants_contact': c.need_contact,
        'contact_by': c.contact_media,
        'message': c.message,
        'replied': c.reply,
        'reply_note': c.reply_note,
        'replied_by': (c.replied_by.first_name or c.replied_by.username) if c.replied_by else '',
        'replied_at': c.replied_at.isoformat() if c.replied_at else None,
        'createdAt': c.createdAt.isoformat() if c.createdAt else None,
    }


def _bad(message):
    return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@throttle_classes([ScopedRateThrottle])
def createContact(request):
    data = request.data
    # A hidden field real people never fill in; simple bots usually do.
    if (data.get('website') or '').strip():
        return Response({'detail': 'Thank you. Your message was sent.'}, status=status.HTTP_201_CREATED)
    first = (data.get('first_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    message = (data.get('message') or '').strip()
    if not first:
        return _bad('Please enter your name.')
    if not email and not phone:
        return _bad('Please give an email address or a phone number so we can reply.')
    if email and not EMAIL.match(email):
        return _bad('That email address does not look right.')
    if len(message) < 5:
        return _bad('Please write a message.')
    if len(message) > 800:
        return _bad('Please keep the message under 800 characters.')
    contact_by = data.get('contact_media') if data.get('contact_media') in ('email', 'tel') else ('email' if email else 'tel')
    Contact.objects.create(
        business=public_business(request),
        first_name=first[:200], last_name=(data.get('last_name') or '').strip()[:200],
        area_code=(data.get('area_code') or '').strip()[:20], phone=phone[:40], email=email[:200],
        need_contact=bool(data.get('need_contact')), contact_media=contact_by, message=message,
    )
    return Response({'detail': 'Thank you. Your message was sent.'}, status=status.HTTP_201_CREATED)


createContact.cls.throttle_scope = 'contact'


@api_view(['GET'])
@permission_classes([CanHandleOrders])
def contacts(request):
    qs = Contact.objects.filter(business=request.business).select_related('replied_by')
    show = request.query_params.get('status')
    if show == 'waiting':
        qs = qs.filter(reply=False)
    elif show == 'replied':
        qs = qs.filter(reply=True)
    return Response({'messages': [_json(c) for c in qs[:300]],
                     'waiting': Contact.objects.filter(business=request.business, reply=False).count()})


@api_view(['PATCH'])
@permission_classes([CanHandleOrders])
def contact_detail(request, pk):
    c = Contact.objects.filter(business=request.business, pk=pk).first()
    if not c:
        return Response({'detail': 'Message not found.'}, status=status.HTTP_404_NOT_FOUND)
    if 'replied' in request.data:
        c.reply = bool(request.data['replied'])
        c.replied_by = request.user if c.reply else None
        c.replied_at = timezone.now() if c.reply else None
    if 'reply_note' in request.data:
        c.reply_note = (request.data.get('reply_note') or '').strip()[:500]
    c.save()
    return Response(_json(c))
