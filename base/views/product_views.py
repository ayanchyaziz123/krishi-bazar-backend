from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
import os
from django.shortcuts import render

from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from base.models import Coupon, CouponRedemption, Product, ProductPriceHistory, Review
from base.roles import IsManager, is_manager_of
from base.serializers import CouponRedemptionSerializer, CouponSerializer, ProductSerializer, Price_History_Serializer
from base.tenancy import public_business
import datetime
from rest_framework import status


def _visible_products(request):
    """Products of the shop being visited. Its managers asking for `scope=all` also get
    products hidden from the website."""
    shop = public_business(request)
    products = Product.objects.filter(business=shop)
    if not (request.query_params.get('scope') == 'all' and is_manager_of(request.user, shop)):
        products = products.filter(sell_online=True)
    return products


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _shop_product(request, pk):
    """A product of the shop being visited, or None (also for ids from other shops)."""
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        return None
    return Product.objects.filter(business=public_business(request), _id=pk).first()
from base.views.helper_file.history_model import *

@api_view(['GET'])
@permission_classes([IsManager])
def getCoupons(request):
    """This business's coupons and who they were given to. Managers only: these are codes."""
    coupon_red = CouponRedemption.objects.filter(business=request.business, is_used=False)
    coupon_redemptions = CouponRedemptionSerializer(coupon_red, many=True)
    coupons = CouponSerializer(Coupon.objects.filter(business=request.business), many=True)
    return Response({'coupon_redemptions': coupon_redemptions.data, 'coupons': coupons.data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getMyCoupons(request):
    """The signed-in customer's own unused coupons from the shop they are visiting."""
    mine = CouponRedemption.objects.filter(business=public_business(request), user_id=request.user, is_used=False)
    return Response(CouponRedemptionSerializer(mine, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
def getCouponStatus(request):
    """Check a coupon code for the signed-in customer. The customer comes from the
    login, never from the request body."""
    code = str(request.data.get('coupon_code') or '').strip()
    redemption = CouponRedemption.objects.filter(business=public_business(request), user_id=request.user,
                                                 coupon_code__coupon_code__iexact=code, is_used=False).first() if code else None
    if redemption:
        return Response({'status': 3, 'total_discount': redemption.total_discount, 'coupon_id': redemption.id})
    return Response({'status': 2, 'total_discount': 0})

getCouponStatus.cls.throttle_scope = 'coupon'


@api_view(['GET'])
# for get products
def getProducts(request):
    params = request.query_params
    query = (params.get('keyword') or '').strip()

    # Search text matches the product name or its category.
    # Price filters and sorting use the price shown on the website.
    products = _visible_products(request).annotate(shown_price=Coalesce('online_price', 'price'))
    if query:
        products = products.filter(Q(name__icontains=query) | Q(category__icontains=query) | Q(code__iexact=query))
    if params.get('category'):
        products = products.filter(category__icontains=params['category'].strip())
    # Admin filters for where a product is sold. They only matter on the admin list.
    channel = params.get('channel')
    if channel == 'both':
        products = products.filter(sell_online=True, sell_in_shop=True)
    elif channel == 'shop_only':
        products = products.filter(sell_online=False, sell_in_shop=True)
    elif channel == 'online_only':
        products = products.filter(sell_online=True, sell_in_shop=False)
    elif channel == 'hidden':
        products = products.filter(sell_online=False, sell_in_shop=False)

    # Optional price range, in rupees.
    try:
        if params.get('min_price'):
            products = products.filter(shown_price__gte=float(params['min_price']))
        if params.get('max_price'):
            products = products.filter(shown_price__lte=float(params['max_price']))
    except ValueError:
        pass

    if params.get('in_stock') in ('1', 'true'):
        products = products.filter(countInStock__gte=1)

    sort_options = {
        'newest': '-createdAt',
        'price_asc': 'shown_price',
        'price_desc': '-shown_price',
        'rating': '-rating',
        'name': 'name',
    }
    order = sort_options.get(params.get('sort'), '-createdAt')
    products = products.order_by(order, '-_id')

    page = request.query_params.get('page')
    # 8 per page for the shop; the admin list can ask for more.
    try:
        page_size = min(max(int(params.get('page_size') or 8), 1), 100)
    except ValueError:
        page_size = 8
    paginator = Paginator(products, page_size)

    try:
        products = paginator.page(page)
    except PageNotAnInteger:
        products = paginator.page(1)
    except EmptyPage:
        products = paginator.page(paginator.num_pages)

    if page == None:
        page = 1

    page = int(page)
    serializer = ProductSerializer(products, many=True)
    return Response({'products': serializer.data, 'page': page, 'pages': paginator.num_pages, 'count': paginator.count})


@api_view(['GET', 'POST'])
def getAllProducts(request):
    products = _visible_products(request)
    serializer = ProductSerializer(products, many=True)
    if request.method == 'POST':
        data = request.data
        # Only products of this shop can be compared.
        pro1 = _shop_product(request, data.get('id_1'))
        pro2 = _shop_product(request, data.get('id_2'))

        if pro1 and pro2:
            product_price_history1 = ProductPriceHistory.objects.filter(
                product=pro1).order_by('createdAt')  # -createdAt // reverse
            price_history1 = Price_History_Serializer(product_price_history1, many=True)
            product_price_history2 = ProductPriceHistory.objects.filter(
                product=pro2).order_by('createdAt')  # -createdAt // reverse
            price_history2 = Price_History_Serializer(
                product_price_history2, many=True) 
            return Response({'products': serializer.data, 'price_history1': price_history1.data, 'price_history2': price_history2.data})
    return Response(serializer.data)


#for get top products

@api_view(['GET'])
def getTopProducts(request):
    products = Product.objects.filter(business=public_business(request), sell_online=True, rating__gte=4).order_by('-rating')[0:5]
    serializer = ProductSerializer(products, many=True)
    return Response(serializer.data)


@api_view(['GET'])
def getOfferProducts(request):
    products = Product.objects.filter(business=public_business(request), sell_online=True, is_offer=True).order_by('-offer_percentage')[0:5]
    serializer = ProductSerializer(products, many=True)
    return Response(serializer.data)


@api_view(['GET','POST'])
def getProduct(request, pk):
    product = _shop_product(request, pk)
    # Products hidden from the website are only visible to that shop's managers.
    if not product or (not product.sell_online and not is_manager_of(request.user, product.business)):
        return Response({'detail': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)
    product_price_history = ProductPriceHistory.objects.filter(product=product).order_by('createdAt')
    price_history = Price_History_Serializer(product_price_history, many=True)
    serializer = ProductSerializer(product, many=False)
    return Response({'product': serializer.data, 'price_history': price_history.data})


@api_view(['POST'])
def predict_history_price(request):
   # print("hello I am Called !!!!!!!!!!!!!!!!!!!!")
    #print(request.data['date'], request.data['product_id'])
    date = request.data.get('date')
    product = _shop_product(request, request.data.get('product_id'))
    if not product:
        return Response({'detail': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)
    predictFuture_price = history_model(date, product._id)
    return Response(predictFuture_price)
        
    


class ProductFormError(Exception):
    pass


def _decimal_field(data, key, label, required=False):
    value = data.get(key)
    if value in (None, ''):
        if required:
            raise ProductFormError(f'{label} is required.')
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ProductFormError(f'{label} must be a number.')
    if not number.is_finite() or number < 0:
        raise ProductFormError(f'{label} cannot be negative.')
    return number


def _apply_product_form(product, data):
    """Copy the product form onto a product. Fields left out of `data` keep their value.
    Stock is not set here; the caller records any change in the stock history."""
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            raise ProductFormError('Item name is required.')
        product.name = name[:200]
    if 'category' in data:
        product.category = (data.get('category') or '').strip()[:200]
    for key in ('brand', 'weight'):  # producer and pack size
        if key in data:
            setattr(product, key, (data.get(key) or '').strip()[:200])
    if 'description' in data:
        product.description = data.get('description') or ''
    if 'price' in data:
        product.price = _decimal_field(data, 'price', 'Shop price', required=True)
    if 'is_offer' in data:
        product.is_offer = bool(data.get('is_offer'))
    if 'offer_percentage' in data:
        try:
            pct = int(data.get('offer_percentage') or 0)
        except (TypeError, ValueError):
            raise ProductFormError('Offer percentage must be a whole number.')
        if not 0 <= pct <= 100:
            raise ProductFormError('Offer percentage must be between 0 and 100.')
        product.offer_percentage = pct
    from base.gst import GSTError, clean_hsn, clean_rate
    try:
        if 'hsn_code' in data:
            product.hsn_code = clean_hsn(data.get('hsn_code'))
        if 'gst_rate' in data:
            product.gst_rate = clean_rate(data.get('gst_rate') or 0)
    except GSTError as e:
        raise ProductFormError(str(e))
    for key in ('sell_online', 'sell_in_shop', 'sold_by_weight'):
        if key in data:
            setattr(product, key, bool(data.get(key)))
    if 'online_price' in data:
        product.online_price = _decimal_field(data, 'online_price', 'Online price')
    if 'code' in data:
        product.code = (str(data.get('code') or '').strip()[:20]) or None
    if 'unit' in data and (data.get('unit') or '').strip():
        product.unit = data['unit'].strip()[:40]


def _stock_from(data, product):
    stock = _decimal_field(data, 'countInStock', 'Stock') or Decimal('0')
    if not product.sold_by_weight and stock != stock.to_integral_value():
        raise ProductFormError('Stock must be a whole number unless the item is sold by weight.')
    return stock


@api_view(['POST'])
@permission_classes([IsManager])
def createProduct(request):
    """Create a product from the filled-in form. Nothing is saved until the form is complete."""
    data = request.data
    product = Product(user=request.user, business=request.business, countInStock=0)
    try:
        if not (data.get('name') or '').strip():
            raise ProductFormError('Item name is required.')
        if data.get('price') in (None, ''):
            raise ProductFormError('Shop price is required.')
        _apply_product_form(product, data)
        stock = _stock_from(data, product)
        with transaction.atomic():
            product.save()
            if stock:
                from base.views.inventory_views import _move_stock
                _move_stock(product, stock, 'correction', user=request.user, note='Opening stock when the product was added')
    except ProductFormError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except IntegrityError:
        return Response({'detail': f'The code "{product.code}" is already used by another item.'},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(ProductSerializer(product, many=False).data, status=status.HTTP_201_CREATED)


@api_view(['PUT'])
@permission_classes([IsManager])
def updateProduct(request, pk):
    data = request.data
    product = Product.objects.filter(business=request.business, _id=_as_int(pk)).first()
    if not product:
        return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        _apply_product_form(product, data)
        stock = _stock_from(data, product) if 'countInStock' in data else None
        with transaction.atomic():
            # Lock the row so a counter sale can't change stock while this edit is saved.
            locked = Product.objects.select_for_update().get(business=request.business, _id=product._id)
            product.save(update_fields=[f.name for f in Product._meta.concrete_fields
                                        if f.name not in ('_id', 'countInStock', 'createdAt')])
            # A changed stock number is recorded as a stock count correction, so it shows in the stock history.
            change = (stock - (locked.countInStock or 0)) if stock is not None else 0
            if change:
                from base.views.inventory_views import _move_stock
                _move_stock(locked, change, 'correction', user=request.user,
                            note='Edited on the product page', unit_cost=locked.cost_price)
            product.countInStock = locked.countInStock
    except ProductFormError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except IntegrityError:
        return Response({'detail': f'The code "{product.code}" is already used by another item.'},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(ProductSerializer(product, many=False).data)


@api_view(['DELETE'])
@permission_classes([IsManager])
def deleteProduct(request, pk):
    product = Product.objects.filter(business=request.business, _id=_as_int(pk)).first()
    if not product:
        return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)
    product.delete()
    return Response('Producted Deleted')


@api_view(['POST'])
@permission_classes([IsManager])
def uploadImage(request):
    """Managers upload one of a product's three photos. Returns the photo's new address."""
    product = Product.objects.filter(business=request.business, _id=_as_int(request.data.get('product_id'))).first()
    if not product:
        return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)
    for field in ('image', 'image2', 'image3'):
        upload = request.FILES.get(field)
        if not upload:
            continue
        if not (upload.content_type or '').startswith('image/') or \
                os.path.splitext(upload.name)[1].lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
            return Response({'detail': 'Choose a JPG, PNG, WEBP or GIF photo.'}, status=status.HTTP_400_BAD_REQUEST)
        if upload.size > 8 * 1024 * 1024:
            return Response({'detail': 'The photo is larger than 8 MB.'}, status=status.HTTP_400_BAD_REQUEST)
        setattr(product, field, upload)
        product.save(update_fields=[field])
        return Response({'field': field, 'url': getattr(product, field).url})
    return Response({'detail': 'Choose a photo to upload.'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def createProductReview(request, pk):
    user = request.user
    product = _shop_product(request, pk)
    if not product:
        return Response({'detail': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)
    data = request.data

    # 1 - Review already exists
    alreadyExists = product.review_set.filter(user=user).exists()
    if alreadyExists:
        content = {'detail': 'Product already reviewed'}
        return Response(content, status=status.HTTP_400_BAD_REQUEST)

    # 2 - No Rating or 0
    elif data['rating'] == 0:
        content = {'detail': 'Please select a rating'}
        return Response(content, status=status.HTTP_400_BAD_REQUEST)

    # 3 - Create review
    else:
        review = Review.objects.create(
            user=user,
            product=product,
            name=user.first_name,
            rating=data['rating'],
            comment=data['comment'],
        )

        reviews = product.review_set.all()
        product.numReviews = len(reviews)

        total = 0
        for i in reviews:
            total += i.rating

        product.rating = total / len(reviews)
        product.save()

        return Response('Review Added')
