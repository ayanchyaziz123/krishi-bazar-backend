from django.urls import path
from base.views import contact_views as views

urlpatterns = [
    path('', views.contacts, name='contacts'),
    path('create/', views.createContact, name='create-contact'),
    path('<int:pk>/', views.contact_detail, name='contact'),
]
