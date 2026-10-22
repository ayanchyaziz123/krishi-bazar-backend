"""Give existing orders their real status and a starting timeline.

Before this, orders only knew whether they were delivered. Delivered orders become
"delivered"; the rest stay "placed". Each order gets a "placed" event at the time it was
placed, and a "delivered" event at the time it was delivered. Reversing removes the events.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Order = apps.get_model('base', 'Order')
    OrderEvent = apps.get_model('base', 'OrderEvent')
    for order in Order.objects.all().iterator():
        order.order_status = 'delivered' if order.isDelivered else 'placed'
        order.save(update_fields=['order_status'])
        placed = OrderEvent.objects.create(order=order, status='placed', note='Order placed')
        OrderEvent.objects.filter(pk=placed.pk).update(created_at=order.createdAt)
        if order.isDelivered:
            done = OrderEvent.objects.create(order=order, status='delivered')
            if order.deliveredAt:
                OrderEvent.objects.filter(pk=done.pk).update(created_at=order.deliveredAt)


def backwards(apps, schema_editor):
    apps.get_model('base', 'OrderEvent').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0059_checkout_delivery_and_tracking'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
