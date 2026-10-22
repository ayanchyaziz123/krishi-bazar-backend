"""Make the existing shop the first business on the platform.

1. Create the business from the current store settings (slug "krishi-bazar").
2. Assign every existing business-owned record to it.
3. Turn today's staff into memberships with the same roles.
4. Check nothing was left unassigned and no rows were lost; stop if anything is off.

Reversing it only removes the business link and memberships; no shop data is deleted.
"""
import os

from django.db import migrations

TENANT_MODELS = ['Product', 'Order', 'Coupon', 'CouponRedemption', 'Contact', 'Farmer',
                 'Supplier', 'Purchase', 'Sale', 'StockMovement', 'Category']
FIRST_SLUG = 'krishi-bazar'


def forwards(apps, schema_editor):
    Business = apps.get_model('base', 'Business')
    Membership = apps.get_model('base', 'Membership')
    StoreSettings = apps.get_model('base', 'StoreSettings')
    StaffRole = apps.get_model('base', 'StaffRole')
    User = apps.get_model('auth', 'User')

    counts_before = {name: apps.get_model('base', name).objects.count() for name in TENANT_MODELS}
    settings = StoreSettings.objects.filter(pk=1).first()

    # The owner: the configured admin email if it exists, else the earliest
    # superuser, else the earliest staff account.
    owner = None
    configured = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip().lower()
    if configured:
        owner = User.objects.filter(username__iexact=configured).first()
    if not owner:
        owner = User.objects.filter(is_superuser=True).order_by('date_joined', 'id').first()
    if not owner:
        owner = User.objects.filter(is_staff=True).order_by('date_joined', 'id').first()

    business, _ = Business.objects.get_or_create(slug=FIRST_SLUG, defaults={
        'name': (settings.name if settings else '') or 'Krishi Bazar',
        'owner': owner,
        'tagline': settings.tagline if settings else '',
        'location': settings.location if settings else '',
        'address': settings.address if settings else '',
        'phone': settings.phone if settings else '',
        'email': settings.email if settings else '',
        'accent': settings.accent if settings else '#059669',
        'receipt_footer': settings.receipt_footer if settings else 'Thank you for shopping with us.',
    })

    for name in TENANT_MODELS:
        apps.get_model('base', name).objects.filter(business__isnull=True).update(business=business)

    # Staff become members with their current role.
    for user in User.objects.filter(is_staff=True) | User.objects.filter(is_superuser=True):
        role = 'owner' if owner and user.pk == owner.pk else 'manager'
        Membership.objects.get_or_create(user=user, business=business, defaults={'role': role})
    for staff in StaffRole.objects.select_related('user'):
        Membership.objects.get_or_create(user=staff.user, business=business, defaults={'role': staff.role})

    # Integrity checks: every row assigned, no rows gained or lost.
    for name in TENANT_MODELS:
        model = apps.get_model('base', name)
        left = model.objects.filter(business__isnull=True).count()
        if left or model.objects.count() != counts_before[name]:
            raise RuntimeError(f'Migration check failed for {name}: {left} unassigned rows')


def backwards(apps, schema_editor):
    for name in TENANT_MODELS:
        apps.get_model('base', name).objects.update(business=None)
    apps.get_model('base', 'Membership').objects.filter(business__slug=FIRST_SLUG).delete()
    apps.get_model('base', 'Business').objects.filter(slug=FIRST_SLUG).delete()


class Migration(migrations.Migration):
    dependencies = [('base', '0051_business_and_membership')]
    operations = [migrations.RunPython(forwards, backwards)]
