"""Coupon codes clash across businesses, because the code itself was the table's primary key.

Step 1 of 2: create a new coupon table with a numeric id and copy every coupon into it,
pointing each redemption at its copy. The old table is left untouched here; step 2 swaps
the tables. Reversible: going back removes the new table and the new link.
"""
import django.db.models.deletion
from django.db import migrations, models


def copy_coupons(apps, schema_editor):
    Coupon = apps.get_model('base', 'Coupon')
    NewCoupon = apps.get_model('base', 'NewCoupon')
    CouponRedemption = apps.get_model('base', 'CouponRedemption')
    copies = {}
    for old in Coupon.objects.order_by('coupon_code'):
        copies[old.pk] = NewCoupon.objects.create(
            business_id=old.business_id, coupon_code=old.coupon_code, discount_amount=old.discount_amount,
            discount_type=old.discount_type, expiration_date=old.expiration_date)
    for redemption in CouponRedemption.objects.exclude(coupon_code__isnull=True):
        redemption.coupon_new = copies[redemption.coupon_code_id]
        redemption.save(update_fields=['coupon_new'])
    # Check nothing was lost before the old table is dropped in the next step.
    if NewCoupon.objects.count() != Coupon.objects.count():
        raise RuntimeError('Coupon copy count does not match.')
    if CouponRedemption.objects.filter(coupon_code__isnull=False, coupon_new__isnull=True).exists():
        raise RuntimeError('A coupon redemption lost its coupon.')
    for redemption in CouponRedemption.objects.exclude(coupon_code__isnull=True).select_related('coupon_new'):
        if redemption.coupon_new.coupon_code != redemption.coupon_code_id:
            raise RuntimeError('A coupon redemption points at the wrong coupon.')


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0054_product_photos_per_business'),
    ]

    operations = [
        migrations.CreateModel(
            name='NewCoupon',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('coupon_code', models.CharField(max_length=200)),
                ('discount_amount', models.FloatField()),
                ('discount_type', models.CharField(max_length=200)),
                ('expiration_date', models.DateTimeField()),
                ('business', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='base.business')),
            ],
        ),
        migrations.AddField(
            model_name='couponredemption',
            name='coupon_new',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='base.newcoupon'),
        ),
        migrations.RunPython(copy_coupons, migrations.RunPython.noop),
    ]
