from django.contrib import admin
from .models import *
# Register your models here.

admin.site.register(Product)
admin.site.register(Review)
admin.site.register(Order)
admin.site.register(OrderItem)
admin.site.register(ShippingAddress)
admin.site.register(CouponRedemption)
admin.site.register(Coupon)
admin.site.register(UserProfile)
admin.site.register(ProductPriceHistory)
admin.site.register(TempUser)
admin.site.register(Profit)


@admin.register(Farmer)
class FarmerAdmin(admin.ModelAdmin):
    list_display = ('name', 'role', 'location', 'is_featured')
    list_filter = ('is_featured',)
    filter_horizontal = ('products',)


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('id', 'createdAt', 'customer_name', 'payment_method', 'total', 'amount_paid')
    list_filter = ('payment_method',)
    inlines = [SaleItemInline]


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ('id', 'createdAt', 'supplier', 'invoice_no', 'total_cost')
    inlines = [PurchaseItemInline]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name', 'phone', 'address')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('createdAt', 'product', 'change', 'stock_after', 'reason', 'note')
    list_filter = ('reason',)
