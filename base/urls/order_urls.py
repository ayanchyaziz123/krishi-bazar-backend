from django.urls import path
from base.views import order_views as views


urlpatterns = [

    path('', views.getOrders, name='orders'),
    path('add/', views.addOrderItems, name='orders-add'),
    path('pay/start/', views.startPayment, name='payment-start'),
    path('pay/confirm/', views.confirmPayment, name='payment-confirm'),
    path('myorders/', views.getMyOrders, name='myorders'),

    path('<str:pk>/deliver/', views.updateOrderToDelivered, name='order-delivered'),
    path('<str:pk>/status/', views.updateOrderStatus, name='order-status'),
    path('<str:pk>/cancel/', views.cancelMyOrder, name='order-cancel'),

    path('<str:pk>/', views.getOrderById, name='user-order'),
    path('<str:pk>/pay/', views.updateOrderToPaid, name='pay'),
]
