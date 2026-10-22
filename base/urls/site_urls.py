from django.urls import path
from base.views import site_views as views

urlpatterns = [
    path('', views.site, name='site'),
    path('draft/', views.draft, name='site-draft'),
    path('publish/', views.publish, name='site-publish'),
    path('discard/', views.discard, name='site-discard'),
    path('images/', views.upload_image, name='site-image'),
    path('pages/', views.pages, name='site-pages'),
    path('pages/<int:pk>/', views.page_detail, name='site-page'),
    path('pages/by-slug/<slug:slug>/', views.page_by_slug, name='site-page-slug'),
]
