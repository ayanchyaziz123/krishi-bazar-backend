"""Open a new shop on the platform with an empty catalog and its owner's account.

    python manage.py create_store --name GBI --slug gbi --owner-name Abdullah --owner-email owner@gbi.local

The owner gets a random password, printed once (or --password to choose it); they add their own items and can change
the password after signing in. An existing account is made owner without changing its
password. Nothing is changed if the shop's web name (slug) is already taken.
"""
import secrets
import string

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email, validate_slug
from django.db import transaction

from base.models import Business, Membership


def _password():
    letters = string.ascii_letters + string.digits
    return 'Shop-' + ''.join(secrets.choice(letters) for _ in range(12))


class Command(BaseCommand):
    help = 'Create a new shop (empty catalog) and its owner account.'

    def add_arguments(self, parser):
        parser.add_argument('--name', required=True, help='Shop name, e.g. "GBI".')
        parser.add_argument('--slug', required=True, help='Web name for the shop link (?shop=<slug>), e.g. "gbi".')
        parser.add_argument('--owner-name', required=True)
        parser.add_argument('--owner-email', required=True, help='The owner signs in with this.')
        parser.add_argument('--password', default='', help='Owner password. Leave out for a random one.')
        parser.add_argument('--accent', default='#51aa3d', help='Brand colour, e.g. "#51aa3d".')

    def handle(self, *args, **opts):
        name = opts['name'].strip()
        slug = opts['slug'].strip().lower()
        owner_name = opts['owner_name'].strip()
        email = opts['owner_email'].strip().lower()
        accent = opts['accent'].strip()
        try:
            validate_slug(slug)
            validate_email(email)
        except ValidationError as e:
            raise CommandError(' '.join(e.messages))
        if not name or not owner_name:
            raise CommandError('Shop name and owner name are required.')
        if len(accent) != 7 or not accent.startswith('#'):
            raise CommandError('Accent must look like #51aa3d.')
        chosen = opts['password']
        if chosen:
            from django.contrib.auth.password_validation import validate_password
            try:
                validate_password(chosen)
            except ValidationError as e:
                raise CommandError(' '.join(e.messages))
        if Business.objects.filter(slug=slug).exists():
            raise CommandError(f'A shop with the web name "{slug}" already exists. Nothing was changed.')

        password = None
        with transaction.atomic():
            user = User.objects.filter(username__iexact=email).first()
            if not user:
                password = chosen or _password()
                user = User.objects.create_user(username=email, email=email, password=password, first_name=owner_name)
            business = Business.objects.create(name=name, slug=slug, owner=user, accent=accent, email=email)
            Membership.objects.create(user=user, business=business, role='owner')

        self.stdout.write(self.style.SUCCESS(f'Created shop {business.name} (web name "{slug}") with no items.'))
        self.stdout.write(f'Owner: {owner_name} <{email}>')
        if password and chosen:
            self.stdout.write('Password: the one you chose.')
        elif password:
            self.stdout.write(f'Password: {password}')
            self.stdout.write('This password is shown only once. Ask the owner to change it after signing in.')
        else:
            self.stdout.write('That account already existed; its password was not changed.')
