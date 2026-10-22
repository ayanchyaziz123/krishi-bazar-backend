from email.policy import default
from decimal import Decimal
from django.db import models
from django.db.models.functions import Lower
from datetime import datetime
from django.contrib.auth.models import User

# Create your models here.

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    mobile = models.CharField(max_length=200, default=0)
    otp = models.CharField(max_length=6, default=0)
    

class TempUser(models.Model):
    temp_user_id = models.AutoField(primary_key=True, editable=False)
    temp_user_firstName = models.CharField(max_length=200, null=False, blank=False)
    temp_user_name = models.CharField(max_length=200, null=False, blank=False)
    temp_user_mobile = models.CharField(
        max_length=200, null=False, blank=False)
    temp_user_email = models.CharField(
        max_length=200, null=False, blank=False)
    # The code is stored hashed, like a password, and expires.
    temp_user_otp = models.CharField(max_length=200, null=False, blank=False)
    temp_user_password = models.CharField(max_length=200, null=False, blank=False)
    otp_created_at = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
        
 

class Brand(models.Model):
    brand = models.CharField(primary_key=True, max_length=100)

    
def _product_image_path(instance, filename):
    import os
    import uuid
    # Each business's product photos live in their own folder.
    return f'business/{instance.business_id}/products/{uuid.uuid4().hex}{os.path.splitext(filename)[1].lower()[:8]}'


class Product(models.Model):
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    category = models.CharField(max_length=200, null=True, blank=True)
    brand = models.CharField(max_length=200, null=True, blank=True)
    name = models.CharField(max_length=200, null=True, blank=True)
    model = models.CharField(max_length=300, null=True, blank=True)
    processor = models.CharField(max_length=200, null=True, blank=True)
    display = models.CharField(max_length=200, null=True, blank=True)
    graphics_card = models.CharField(max_length=200, null=True, blank=True)
    ram_memory = models.IntegerField(null=True, blank=True, default=0)
    storage = models.CharField(max_length=200, null=True, blank=True)
    operating_system = models.CharField(max_length=200, null=True, blank=True)
    web_cam = models.CharField(max_length=200, null=True, blank=True)
    weight = models.CharField(max_length=200, null=True, blank=True)
    color = models.CharField(max_length=200, null=True, blank=True)
    battery =  models.CharField(max_length=200, null=True, blank=True)
    warranty = models.CharField(max_length=200, null=True, blank=True)
    
    
    image = models.ImageField(null=True, blank=True, upload_to=_product_image_path,
                              default='/placeholder.png')
    
    image2 = models.ImageField(null=True, blank=True, upload_to=_product_image_path,
                              default='/placeholder.png')
    image3 = models.ImageField(null=True, blank=True, upload_to=_product_image_path,
                              default='/placeholder.png')
    image_credit = models.CharField(max_length=300, null=True, blank=True,
                                    help_text='Photo credit shown under the product image, e.g. "Photo: Name, CC BY-SA 4.0".')
    image_credit_url = models.URLField(max_length=500, null=True, blank=True,
                                       help_text='Link to the photo source or license.')
    
    
    description = models.TextField(null=True, blank=True)
    rating = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True)
    numReviews = models.IntegerField(null=True, blank=True, default=0)
    price = models.DecimalField(
        max_digits=13, decimal_places=3, null=True, blank=True)
    # Decimal so items sold loose by weight can hold stock like 2.5 kg.
    countInStock = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True, default=0)
    createdAt = models.DateTimeField(auto_now_add=True)
    is_offer = models.BooleanField(default=False, null=True, blank=True)
    offer_percentage = models.IntegerField(
        default=0, null=True, blank=True)

    # Inventory fields used by the shop's back office. The storefront ignores them.
    unit = models.CharField(max_length=40, default='piece', blank=True,
                            help_text='How one unit of stock is counted, for example "kg", "500 g pack" or "jar".')
    cost_price = models.DecimalField(max_digits=13, decimal_places=3, null=True, blank=True,
                                     help_text='What the shop paid for one unit. Updated whenever stock is received.')
    reorder_level = models.IntegerField(default=5,
                                        help_text='Warn when stock falls to this number or below.')

    # Where the product is sold. Both channels share one stock count.
    sell_online = models.BooleanField(default=True, help_text='Show and sell this product on the website.')
    sell_in_shop = models.BooleanField(default=True, help_text='Sell this product at the shop counter.')
    online_price = models.DecimalField(max_digits=13, decimal_places=3, null=True, blank=True,
                                       help_text='Price on the website. Leave empty to use the shop price.')
    sold_by_weight = models.BooleanField(default=False,
                                         help_text='Sold loose at the counter, so quantities like 1.5 are allowed.')
    # GST (India): the item's HSN code and GST rate in percent. Used when the shop is GST registered.
    hsn_code = models.CharField(max_length=8, blank=True, default='')
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    code = models.CharField(max_length=20, null=True, blank=True,
                            help_text='Short code the cashier can type at the counter, e.g. "101".')
    _id = models.AutoField(primary_key=True, editable=False)
    


    class Meta:
        constraints = [models.UniqueConstraint(fields=['business', 'code'], condition=models.Q(code__isnull=False),
                                               name='product_code_unique_per_business')]
    def __str__(self):
        return self.name

