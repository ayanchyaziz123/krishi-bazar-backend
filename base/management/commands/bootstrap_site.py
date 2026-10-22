"""Prepare a fresh deployment: make sure the first business exists, load its starter
catalog once, and make sure its admin account exists.

Safe to run on every start. It only loads the catalog into a business with no products,
and only creates the admin when DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD are set.
"""
import json
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand

from base.models import Business, Membership, Product

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures' / 'catalog.json'


class Command(BaseCommand):
    help = 'Create the first business, load its starter catalog if empty, and ensure its admin exists.'

    def handle(self, *args, **opts):
        business, created = Business.objects.get_or_create(
            slug=settings.DEFAULT_BUSINESS_SLUG, defaults={'name': 'Krishi Bazar'})
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created business {business.name}.'))

        if Product.objects.filter(business=business).exists():
            self.stdout.write('Catalog already present, skipping.')
        elif FIXTURE.exists():
            # The starter catalog has no business; attach every record to this one.
            records = json.loads(FIXTURE.read_text())
            for record in records:
                if record['model'] in ('base.product', 'base.category'):
                    record['fields']['business'] = business.pk
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as tmp:
                json.dump(records, tmp)
            try:
                call_command('loaddata', tmp.name)
            finally:
                os.unlink(tmp.name)
            self.stdout.write(self.style.SUCCESS(f'Loaded {Product.objects.filter(business=business).count()} products.'))

        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip().lower()
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '')
        if email and password and not User.objects.filter(username=email).exists():
            user = User.objects.create_user(username=email, email=email, password=password, first_name='Admin')
            Membership.objects.get_or_create(user=user, business=business, defaults={'role': 'owner'})
            if not business.owner:
                business.owner = user
                business.save(update_fields=['owner'])
            self.stdout.write(self.style.SUCCESS(f'Created owner account {email}.'))
