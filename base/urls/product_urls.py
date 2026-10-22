from django.urls import path
from base.views import product_views as views

urlpatterns = [

    path('', views.getProducts, name="products"),
    path('all/', views.getAllProducts, name="all+products"),
    path('coupons/', views.getCoupons, name="coupons"),
    path('coupons/mine/', views.getMyCoupons, name="my-coupons"),
    
    
    path('predict_history_price/', views.predict_history_price,
         name="predict_history_price"),
    
    path('coupon_check/', views.getCouponStatus, name="coupon_check"),
    
    
    path('create/', views.createProduct, name="product-create"),
    path('upload/', views.uploadImage, name="image-upload"),

    path('<str:pk>/reviews/', views.createProductReview, name="create-review"),
    path('top/', views.getTopProducts, name='top-products'),
    path('offer/', views.getOfferProducts, name='offer-products'),
    path('<str:pk>/', views.getProduct, name="product"),

    path('update/<str:pk>/', views.updateProduct, name="product-update"),
    path('delete/<str:pk>/', views.deleteProduct, name="product-delete"),
]