class ProductPriceHistory(models.Model):
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    price = models.DecimalField(
        max_digits=13, decimal_places=3, null=False, blank=False)
    createdAt = models.DateField(null=False, blank=False)
    

class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    name = models.CharField(max_length=200, null=True, blank=True)
    rating = models.IntegerField(null=True, blank=True, default=0)
    comment = models.TextField(null=True, blank=True)
    is_sample = models.BooleanField(default=False, help_text='Test data. Remove with: manage.py remove_sample_reviews')
    createdAt = models.DateTimeField(auto_now_add=True)
    _id = models.AutoField(primary_key=True, editable=False)

    def __str__(self):
        return str(self.rating)


class Order(models.Model):
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    paymentMethod = models.CharField(max_length=200, null=True, blank=True)
    # Delivered to the customer's address, or collected from the shop (no shipping fee).
    DELIVERY_CHOICES = [('deliver', 'Delivery'), ('pickup', 'Store pick-up')]
    delivery_method = models.CharField(max_length=10, choices=DELIVERY_CHOICES, default='deliver')
    taxPrice = models.DecimalField(
        max_digits=13, decimal_places=3, null=True, blank=True)
    shippingPrice = models.DecimalField(
        max_digits=13, decimal_places=3, null=True, blank=True)
    totalPrice = models.DecimalField(
        max_digits=13, decimal_places=3, null=True, blank=True)
    isPaid = models.BooleanField(default=False)
    paidAt = models.DateTimeField(auto_now_add=False, null=True, blank=True)
    is_offer = models.BooleanField(default=False, null=True, blank=True)
    offer_percentage = models.IntegerField(
        default=0, null=True, blank=True)
    # Where the order is. Delivery: placed, confirmed, packed, out for delivery, delivered.
    # Pick-up: placed, confirmed, packed, ready for pick-up, delivered (collected). Or cancelled.
    STATUS_CHOICES = [('placed', 'Placed'), ('confirmed', 'Confirmed'), ('packed', 'Packed'),
                      ('out_for_delivery', 'Out for delivery'), ('ready_for_pickup', 'Ready for pick-up'),
                      ('delivered', 'Delivered'), ('cancelled', 'Cancelled')]
    order_status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='placed')
    delivery_note = models.CharField(max_length=300, blank=True, default='')
    # GST: how the shop was registered when this was sold, and the tax charged.
    gst_mode = models.CharField(max_length=12, default='none')
    cgst = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    # True when the item prices already included the GST (it was not added on top).
    tax_included = models.BooleanField(default=False)
    # The online payment for this order (Razorpay payment id), used for refunds.
    payment_id = models.CharField(max_length=60, blank=True, default='')
    # The coupon used on this order, so cancelling can make it usable again.
    coupon = models.ForeignKey('CouponRedemption', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    isDelivered = models.BooleanField(default=False)
    deliveredAt = models.DateTimeField(
        auto_now_add=False, null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)
    _id = models.AutoField(primary_key=True, editable=False)

    def __str__(self):
        return str(self.createdAt)


class OrderItem(models.Model):
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True)
    name = models.CharField(max_length=200, null=True, blank=True)
    # Whole units, or halves (1.5 kg) for items sold loose by weight.
    qty = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, default=0)
    price = models.DecimalField(
        max_digits=13, decimal_places=3, null=True, blank=True)
    
    image = models.CharField(max_length=200, null=True, blank=True)
    # GST on this line (filled in when the shop charges GST).
    hsn_code = models.CharField(max_length=8, blank=True, default='')
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    taxable_value = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    _id = models.AutoField(primary_key=True, editable=False)

    def __str__(self):
        return str(self.name)


