from django.urls import path
from base.views import farmer_views as views

urlpatterns = [
    path('featured/', views.featured_farmer, name="featured-farmer"),
]
