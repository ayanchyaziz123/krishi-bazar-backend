from django.shortcuts import render
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.contrib.auth.models import User
from base.serializers import ProductSerializer, UserProfileSerilizer, UserSerializer, UserSerializerWithToken
# Create your views here.
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

from django.contrib.auth.hashers import make_password
from rest_framework import status
from django.db import transaction
from django.db.models import Q
from base.models import Membership, Order, TempUser, UserProfile
from base.roles import CASHIER, IsManager, MANAGER, MANAGER_ROLES, ORDERS, OWNER, STOCK_KEEPER, request_role
from django.db.models import Sum
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.decorators import throttle_classes
from rest_framework.throttling import ScopedRateThrottle
from base.views.helper_file.otp import send_otp

class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        # Emails are the usernames. Match them ignoring case and stray spaces,
        # so "Customer@Test.local " still finds customer@test.local.
        typed = (attrs.get(self.username_field) or '').strip()
        match = User.objects.filter(username__iexact=typed).first()
        attrs[self.username_field] = match.username if match else typed
        data = super().validate(attrs)

        serializer = UserSerializerWithToken(self.user).data
        for k, v in serializer.items():
            data[k] = v

        return data


class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer
    # Slows down password guessing: limited attempts per minute from one address.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'






def _otp_hash(code):
    return make_password(code)