class ShippingAddress(models.Model):
    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, null=True, blank=True)
    address = models.CharField(max_length=200, null=True, blank=True)
    city = models.CharField(max_length=200, null=True, blank=True)
    postalCode = models.CharField(max_length=200, null=True, blank=True)
    country = models.CharField(max_length=200, null=True, blank=True)
    full_name = models.CharField(max_length=120, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    area = models.CharField(max_length=200, blank=True, default='')
    landmark = models.CharField(max_length=200, blank=True, default='')
    state = models.CharField(max_length=80, blank=True, default='')
    shippingPrice = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True)
    _id = models.AutoField(primary_key=True, editable=False)

    def __str__(self):
        return str(self.address)
class Coupon(models.Model):
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    # Codes are unique within a business (ignoring capitals), so two shops can both have SAVE10.
    coupon_code = models.CharField(max_length=200)
    discount_amount = models.FloatField()
    discount_type = models.CharField(max_length=200)
    expiration_date = models.DateTimeField(auto_now=False, auto_now_add=False)

    class Meta:
        constraints = [models.UniqueConstraint(Lower('coupon_code'), 'business', name='coupon_code_unique_per_business')]

    def __str__(self):
        return str(self.discount_type)
        

class CouponRedemption(models.Model):
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    user_id = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    coupon_code = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True)
    total_discount = models.FloatField()
    redemption_date = models.DateTimeField(auto_now=False, auto_now_add=False)
    is_used = models.BooleanField(default=False)
    status = models.BooleanField(default=False)
    
    def __str__(self):
        return str(self.coupon_code)

    

class Contact(models.Model):
    """A message sent through the shop's contact form."""
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    first_name = models.CharField(max_length=200)
    last_name = models.CharField(max_length=200, blank=True)
    area_code = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=200, blank=True)
    email = models.CharField(max_length=200, blank=True)
    need_contact = models.BooleanField(default=False)
    contact_media = models.CharField(max_length=20, blank=True)
    message = models.CharField(max_length=800)
    reply = models.BooleanField(default=False, help_text='Staff have replied.')
    reply_note = models.CharField(max_length=500, blank=True, help_text='What staff said or did, for the record.')
    replied_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='contact_replies')
    replied_at = models.DateTimeField(null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ['reply', '-createdAt']

    def __str__(self):
        return f'{self.first_name}: {self.message[:40]}'

class Profit(models.Model):
    year = models.IntegerField(null=False, blank=False)
    profit = models.IntegerField(null=False, blank=False)
    revenue = models.IntegerField(null=False, blank=False)
    expenses = models.IntegerField(null=False, blank=False)
    
    def __str__(self):
        return str(self.year)
    
        
    
    
    


class Farmer(models.Model):
    """A grower or artisan featured in the storefront's "Meet the farmer" card."""
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=120, help_text='For example "Rice farmer" or "Bamboo artisan".')
    location = models.CharField(max_length=120, blank=True, help_text='Village or district, for example "Sunamganj".')
    story = models.TextField(help_text='A few sentences in the farmer\'s own words work best.')
    photo = models.ImageField(null=True, blank=True)
    products = models.ManyToManyField(Product, blank=True, related_name='farmers',
                                      help_text='Products this farmer supplies.')
    is_featured = models.BooleanField(default=True, help_text='Show this farmer on the homepage.')
    createdAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-createdAt']

    def __str__(self):
        return f'{self.name} ({self.role})'


# ---------------------------------------------------------------------------
# Inventory: the physical shop's back office.
# Every change to Product.countInStock is recorded as a StockMovement, so the
# stock history of an item can always be explained.
# ---------------------------------------------------------------------------

class Supplier(models.Model):
    """A farmer, tea garden, artisan or wholesaler the shop buys from."""
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=40, blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


def _receipt_storage():
    # Supplier receipts show buying prices, so they live outside the public images folder
    # and are only served to staff through the inventory API.
    import os
    from django.conf import settings
    from django.core.files.storage import FileSystemStorage

    class ReceiptStorage(FileSystemStorage):
        # Read the folder on every use, so a changed RECEIPT_ROOT (e.g. in tests) takes effect.
        @property
        def base_location(self):
            return settings.RECEIPT_ROOT

        @property
        def location(self):
            return os.path.abspath(self.base_location)

    return ReceiptStorage()


