"""Product categories, per business. Anyone can list a shop's categories; only its managers change them."""
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from base.models import Category, Product
from base.roles import IsManager, MANAGER_ROLES
from base.tenancy import public_business, staff_membership

ICONS = {'tag', 'wheat', 'leaf', 'pot', 'bean', 'citrus', 'coffee', 'palette', 'apple', 'milk', 'egg', 'fish', 'cookie', 'gift'}


def _products_in(category):
    return Product.objects.filter(business_id=category.business_id, category__icontains=category.keyword)


def _json(c, with_count=False):
    data = {'id': c.pk, 'name': c.name, 'keyword': c.keyword, 'icon': c.icon, 'position': c.position}
    if with_count:
        data['product_count'] = _products_in(c).count()
    return data


def _error(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': message}, status=code)


def _manager_business(request):
    """The business the request's manager is working in, or None."""
    membership = staff_membership(request)
    return membership.business if membership and membership.role in MANAGER_ROLES else None


def _clean(data, category=None):
    """Validated name, keyword and icon from a request, keeping current values when a field is left out."""
    name = (data.get('name', category.name if category else '') or '').strip()[:60]
    if not name:
        raise ValueError('Category name is required.')
    keyword = (data.get('keyword') or '').strip().lower()[:60]
    if not keyword:
        keyword = category.keyword if category and 'keyword' not in data else name.lower()
    icon = data.get('icon', category.icon if category else 'tag') or 'tag'
    if icon not in ICONS:
        icon = 'tag'
    return name, keyword, icon


@api_view(['GET', 'POST'])
def categories(request):
    if request.method == 'GET':
        # Managers working in a business get its categories with product counts;
        # visitors get the categories of the shop they are visiting.
        business = _manager_business(request) if request.headers.get('X-Business') else None
        shop = business or public_business(request)
        return Response([_json(c, with_count=bool(business)) for c in Category.objects.filter(business=shop)])

    business = _manager_business(request)
    if not business:
        return _error('Only managers can add categories.', status.HTTP_403_FORBIDDEN)
    try:
        name, keyword, icon = _clean(request.data)
    except ValueError as e:
        return _error(str(e))
    if Category.objects.filter(business=business, name__iexact=name).exists():
        return _error(f'There is already a category called "{name}".')
    last = Category.objects.filter(business=business).order_by('-position').first()
    category = Category.objects.create(business=business, name=name, keyword=keyword, icon=icon,
                                       position=(last.position + 1) if last else 0)
    return Response(_json(category, with_count=True), status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsManager])
def category_detail(request, pk):
    category = Category.objects.filter(business=request.business, pk=pk).first()
    if not category:
        return _error('Category not found.', status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        count = _products_in(category).count()
        if count:
            return _error(f'{count} item{"s" if count != 1 else ""} still use "{category.name}". '
                          'Move them to another category first.')
        category.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    try:
        name, keyword, icon = _clean(request.data, category)
    except ValueError as e:
        return _error(str(e))
    if Category.objects.filter(business=request.business, name__iexact=name).exclude(pk=pk).exists():
        return _error(f'There is already a category called "{name}".')
    old_name = category.name
    try:
        with transaction.atomic():
            category.name, category.keyword, category.icon = name, keyword, icon
            category.save()
            # Products labelled with the old name move to the new name, so they stay in this category.
            if name != old_name:
                Product.objects.filter(business=request.business, category__iexact=old_name).update(category=name)
    except IntegrityError:
        return _error(f'There is already a category called "{name}".')
    return Response(_json(category, with_count=True))
