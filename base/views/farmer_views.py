import random

from rest_framework.decorators import api_view
from rest_framework.response import Response

from base.models import Farmer
from base.pricing import online_unit_price
from base.tenancy import public_business


def _price(product):
    return float(online_unit_price(product))


@api_view(['GET'])
def featured_farmer(request):
    """One featured farmer, picked at random so each visit can show a different story."""
    shop = public_business(request)
    farmers = list(Farmer.objects.filter(business=shop, is_featured=True).prefetch_related('products'))
    if not farmers:
        return Response(None)
    f = random.choice(farmers)
    return Response({
        'id': f.id,
        'name': f.name,
        'role': f.role,
        'location': f.location,
        'story': f.story,
        'photo': f.photo.url if f.photo else None,
        'products': [
            {'_id': p._id, 'name': p.name, 'image': p.image.url if p.image else None, 'price': _price(p)}
            for p in f.products.filter(business=shop, sell_online=True)[:4]
        ],
    })
