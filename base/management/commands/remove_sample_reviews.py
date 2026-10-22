"""Delete one business's sample reviews and fix its product ratings. The shared sample
reviewer account is removed once no business has sample reviews left."""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from base.models import Business, Product, Review
from ._sample_reviews import SAMPLE_USER_EMAIL, recompute_rating


class Command(BaseCommand):
    help = "Remove the sample reviews added by add_sample_reviews for one business."

    def add_arguments(self, parser):
        parser.add_argument('--business', required=True, help='Slug of the business, e.g. krishi-bazar.')

    def handle(self, *args, **opts):
        business = Business.objects.filter(slug=opts['business']).first()
        if not business:
            raise CommandError(f"No business with slug {opts['business']!r}.")
        samples = Review.objects.filter(is_sample=True, product__business=business)
        product_ids = set(samples.values_list('product_id', flat=True))
        deleted, _ = samples.delete()
        for product in Product.objects.filter(business=business, _id__in=product_ids):
            recompute_rating(product)
        if not Review.objects.filter(is_sample=True).exists():
            User.objects.filter(username=SAMPLE_USER_EMAIL).delete()
        self.stdout.write(self.style.SUCCESS(f'Removed {deleted} sample reviews from {business.name}.'))