def _receipt_path(instance, filename):
    import os
    import uuid
    ext = os.path.splitext(filename)[1].lower()[:8]
    # Each business's receipts live in their own folder.
    return f'{instance.business_id}/{uuid.uuid4().hex}{ext}'


class Purchase(models.Model):
    """One delivery of stock into the shop."""
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    invoice_no = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=255, blank=True)
    total_cost = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    received_on = models.DateField(null=True, blank=True, help_text='The day the goods arrived.')
    amount_paid = models.DecimalField(max_digits=13, decimal_places=3, default=0,
                                      help_text='Paid to the supplier so far.')
    receipt = models.FileField(storage=_receipt_storage, upload_to=_receipt_path, null=True, blank=True,
                               help_text="Photo or PDF of the supplier's receipt.")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-createdAt']

    def __str__(self):
        return f'Purchase #{self.pk}'


class PurchaseItem(models.Model):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    name = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=13, decimal_places=3)
    batch_no = models.CharField(max_length=60, blank=True, help_text='Batch or lot number on the package.')
    # How much of this batch is still on the shelf. Sales take from the oldest stock first.
    remaining = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    expiry_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f'{self.qty} x {self.name}'


class Sale(models.Model):
    """A sale made at the shop counter."""
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    PAYMENT_CHOICES = [
        ('cash', 'Cash'),
        ('upi', 'UPI'),
        ('bkash', 'bKash'),
        ('nagad', 'Nagad'),
        ('card', 'Card'),
        ('due', 'Due (pay later)'),
    ]
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=40, blank=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_CHOICES, default='cash')
    subtotal = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    discount = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    total = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    amount_paid = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    total_cost = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    # Returns: the value of goods brought back (the price paid, after any bill discount) and
    # the buying cost of what went back on the shelf. What is owed is total - returned - paid.
    returned = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    returned_cost = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    voided = models.BooleanField(default=False)
    # A sale written down on paper and typed in afterwards: createdAt is when it was sold,
    # entered_at is when it was typed in.
    # GST: how the shop was registered when this was sold, and the tax charged.
    gst_mode = models.CharField(max_length=12, default='none')
    tax_total = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    cgst = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    entered_later = models.BooleanField(default=False)
    entered_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-createdAt']

    def __str__(self):
        return f'Sale #{self.pk}'


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=40, blank=True)
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=13, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=13, decimal_places=3, default=0)
    returned_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    # GST on this line (filled in when the shop charges GST).
    hsn_code = models.CharField(max_length=8, blank=True, default='')
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    taxable_value = models.DecimalField(max_digits=13, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=13, decimal_places=2, default=0)

    def __str__(self):
        return f'{self.qty} x {self.name}'


class SaleReturn(models.Model):
    """Items brought back from a counter bill, and the money given back. A void returns
    everything on the bill."""
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name='returns')
    KIND_CHOICES = [('return', 'Return'), ('void', 'Void')]
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default='return')
    REFUND_CHOICES = [('cash', 'Cash'), ('upi', 'UPI'), ('card', 'Card'), ('none', 'Taken off what is owed')]
    refund_method = models.CharField(max_length=10, choices=REFUND_CHOICES, default='cash')
    value = models.DecimalField(max_digits=13, decimal_places=3, help_text='Price paid for the returned goods.')
    refund_amount = models.DecimalField(max_digits=13, decimal_places=3, help_text='Money handed back.')
    cost_recovered = models.DecimalField(max_digits=13, decimal_places=3, default=0,
                                         help_text='Buying cost of what went back on the shelf.')
    restocked = models.BooleanField(default=True)
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-createdAt']


class SaleReturnItem(models.Model):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name='items')
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name='+')
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=40, blank=True)
    qty = models.DecimalField(max_digits=12, decimal_places=3)
    amount = models.DecimalField(max_digits=13, decimal_places=3)


class StockMovement(models.Model):
    """One change to a product's stock and the reason for it."""
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    REASON_CHOICES = [
        ('purchase', 'Stock received'),
        ('shop_sale', 'Shop sale'),
        ('online_sale', 'Online order'),
        ('wastage', 'Wastage or damage'),
        ('return', 'Customer return'),
        ('online_cancel', 'Online order cancelled'),
        ('correction', 'Stock count correction'),
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    change = models.DecimalField(max_digits=12, decimal_places=3, help_text='Positive adds stock, negative removes it.')
    stock_after = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    note = models.CharField(max_length=255, blank=True)
    unit_cost = models.DecimalField(max_digits=13, decimal_places=3, null=True, blank=True)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True)
    purchase = models.ForeignKey(Purchase, on_delete=models.SET_NULL, null=True, blank=True)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-createdAt']

    def __str__(self):
        return f'{self.product} {self.change.normalize():+} ({self.reason})'


