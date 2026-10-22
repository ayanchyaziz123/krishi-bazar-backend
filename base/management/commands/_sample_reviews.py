from django.contrib.auth.models import User
from base.models import Product

SAMPLE_USER_EMAIL = 'sample-reviews@test.local'


def recompute_rating(product):
    """Keep a product's star average and review count in step with its reviews."""
    reviews = list(product.review_set.all())
    product.numReviews = len(reviews)
    product.rating = (sum(r.rating for r in reviews) / len(reviews)) if reviews else 0
    product.save(update_fields=['numReviews', 'rating'])


def sample_user():
    user, created = User.objects.get_or_create(
        username=SAMPLE_USER_EMAIL,
        defaults={'email': SAMPLE_USER_EMAIL, 'first_name': 'Sample reviewer'},
    )
    if created:
        user.set_unusable_password()
        user.save()
    return user
