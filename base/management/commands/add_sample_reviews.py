"""Add clearly marked sample reviews to every product, for testing ratings and review layouts."""
import random

from django.core.management.base import BaseCommand, CommandError

from base.models import Business, Product, Review
from ._sample_reviews import recompute_rating, sample_user

COMMENTS = {
    5: ['Exactly as described. Well packed and arrived quickly.',
        'Great quality, will order again.',
        'Very happy with this. Good value for the price.'],
    4: ['Good product. Delivery took a day longer than expected.',
        'Nice quality overall. Packaging could be a little better.',
        'Would buy again, just wish the pack was bigger.'],
    3: ['Decent, but not quite what I expected.',
        'Okay for the price. Nothing special.'],
}


class Command(BaseCommand):
    help = "Add sample reviews (marked is_sample) to one business's products. Safe to run again: it replaces that business's earlier samples."

    def add_arguments(self, parser):
        parser.add_argument('--per-product', type=int, default=3)
        parser.add_argument('--seed', type=int, default=7, help='Random seed, so runs are repeatable.')
        parser.add_argument('--business', required=True, help='Slug of the business, e.g. krishi-bazar.')

    def handle(self, *args, **opts):
        business = Business.objects.filter(slug=opts['business']).first()
        if not business:
            raise CommandError(f"No business with slug {opts['business']!r}.")
        rng = random.Random(opts['seed'])
        user = sample_user()
        Review.objects.filter(is_sample=True, product__business=business).delete()
        count = 0
        for product in Product.objects.filter(business=business):
            for n in range(opts['per_product']):
                rating = rng.choices([5, 4, 3], weights=[3, 4, 2])[0]
                Review.objects.create(
                    product=product, user=user, name=f'Sample reviewer {n + 1}',
                    rating=rating, comment=rng.choice(COMMENTS[rating]), is_sample=True,
                )
                count += 1
            recompute_rating(product)
        self.stdout.write(self.style.SUCCESS(f'Added {count} sample reviews. Remove them with: manage.py remove_sample_reviews'))