class Category(models.Model):
    """A product category shown in the shop, the counter and the product editor.

    Products keep their category as text. A product belongs to a category when its
    category text contains the category's match word, so "Pickle" and "Pickles" both match.
    """
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.PROTECT, related_name='+')
    name = models.CharField(max_length=60)
    keyword = models.CharField(max_length=60, help_text='Products whose category contains this word belong here.')
    icon = models.CharField(max_length=30, default='tag')
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['position', 'name']
        constraints = [models.UniqueConstraint(fields=['business', 'name'], name='category_name_unique_per_business')]
        verbose_name_plural = 'categories'

    def __str__(self):
        return self.name


class StaffRole(models.Model):
    """The job of a staff account that is not a manager. Managers use Django's staff flag."""
    ROLE_CHOICES = [('cashier', 'Cashier'), ('stock', 'Stock keeper'), ('orders', 'Online orders')]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_role')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    def __str__(self):
        return f'{self.user.username} ({self.get_role_display()})'


class StoreSettings(models.Model):
    """The store's own name, details and colour. There is one row; every page reads it,
    so the same system can be branded for a different store without code changes."""
    name = models.CharField(max_length=80, default='Krishi Bazar')
    tagline = models.CharField(max_length=160, blank=True, default='Fresh produce and crafts from local farms')
    location = models.CharField(max_length=80, blank=True, default='Sylhet', help_text='Shown next to the store name, e.g. a city or branch.')
    address = models.CharField(max_length=255, blank=True, default='121, Zindabazar, Sylhet, Bangladesh')
    phone = models.CharField(max_length=40, blank=True, default='')
    email = models.CharField(max_length=120, blank=True, default='')
    accent = models.CharField(max_length=7, default='#059669', help_text='Main colour as #RRGGBB. The whole palette is made from it.')
    receipt_footer = models.CharField(max_length=160, blank=True, default='Thank you for shopping with us.')
    updatedAt = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'store settings'

    def __str__(self):
        return self.name

    @classmethod
    def load(cls):
        settings, _ = cls.objects.get_or_create(pk=1)
        return settings


def _business_logo_path(instance, filename):
    import os
    import uuid
    return f'business/{instance.pk or "new"}/logo-{uuid.uuid4().hex[:8]}{os.path.splitext(filename)[1].lower()[:8]}'


class Business(models.Model):
    """One store on the platform. Everything a store owns (products, stock, sales,
    orders, customers' orders, staff memberships) points at its Business."""
    STATUS_CHOICES = [('active', 'Active'), ('suspended', 'Suspended'), ('review', 'Under review')]
    name = models.CharField(max_length=80)
    # Used in the shop address, e.g. /s/krishi-bazar now, or krishi-bazar.<platform> later.
    slug = models.SlugField(max_length=60, unique=True)
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='owned_businesses')
    tagline = models.CharField(max_length=160, blank=True)
    location = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.CharField(max_length=120, blank=True)
    accent = models.CharField(max_length=7, default='#059669')
    receipt_footer = models.CharField(max_length=160, blank=True, default='Thank you for shopping with us.')
    logo = models.ImageField(upload_to=_business_logo_path, null=True, blank=True)
    # GST (India). 'none': not registered (no GST; the online shop's flat tax setting applies).
    # 'regular': GST charged per item, CGST + SGST in the shop's state, IGST across states.
    # 'composition': composition scheme, no GST charged to customers (Bill of Supply).
    GST_MODES = [('none', 'Not registered'), ('regular', 'Regular GST'), ('composition', 'Composition scheme')]
    gst_mode = models.CharField(max_length=12, choices=GST_MODES, default='none')
    gstin = models.CharField(max_length=15, blank=True, default='')
    state = models.CharField(max_length=60, blank=True, default='', help_text="The shop's state, for CGST/SGST or IGST.")
    prices_include_gst = models.BooleanField(default=True, help_text='Selling prices already include GST (like MRP).')
    # Delivery and pick-up for the online shop. The defaults are the original fixed rules.
    delivery_enabled = models.BooleanField(default=True)
    pickup_enabled = models.BooleanField(default=True)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('10'))
    free_delivery_over = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, default=Decimal('100'),
                                             help_text='Delivery is free when the goods cost more than this. Empty: never free.')
    min_order = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('5'), help_text='Percent.')
    delivery_time = models.CharField(max_length=80, blank=True, help_text='e.g. "1-2 days"')
    pickup_time = models.CharField(max_length=80, blank=True, help_text='e.g. "Ready in 2 hours"')
    delivery_pincodes = models.TextField(blank=True, help_text='PIN codes delivered to, e.g. "788001, 7880*". Empty: everywhere.')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    plan = models.CharField(max_length=30, default='free')
    # Reserved for later: a store's own domain, e.g. shop.example.com.
    custom_domain = models.CharField(max_length=253, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'businesses'
        ordering = ['name']

    def __str__(self):
        return self.name


class Membership(models.Model):
    """A person's job in one business. One person can belong to several businesses
    with a different role in each (e.g. owner of one, cashier at another)."""
    ROLE_CHOICES = [('owner', 'Owner'), ('manager', 'Manager'), ('cashier', 'Cashier'),
                    ('stock', 'Stock keeper'), ('orders', 'Online orders')]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='memberships')
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'business'], name='one_membership_per_business')]
        ordering = ['created_at']

    def __str__(self):
        return f'{self.user.username} · {self.role} at {self.business}'



