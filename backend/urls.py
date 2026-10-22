"""backend URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include, re_path
from base.views.order_views import razorpayWebhook as razorpay_webhook
from django.views.static import serve

from django.conf import settings
from django.views.generic import TemplateView

admin.site.site_header = "Krishi Bazar"
admin.site.site_title = "Krishi Bazar"
admin.site.index_title = "Welcome to Krishi Bazar admin"

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', TemplateView.as_view(template_name='index.html')),
    path('api/products/', include('base.urls.product_urls')),
    path('api/users/', include('base.urls.user_urls')),
    path('api/orders/', include('base.urls.order_urls')),
    path('api/contact/', include('base.urls.contact_urls')),
    path('api/chat/', include('base.urls.chat_urls')),
    path('api/farmers/', include('base.urls.farmer_urls')),
    path('api/inventory/', include('base.urls.inventory_urls')),
    path('api/categories/', include('base.urls.category_urls')),
    path('api/store/', include('base.urls.store_urls')),
    path('api/site/', include('base.urls.site_urls')),
    path('api/payments/razorpay/webhook/', razorpay_webhook, name='razorpay-webhook'),
]

# Product and farmer photos. Served by Django so they also work with DEBUG off.
urlpatterns += [
    re_path(r'^images/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