@api_view(['POST'])
@throttle_classes([ScopedRateThrottle])
def tempRegister_user(request):
    """Step 1 of sign-up: check the details and email a one-time code."""
    data = request.data
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    mobile = (data.get('mobile') or '').strip()
    password = data.get('password') or ''
    if not name or not email or not mobile:
        return Response({'detail': 'Name, email and mobile number are required.'}, status=status.HTTP_400_BAD_REQUEST)
    if UserProfile.objects.filter(mobile=mobile).exists():
        return Response({'detail': 'User with this mobile already exists'}, status=status.HTTP_400_BAD_REQUEST)
    if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
        return Response({'detail': 'User with this email already exists'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        validate_password(password)
    except ValidationError as e:
        return Response({'detail': ' '.join(e.messages)}, status=status.HTTP_400_BAD_REQUEST)

    code = f'{secrets.randbelow(10 ** 6):06d}'
    if not send_otp(email, code):
        return Response({'detail': 'We could not send the code to that email. Check the address and try again.'},
                        status=status.HTTP_400_BAD_REQUEST)
    # One pending sign-up per email; asking again replaces the old code.
    TempUser.objects.filter(temp_user_email__iexact=email).delete()
    TempUser.objects.create(
        temp_user_firstName=name[:200],
        temp_user_name=email,
        temp_user_email=email,
        temp_user_mobile=mobile[:200],
        temp_user_otp=_otp_hash(code),
        temp_user_password=make_password(password),
        otp_created_at=timezone.now(),
    )
    return Response(email)

tempRegister_user.cls.throttle_scope = 'otp_send'


@api_view(['POST'])
@throttle_classes([ScopedRateThrottle])
def registerUser_with_otp(request):
    """Step 2 of sign-up: check the code and create the account."""
    email = (request.data.get('email') or '').strip().lower()
    code = str(request.data.get('otp') or '').strip()
    wrong = Response({'detail': 'That code is wrong or has expired. Ask for a new one.'}, status=status.HTTP_400_BAD_REQUEST)
    with transaction.atomic():
        temp_user = TempUser.objects.select_for_update().filter(temp_user_email__iexact=email).first()
        if not temp_user or not code:
            return wrong
        expired = not temp_user.otp_created_at or \
            timezone.now() - temp_user.otp_created_at > timedelta(minutes=settings.OTP_TTL_MINUTES)
        if expired or temp_user.otp_attempts >= settings.OTP_MAX_ATTEMPTS:
            temp_user.delete()
            return wrong
        if not check_password(code, temp_user.temp_user_otp):
            temp_user.otp_attempts += 1
            temp_user.save(update_fields=['otp_attempts'])
            return wrong
        if User.objects.filter(username__iexact=email).exists():
            temp_user.delete()
            return Response({'detail': 'User with this email already exists'}, status=status.HTTP_400_BAD_REQUEST)
        user = User(first_name=temp_user.temp_user_firstName, username=email, email=email)
        # The password was hashed when the code was sent; store that hash as it is.
        user.password = temp_user.temp_user_password
        user.save()
        UserProfile.objects.create(user=user, mobile=temp_user.temp_user_mobile, otp='')
        temp_user.delete()
    return Response(UserSerializerWithToken(user, many=False).data)

registerUser_with_otp.cls.throttle_scope = 'otp_verify'


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def updateUserProfile(request):
    user = request.user
    serializer = UserSerializerWithToken(user, many=False)

    data = request.data
    # Customers can change their name and password. The email is their login
    # and can't be changed here; any email sent in the request is ignored.
    if data.get('name'):
        user.first_name = data['name']

    if data.get('password'):
        user.password = make_password(data['password'])

    user.save()

    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getUserProfile(request):
    user = request.user
    serializer = UserSerializer(user, many=False)
    return Response(serializer.data)


def _people_of(business):
    """Staff of a business plus customers who ordered from it. Accounts are shared across
    the platform, so a business only ever sees people connected to it."""
    # Staff whose access is paused are still listed, so a manager can restore them.
    staff_ids = Membership.objects.filter(business=business).values('user_id')
    customer_ids = Order.objects.filter(business=business, user__isnull=False).values('user_id')
    return User.objects.filter(Q(id__in=staff_ids) | Q(id__in=customer_ids)).distinct().order_by('id')


def _person(business, pk):
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return None
    return _people_of(business).filter(id=pk).first()


def _serialize(users, business, many=False):
    return UserSerializer(users, many=many, context={'business': business}).data


@api_view(['GET'])
@permission_classes([IsManager])
def getUsers(request):
    return Response(_serialize(_people_of(request.business), request.business, many=True))


@api_view(['GET'])
@permission_classes([IsManager])
def getUserById(request, pk):
    user = _person(request.business, pk)
    if not user:
        return _bad('User not found.', status.HTTP_404_NOT_FOUND)
    return Response(_serialize(user, request.business))


ROLE_CHOICES = ('customer', OWNER, MANAGER, CASHIER, STOCK_KEEPER, ORDERS)
STAFF_ROLES = (OWNER, MANAGER, CASHIER, STOCK_KEEPER, ORDERS)


def _set_role(business, user, role):
    """Give a person a role in one business, or remove them from it ('customer').
    Changing the role of someone whose access is paused leaves it paused."""
    if role in STAFF_ROLES:
        Membership.objects.update_or_create(user=user, business=business, defaults={'role': role})
    else:
        Membership.objects.filter(user=user, business=business).delete()


def _used_elsewhere(user, business):
    """Whether this account is connected to any other business (staff or customer).
    Then its name and email belong to the person, not to this business."""
    return Membership.objects.filter(user=user).exclude(business=business).exists() or \
        Order.objects.filter(user=user).exclude(business=business).exists()


def _bad(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': message}, status=code)


@api_view(['PUT'])
@permission_classes([IsManager])
def updateUser(request, pk):
    """Managers change a person's role in their business, or pause and restore their
    access (`active`). Name and email can only be changed for staff accounts that belong
    to this business alone. Ownership moves only by the owner handing it over."""
    business = request.business
    user = _person(business, pk)
    if not user:
        return _bad('User not found.', status.HTTP_404_NOT_FOUND)
    data = request.data
    membership = Membership.objects.filter(user=user, business=business).first()
    current = membership.role if membership else None

    # `role` is the current field; older screens send only `isAdmin`.
    if 'role' in data:
        role = data['role'] or 'customer'
    elif 'isAdmin' in data:
        role = MANAGER if data['isAdmin'] else (current if current not in MANAGER_ROLES else 'customer')
    else:
        role = current or 'customer'
    if role not in ROLE_CHOICES:
        return _bad('Choose customer, cashier, stock keeper, online orders, manager or owner.')
    if user == request.user and role != current:
        if current == OWNER:
            return _bad('To stop being the owner, make someone else the owner. You will become a manager.')
        return _bad("You can't change your own role. Ask the owner or another manager to do it.")
    transfer = role == OWNER and current != OWNER
    if OWNER in (role, current) and role != current:
        if request_role(request) != OWNER:
            return _bad('Only the owner can make someone owner or change the owner.', status.HTTP_403_FORBIDDEN)
        if not transfer:
            return _bad('A business always has an owner. To change it, make someone else the owner.')
        if not membership or not membership.is_active:
            return _bad('Only an active member of your staff can become the owner. Add them as staff first.')
    active = data.get('active')
    if active is not None:
        active = active in (True, 'true', 'True', 1, '1')
        if not membership or role not in STAFF_ROLES:
            return _bad('Only staff have access to pause or restore.')
        if not active and user == request.user:
            return _bad("You can't pause your own access.")
        if not active and role == OWNER:
            return _bad("The owner's access can't be paused.")
    # The edit page sends back the name it shows, which is the email when no name is set.
    shown_name = (user.first_name or '').strip() or user.email
    name = (data.get('name') or shown_name or '').strip()
    email = (data.get('email') or user.email or '').strip().lower()
    identity_changed = name != shown_name or email != (user.email or '').lower()
    if name == user.email and not user.first_name:
        name = ''
    if identity_changed:
        if current is None:
            return _bad("A customer's name and email belong to them. They can change them on their account page.",
                        status.HTTP_403_FORBIDDEN)
        if _used_elsewhere(user, business):
            return _bad('This account is also used outside your business, so only its owner can change the name or email.',
                        status.HTTP_403_FORBIDDEN)
        if not email:
            return _bad('Email is required.')
        if User.objects.filter(username__iexact=email).exclude(pk=user.pk).exists():
            return _bad(f'{email} is already used by another account.')

    with transaction.atomic():
        if identity_changed:
            user.first_name, user.username, user.email = name, email, email
            user.save()
        if transfer:
            # Hand the business over: the new owner gets full control and the old owner stays on as a manager.
            Membership.objects.filter(business=business, role=OWNER).update(role=MANAGER)
            business.owner = user
            business.save(update_fields=['owner', 'updated_at'])
        _set_role(business, user, role)
        if active is not None:
            Membership.objects.filter(user=user, business=business).update(is_active=active)

    return Response(_serialize(user, business))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def myBusinesses(request):
    """The businesses the signed-in person works in, with their role in each. The admin
    area reloads this so a changed role or a removed membership shows up straight away."""
    return Response(UserSerializerWithToken().get_businesses(request.user))


@api_view(['POST'])
@permission_classes([IsManager])
def createStaff(request):
    """Add someone to this business's staff. A new email gets an account; an existing
    account is added to this business with the chosen role (its password is unchanged)."""
    business = request.business
    data = request.data
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    role = data.get('role')
    if not name or not email:
        return _bad('Name and email are required.')
    if '@' not in email:
        return _bad('Enter a valid email address.')
    if role not in (MANAGER, CASHIER, STOCK_KEEPER, ORDERS):
        return _bad('Choose cashier, stock keeper, online orders or manager.')
    existing = User.objects.filter(username__iexact=email).first()
    if existing:
        if Membership.objects.filter(user=existing, business=business).exists():
            return _bad(f'{email} is already on your staff. Change their role on the Staff page instead.')
        _set_role(business, existing, role)
        return Response(_serialize(existing, business), status=status.HTTP_201_CREATED)
    try:
        validate_password(password)
    except ValidationError as e:
        return _bad(' '.join(e.messages))
    with transaction.atomic():
        user = User.objects.create(username=email, email=email, first_name=name, password=make_password(password))
        _set_role(business, user, role)
    return Response(_serialize(user, business), status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsManager])
def deleteUser(request, pk):
    """Remove someone from this business's staff. Accounts are shared across the platform,
    so the account itself is never deleted by a business."""
    business = request.business
    user = _person(business, pk)
    if not user:
        return _bad('User not found.', status.HTTP_404_NOT_FOUND)
    if user == request.user:
        return _bad("You can't remove yourself.")
    membership = Membership.objects.filter(user=user, business=business).first()
    if not membership:
        return _bad("Customer accounts belong to the customer and can't be deleted by a store.")
    if membership.role == OWNER:
        return _bad("The owner can't be removed.")
    membership.delete()
    return Response('User was removed from your staff')


@api_view(['POST'])
@throttle_classes([ScopedRateThrottle])
def resetPassword(request):
    data = request.data 
    email = data['email']
    try:
        user = User.objects.get(email=email)
        return Response({'email': email, 'result': True})
    except:
        return Response({'email': email, 'result': False})

resetPassword.cls.throttle_scope = 'password_reset'