def _site_image_path(instance, filename):
    import os
    import uuid
    # Website photos live in the business's own folder, next to its product photos.
    return f'business/{instance.business_id}/site/{uuid.uuid4().hex}{os.path.splitext(filename)[1].lower()[:8]}'


class SiteContent(models.Model):
    """A business's online shop content: home page sections, announcement bar, menu,
    footer links and search-engine text. Staff edit the draft; customers see the
    published copy. One row per business."""
    business = models.OneToOneField('Business', on_delete=models.CASCADE, related_name='+')
    draft = models.JSONField(default=dict)
    published = models.JSONField(default=dict)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updatedAt = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Website content for {self.business}'


class SitePage(models.Model):
    """A page a business writes itself, e.g. About us or Delivery and returns."""
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='+')
    title = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80)
    body = models.TextField(blank=True)
    published = models.BooleanField(default=False)
    updatedAt = models.DateTimeField(auto_now=True)
    createdAt = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['title']
        constraints = [models.UniqueConstraint(fields=['business', 'slug'], name='site_page_slug_unique_per_business')]

    def __str__(self):
        return self.title


class SiteImage(models.Model):
    """A photo uploaded in the website editor, kept so each file is known to belong to one business."""
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='+')
    image = models.ImageField(upload_to=_site_image_path)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    createdAt = models.DateTimeField(auto_now_add=True)


class OrderEvent(models.Model):
    """One step in an online order's life (placed, packed, cancelled...), for the timeline."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='events')
    status = models.CharField(max_length=30, choices=Order.STATUS_CHOICES)
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']


class CustomerAddress(models.Model):
    """A delivery address a customer saved in one shop."""
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='+')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='addresses')
    label = models.CharField(max_length=20, default='Home')
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    line1 = models.CharField(max_length=200, help_text='House, building and street')
    area = models.CharField(max_length=200, blank=True)
    landmark = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=80, blank=True)
    pincode = models.CharField(max_length=10)
    country = models.CharField(max_length=60, default='India')
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_default', '-updated_at']


class PaymentAttempt(models.Model):
    """A checkout waiting for its online payment. The order is only created once the
    payment is confirmed; until then nothing is reserved and no order exists."""
    # The business this belongs to. Every query for it must be limited to one business.
    business = models.ForeignKey('Business', on_delete=models.CASCADE, related_name='+')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    STATUS_CHOICES = [('created', 'Waiting for payment'), ('paid', 'Paid, order placed'),
                      ('refunded', 'Paid but could not be placed: refunded'), ('failed', 'Failed')]
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='created')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # What was checked out: items with the prices charged, delivery, address, coupon, note.
    payload = models.JSONField()
    razorpay_order_id = models.CharField(max_length=60, unique=True)
    razorpay_payment_id = models.CharField(max_length=60, blank=True)
    order = models.OneToOneField(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='payment_attempt')
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
