from django.urls import path
from base.views import user_views as views
from base.views import address_views


urlpatterns = [
    path('login/', views.MyTokenObtainPairView.as_view(),
         name='token_obtain_pair'),

    path('register/', views.registerUser_with_otp, name='register'),
    path('temp_register/', views.tempRegister_user, name='temp_register'),
    path('resetPassword/', views.resetPassword, name='resetPassword'),
    path('profile/', views.getUserProfile, name="users-profile"),
    path('profile/update/', views.updateUserProfile, name="user-profile-update"),
    path('', views.getUsers, name="users"),
    path('staff/', views.createStaff, name='staff-create'),
    path('businesses/', views.myBusinesses, name='my-businesses'),
    path('addresses/', address_views.addresses, name='addresses'),
    path('addresses/<int:pk>/', address_views.address_detail, name='address'),

    path('<str:pk>/', views.getUserById, name='user'),

    path('update/<str:pk>/', views.updateUser, name='user-update'),

    path('delete/<str:pk>/', views.deleteUser, name='user-delete'),

]
