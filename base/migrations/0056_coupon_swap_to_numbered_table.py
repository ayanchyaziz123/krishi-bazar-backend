"""Step 2 of 2: drop the old code-keyed coupon table, give the new one its name, and make
codes unique per business (ignoring capitals). Going back restores the old layout and,
with 0055's reverse, the copied data is matched back by code.
"""
import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models


def restore_links(apps, schema_editor):
    # Reverse only: point redemptions back at the old code-keyed coupons.
    Coupon = apps.get_model('base', 'Coupon')
    NewCoupon = apps.get_model('base', 'NewCoupon')
    CouponRedemption = apps.get_model('base', 'CouponRedemption')
    for new in NewCoupon.objects.all():
        if Coupon.objects.filter(pk=new.coupon_code).exists():
            raise RuntimeError(f'Two businesses use coupon code {new.coupon_code!r}; it cannot go back to one shared list.')
        Coupon.objects.create(coupon_code=new.coupon_code, business_id=new.business_id, discount_amount=new.discount_amount,
                              discount_type=new.discount_type, expiration_date=new.expiration_date)
    for redemption in CouponRedemption.objects.exclude(coupon_new__isnull=True).select_related('coupon_new'):
        redemption.coupon_code_id = redemption.coupon_new.coupon_code
        redemption.save(update_fields=['coupon_code'])


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0055_coupon_copy_to_numbered_table'),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, restore_links),
        migrations.RemoveField(model_name='couponredemption', name='coupon_code'),
        migrations.DeleteModel(name='Coupon'),
        migrations.RenameModel(old_name='NewCoupon', new_name='Coupon'),
        migrations.RenameField(model_name='couponredemption', old_name='coupon_new', new_name='coupon_code'),
        migrations.AlterField(
            model_name='couponredemption',
            name='coupon_code',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='base.coupon'),
        ),
        migrations.AddConstraint(
            model_name='coupon',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('coupon_code'), models.F('business'),
                                               name='coupon_code_unique_per_business'),
        ),
    ]
