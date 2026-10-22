from cProfile import Profile
from rest_framework import serializers
from django.contrib.auth.models import User
from rest_framework_simplejwt.tokens import RefreshToken
from .models import Coupon, CouponRedemption, Product, Order, OrderItem, ProductPriceHistory, Profit, UserProfile, ShippingAddress, Review, UserProfile


class UserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField(read_only=True)
    _id = serializers.SerializerMethodField(read_only=True)
    isAdmin = serializers.SerializerMethodField(read_only=True)
    # 'manager', 'cashier', 'stock', or None for customers. isAdmin means manager.
    role = serializers.SerializerMethodField(read_only=True)

    # Staff lists only: False when the person's access to this business is paused.
    active = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = ['id', '_id', 'username', 'email', 'name', 'isAdmin', 'role', 'active']

    def _membership(self, obj):
        # The membership in the business given in the context (staff lists, paused or not),
        # else the person's first active business (the login response). None means customer.
        from base.roles import primary_membership
        cache = self.context.setdefault('_memberships', {})
        if obj.pk not in cache:
            business = self.context.get('business')
            cache[obj.pk] = obj.memberships.filter(business=business).first() if business is not None \
                else primary_membership(obj)
        return cache[obj.pk]

    def _role(self, obj):
        membership = self._membership(obj)
        return membership.role if membership else None

    def get_active(self, obj):
        membership = self._membership(obj)
        return membership.is_active if membership else None

    def get_role(self, obj):
        return self._role(obj)

    def get__id(self, obj):
        return obj.id

    def get_isAdmin(self, obj):
        from base.roles import MANAGER_ROLES
        return self._role(obj) in MANAGER_ROLES

    def get_name(self, obj):
        name = obj.first_name
        if name == '':
            name = obj.email

        return name


class UserSerializerWithToken(UserSerializer):
    token = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = ['id', '_id', 'username', 'email', 'name', 'isAdmin', 'role', 'businesses', 'token']

    businesses = serializers.SerializerMethodField(read_only=True)

    def get_businesses(self, obj):
        """Every business this person works in, for choosing which one to open."""
        return [{'slug': m.business.slug, 'name': m.business.name, 'role': m.role, 'location': m.business.location}
                for m in obj.memberships.select_related('business').filter(is_active=True, business__status='active')]

    def get_token(self, obj):
        token = RefreshToken.for_user(obj)
        return str(token.access_token)


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = '__all__'


class ProductSerializer(serializers.ModelSerializer):
    reviews = serializers.SerializerMethodField(read_only=True)
    # The storefront reads `price` and `countInStock`, so they hold what the website sells:
    # the online price when one is set, and whole units of stock.
    price = serializers.SerializerMethodField()
    countInStock = serializers.SerializerMethodField()
    # Exact values for the admin product editor.
    shop_price = serializers.DecimalField(source='price', max_digits=13, decimal_places=3, read_only=True)
    stock_exact = serializers.FloatField(source='countInStock', read_only=True)

    class Meta:
        model = Product
        fields = '__all__'

    def get_price(self, obj):
        price = obj.online_price if obj.online_price is not None else obj.price
        return None if price is None else str(price)

    def get_countInStock(self, obj):
        # Loose items sell in halves online, so 12.7 kg in stock offers 12.5; the rest in whole units.
        stock = obj.countInStock or 0
        if obj.sold_by_weight:
            return float(int(stock * 2) / 2)
        return int(stock)

    def get_reviews(self, obj):
        reviews = obj.review_set.all()
        serializer = ReviewSerializer(reviews, many=True)
        return serializer.data
    



class Price_History_Serializer(serializers.ModelSerializer):
    class Meta:
        model = ProductPriceHistory
        fields = '__all__'


class Profit_Serializer(serializers.ModelSerializer):
    class Meta:
        model = Profit
        fields = '__all__'

class CouponRedemptionSerializer(serializers.ModelSerializer):
    # The code as text, as before coupons got numeric ids.
    coupon_code = serializers.CharField(source='coupon_code.coupon_code', read_only=True, default=None)

    class Meta:
        model = CouponRedemption
        fields = '__all__'


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = '__all__'


class UserProfileSerilizer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = '__all__'


class ShippingAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingAddress
        fields = '__all__'


class OrderItemSerializer(serializers.ModelSerializer):
    # A plain number (2 or 1.5) rather than "1.500".
    qty = serializers.FloatField(read_only=True)
    unit = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = OrderItem
        fields = '__all__'

    def get_unit(self, obj):
        return (obj.product.unit if obj.product_id and obj.product else '') or ''


class OrderSerializer(serializers.ModelSerializer):
    orderItems = serializers.SerializerMethodField(read_only=True)
    shippingAddress = serializers.SerializerMethodField(read_only=True)
    user = serializers.SerializerMethodField(read_only=True)
    events = serializers.SerializerMethodField(read_only=True)
    next_steps = serializers.SerializerMethodField(read_only=True)
    can_cancel = serializers.SerializerMethodField(read_only=True)

    def get_events(self, obj):
        return [{'status': e.status, 'note': e.note, 'at': e.created_at.isoformat() if e.created_at else None}
                for e in obj.events.all()]

    def get_next_steps(self, obj):
        from base.delivery import next_steps
        return next_steps(obj)

    def get_can_cancel(self, obj):
        # Whether the customer can still cancel it themselves.
        from base.delivery import CUSTOMER_CANCEL_UNTIL
        # Paid online orders are refunded automatically; ones paid to the shop directly are not.
        return obj.order_status in CUSTOMER_CANCEL_UNTIL and (not obj.isPaid or bool(obj.payment_id))

    class Meta:
        model = Order
        fields = '__all__'

    def get_orderItems(self, obj):
        items = obj.orderitem_set.all()
        serializer = OrderItemSerializer(items, many=True)
        return serializer.data

    def get_shippingAddress(self, obj):
        try:
            address = ShippingAddressSerializer(
                obj.shippingaddress, many=False).data
        except:
            address = False
        return address

    def get_user(self, obj):
        user = obj.user
        serializer = UserSerializer(user, many=False)
        return serializer.data
