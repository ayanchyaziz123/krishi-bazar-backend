"""A business's online shop content and custom pages (the website editor).

Visitors read the published content of the shop they are visiting. Managers edit a
draft for their own business, preview it, then publish it (or throw the draft away).
Nothing a manager types reaches customers before publishing, and no business can read
or change another business's content, pages or photos.
"""
import copy
import os
import re

from django.db import IntegrityError
from PIL import Image
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from base.models import SiteContent, SiteImage, SitePage
from base.roles import IsManager, is_manager_of
from base.site_content import DEFAULT_CONTENT, ContentError, clean_content, clean_html
from base.tenancy import public_business

IMAGE_TYPES = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
MAX_PAGES = 50


def _site(business):
    # Each business gets its own copy of the defaults, so editing a draft never changes them.
    site, _ = SiteContent.objects.get_or_create(
        business=business, defaults={'draft': copy.deepcopy(DEFAULT_CONTENT), 'published': copy.deepcopy(DEFAULT_CONTENT)})
    return site


def _with_defaults(content):
    return copy.deepcopy({**DEFAULT_CONTENT, **(content or {})})


def _bad(message, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': message}, status=code)


def _page_json(p, with_body=True):
    data = {'id': p.pk, 'title': p.title, 'slug': p.slug, 'published': p.published,
            'updatedAt': p.updatedAt.isoformat() if p.updatedAt else None}
    if with_body:
        data['body'] = p.body
    return data


@api_view(['GET'])
def site(request):
    """What customers see: this shop's published content and its published pages."""
    business = public_business(request)
    content = _with_defaults(_site(business).published)
    pages = [_page_json(p, with_body=False) for p in SitePage.objects.filter(business=business, published=True)]
    return Response({**content, 'pages': pages})


def _draft_for_editor(site_row, business):
    # An unset colour is shown as the colour the store uses now.
    content = _with_defaults(site_row.draft)
    content['theme'] = {**DEFAULT_CONTENT['theme'], **content.get('theme', {})}
    if not content['theme']['accent']:
        content['theme']['accent'] = business.accent
    if 'logo' not in (site_row.draft or {}).get('theme', {}) and business.logo:
        content['theme']['logo'] = business.logo.url
    return content


@api_view(['GET', 'PUT'])
@permission_classes([IsManager])
def draft(request):
    business = request.business
    site_row = _site(business)
    if request.method == 'PUT':
        try:
            site_row.draft = clean_content(request.data, business.pk)
        except ContentError as e:
            return _bad(str(e))
        site_row.save(update_fields=['draft', 'updatedAt'])
    return Response({
        'draft': _draft_for_editor(site_row, business),
        'unpublished': _with_defaults(site_row.draft) != _with_defaults(site_row.published),
        'published_at': site_row.published_at.isoformat() if site_row.published_at else None,
        'published_by': (site_row.published_by.first_name or site_row.published_by.username) if site_row.published_by else '',
        'pages': [_page_json(p, with_body=False) for p in SitePage.objects.filter(business=business)],
    })


@api_view(['POST'])
@permission_classes([IsManager])
def publish(request):
    business = request.business
    site_row = _site(business)
    site_row.published = site_row.draft
    site_row.published_at = timezone.now()
    site_row.published_by = request.user
    site_row.save()
    # The website's colour and logo are for the online shop only. The admin, counter and
    # receipts keep the store's own colour and logo, set in Settings.
    return Response({'published_at': site_row.published_at.isoformat()})


@api_view(['POST'])
@permission_classes([IsManager])
def discard(request):
    site_row = _site(request.business)
    site_row.draft = site_row.published
    site_row.save(update_fields=['draft', 'updatedAt'])
    return Response({'draft': _draft_for_editor(site_row, request.business)})


@api_view(['POST'])
@permission_classes([IsManager])
def upload_image(request):
    """A photo for a banner, section or page, stored in this business's folder."""
    upload = request.FILES.get('image')
    if not upload:
        return _bad('Choose a photo.')
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in IMAGE_TYPES or not (upload.content_type or '').startswith('image/'):
        return _bad('Choose a JPG, PNG, WEBP or GIF photo.')
    if upload.size > 8 * 1024 * 1024:
        return _bad('The photo is larger than 8 MB.')
    try:
        # Make sure it really is a picture, not another kind of file renamed.
        with Image.open(upload) as picture:
            picture.verify()
        upload.seek(0)
    except Exception:
        return _bad('That file is not a photo the shop can show.')
    saved = SiteImage.objects.create(business=request.business, image=upload, uploaded_by=request.user)
    return Response({'url': saved.image.url}, status=status.HTTP_201_CREATED)


def _clean_page(data, page):
    title = (data.get('title', page.title) or '').strip()[:120]
    if not title:
        raise ContentError('The page needs a title.')
    slug = slugify(data.get('slug') or page.slug or title)[:80]
    if not slug or not re.match(r'^[a-z0-9-]+$', slug):
        raise ContentError('The page address can only use letters, numbers and dashes.')
    page.title, page.slug = title, slug
    if 'body' in data:
        page.body = clean_html(data.get('body'), page.business_id)
    if 'published' in data:
        page.published = bool(data.get('published'))


def _save_page(page, data, created=False):
    try:
        _clean_page(data, page)
        if SitePage.objects.filter(business_id=page.business_id, slug=page.slug).exclude(pk=page.pk).exists():
            raise IntegrityError
        page.save()
    except ContentError as e:
        return _bad(str(e))
    except IntegrityError:
        return _bad(f'There is already a page at /page/{page.slug}. Choose another address.')
    return Response(_page_json(page), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(['GET', 'POST'])
@permission_classes([IsManager])
def pages(request):
    """All of this business's pages, published or not, for the editor."""
    business = request.business
    if request.method == 'GET':
        return Response([_page_json(p, with_body=False) for p in SitePage.objects.filter(business=business)])
    if SitePage.objects.filter(business=business).count() >= MAX_PAGES:
        return _bad(f'A shop can have up to {MAX_PAGES} pages.')
    return _save_page(SitePage(business=business), request.data, created=True)


@api_view(['GET'])
def page_by_slug(request, slug):
    """A custom page of the shop being visited. Its managers can also preview unpublished pages."""
    business = public_business(request)
    page = SitePage.objects.filter(business=business, slug=slug).first()
    if not page or (not page.published and not is_manager_of(request.user, business)):
        return _bad('Page not found.', status.HTTP_404_NOT_FOUND)
    return Response(_page_json(page))


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsManager])
def page_detail(request, pk):
    page = SitePage.objects.filter(business=request.business, pk=pk).first()
    if not page:
        return _bad('Page not found.', status.HTTP_404_NOT_FOUND)
    if request.method == 'GET':
        return Response(_page_json(page))
    if request.method == 'DELETE':
        page.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    return _save_page(page, request.data)
