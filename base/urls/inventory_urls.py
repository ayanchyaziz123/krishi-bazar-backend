from django.urls import path
from base.views import inventory_views as views

urlpatterns = [
    path('products/', views.inventory_products, name='inventory-products'),
    path('products/<int:pk>/', views.update_inventory_product, name='inventory-product-update'),
    path('adjust/', views.adjust_stock, name='inventory-adjust'),
    path('movements/', views.stock_movements, name='inventory-movements'),
    path('sales/', views.sales, name='inventory-sales'),
    path('sales/<int:pk>/', views.sale_detail, name='inventory-sale'),
    path('sales/<int:pk>/collect/', views.collect_due, name='inventory-sale-collect'),
    path('sales/<int:pk>/return/', views.return_items, name='inventory-sale-return'),
    path('sales/<int:pk>/void/', views.void_sale, name='inventory-sale-void'),
    path('dues/', views.dues, name='inventory-dues'),
    path('transactions/', views.transactions, name='inventory-transactions'),
    path('gst/', views.gst_report, name='inventory-gst'),
    path('suppliers/', views.suppliers, name='inventory-suppliers'),
    path('purchases/', views.purchases, name='inventory-purchases'),
    path('purchases/<int:pk>/', views.purchase_detail, name='inventory-purchase'),
    path('purchases/<int:pk>/receipt/', views.purchase_receipt, name='inventory-purchase-receipt'),
    path('purchases/<int:pk>/pay/', views.pay_supplier, name='inventory-purchase-pay'),
    path('products/<int:pk>/batches/', views.product_batches, name='inventory-product-batches'),
    path('summary/', views.summary, name='inventory-summary'),
    path('home/', views.home, name='inventory-home'),
]
