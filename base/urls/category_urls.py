from django.urls import path
from base.views import category_views as views

urlpatterns = [
    path('', views.categories, name='categories'),
    path('<int:pk>/', views.category_detail, name='category-detail'),
]
