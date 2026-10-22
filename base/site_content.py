"""What the website editor may store, and the starting content. Content belongs to one
business; nothing here reads or writes the database.

Everything the editor sends is checked here: only known section types and fields are
kept, text is trimmed to a sensible length, and links and image addresses must be
internal pages or http(s) addresses. Custom page text is cleaned so only plain
formatting survives.
"""
import re
import uuid
from html import escape
from html.parser import HTMLParser

SECTION_TYPES = ('hero', 'slides', 'categories', 'products', 'promo', 'text_image', 'catalog')
PRODUCT_SOURCES = ('picked', 'offers', 'newest', 'top_rated', 'category')


def new_id():
    return uuid.uuid4().hex[:10]


DEFAULT_CONTENT = {
    'announcement': {'enabled': False, 'text': '', 'link': ''},
    'sections': [
        {'id': 'categories', 'type': 'categories', 'hidden': False,
         'title': 'Shop by category', 'subtitle': "Everything from the field, the garden and the craftsman's bench."},
        {'id': 'catalog', 'type': 'catalog', 'hidden': False,
         'title': 'Fresh from the farm', 'subtitle': 'New harvests and handmade goods, added as they arrive.'},
    ],
    'menu': [
        {'label': 'Shop', 'link': '/'},
        {'label': 'Shop by Budget', 'link': '/priceRange'},
        {'label': 'Top Reviewed', 'link': '/topReviewProductScreen'},
        {'label': 'Contact', 'link': '/contact'},
    ],
    'footer_links': [
        {'label': 'Shop all products', 'link': '/'},
        {'label': 'Top reviewed', 'link': '/topReviewProductScreen'},
        {'label': 'Shop by budget', 'link': '/priceRange'},
        {'label': 'Contact us', 'link': '/contact'},
    ],
    # Social media pages shown as icons in the footer. Empty ones are not shown.
    'social': {'website': '', 'facebook': '', 'instagram': '', 'youtube': ''},
    # What search engines and link previews show. Empty means the store name and tagline.
    'seo': {'title': '', 'description': ''},
    # The shop's colour (empty keeps the current store colour) and the header's look.
    # Publishing makes the colour the store colour everywhere: shop, admin and receipts.
    'theme': {'accent': '', 'header': 'solid', 'logo': '', 'show_name': True},
}

HEADER_STYLES = ('solid', 'gradient')

SOCIAL_NETWORKS = ('website', 'facebook', 'instagram', 'youtube')


class ContentError(ValueError):
    pass


def _text(value, limit):
    return str(value or '').strip()[:limit]


def clean_link(value):
    """An internal page ("/contact", "/page/about-us") or a full http(s) address."""
    link = _text(value, 300)
    if not link:
        return ''
    if link.startswith('#/'):
        link = link[1:]
    if (link.startswith('/') and not link.startswith('//')) or re.match(r'^https?://[^\s"<>]+$', link):
        return link
    raise ContentError(f'"{link}" is not a valid link. Use a page like /contact or a full https:// address.')


def clean_image(value, business_id=None):
    """An uploaded photo of this business, or a full http(s) address."""
    url = _text(value, 400)
    if not url:
        return ''
    if business_id is not None and url.startswith(f'/images/business/{business_id}/') and '..' not in url:
        return url
    if re.match(r'^https?://[^\s"<>]+$', url):
        return url
    raise ContentError('Images must be uploaded here or be a full https:// address.')


def clean_web_address(value, label):
    url = _text(value, 300)
    if url and not re.match(r'^https?://[^\s"<>]+$', url):
        raise ContentError(f'{label} must be a full https:// address.')
    return url


def _links(items, limit, label):
    if not isinstance(items, list):
        raise ContentError(f'{label} must be a list.')
    if len(items) > limit:
        raise ContentError(f'{label} can have at most {limit} links.')
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _text(item.get('label'), 40)
        if text:
            out.append({'label': text, 'link': clean_link(item.get('link')) or '/'})
    return out


def _section(raw, business_id):
    if not isinstance(raw, dict) or raw.get('type') not in SECTION_TYPES:
        raise ContentError('Unknown section type.')
    kind = raw['type']
    s = {'id': _text(raw.get('id'), 20) or new_id(), 'type': kind, 'hidden': bool(raw.get('hidden')),
         'title': _text(raw.get('title'), 120), 'subtitle': _text(raw.get('subtitle'), 300)}
    image = lambda key: clean_image(raw.get(key), business_id)  # noqa: E731
    image_of = lambda slide: clean_image(slide.get('image'), business_id)  # noqa: E731
    if kind == 'hero':
        s.update(badge=_text(raw.get('badge'), 60), text=_text(raw.get('text'), 400),
                 button_label=_text(raw.get('button_label'), 40), button_link=clean_link(raw.get('button_link')),
                 second_label=_text(raw.get('second_label'), 40), second_link=clean_link(raw.get('second_link')),
                 image=image('image'))
    elif kind == 'slides':
        # A banner slider: photos that change every few seconds, each with an optional link.
        slides = raw.get('slides') or []
        if not isinstance(slides, list) or len(slides) > 8:
            raise ContentError('A banner slider can have at most 8 photos.')
        s['slides'] = [{'image': image_of(sl), 'link': clean_link(sl.get('link')), 'alt': _text(sl.get('alt'), 120)}
                       for sl in slides if isinstance(sl, dict) and sl.get('image')]
    elif kind == 'categories':
        cats = raw.get('categories') or []
        s['categories'] = [_text(c, 60) for c in cats if _text(c, 60)][:20] if isinstance(cats, list) else []
    elif kind == 'products':
        source = raw.get('source') if raw.get('source') in PRODUCT_SOURCES else 'newest'
        ids = raw.get('product_ids') or []
        s.update(source=source, category=_text(raw.get('category'), 60),
                 product_ids=[int(i) for i in ids if str(i).isdigit()][:24] if isinstance(ids, list) else [],
                 limit=raw.get('limit') if raw.get('limit') in (4, 8, 12) else 4,
                 link_label=_text(raw.get('link_label'), 40), link=clean_link(raw.get('link')))
    elif kind == 'promo':
        s.update(text=_text(raw.get('text'), 400), button_label=_text(raw.get('button_label'), 40),
                 button_link=clean_link(raw.get('button_link')), image=image('image'),
                 style=raw.get('style') if raw.get('style') in ('brand', 'dark', 'light') else 'brand')
    elif kind == 'text_image':
        s.update(text=_text(raw.get('text'), 2000), image=image('image'),
                 image_side='left' if raw.get('image_side') == 'left' else 'right',
                 button_label=_text(raw.get('button_label'), 40), button_link=clean_link(raw.get('button_link')))
    return s


def clean_content(raw, business_id):
    """The editor's content, checked and trimmed. Photos must belong to this business.
    Raises ContentError with a plain message."""
    if not isinstance(raw, dict):
        raise ContentError('The website content could not be read.')
    sections = raw.get('sections') or []
    if not isinstance(sections, list) or len(sections) > 30:
        raise ContentError('The home page can have at most 30 sections.')
    cleaned = [_section(s, business_id) for s in sections]
    if sum(1 for s in cleaned if s['type'] == 'catalog') > 1:
        raise ContentError('The home page can show the full product list only once.')
    ann = raw.get('announcement') or {}
    return {
        'announcement': {'enabled': bool(ann.get('enabled')), 'text': _text(ann.get('text'), 200),
                         'link': clean_link(ann.get('link'))},
        'sections': cleaned,
        'menu': _links(raw.get('menu', DEFAULT_CONTENT['menu']), 10, 'The menu'),
        'footer_links': _links(raw.get('footer_links', DEFAULT_CONTENT['footer_links']), 12, 'The footer'),
        'social': {name: clean_web_address((raw.get('social') or {}).get(name), name.title() + ' link')
                   for name in SOCIAL_NETWORKS},
        'seo': {'title': _text((raw.get('seo') or {}).get('title'), 70),
                'description': _text((raw.get('seo') or {}).get('description'), 160)},
        'theme': _theme(raw.get('theme') or {}, business_id),
    }


def _theme(raw, business_id):
    accent = _text(raw.get('accent'), 7).lower()
    if accent and not re.match(r'^#[0-9a-f]{6}$', accent):
        raise ContentError('Choose the colour from the list or as #RRGGBB.')
    header = raw.get('header') if raw.get('header') in HEADER_STYLES else 'solid'
    # The logo must be a photo uploaded to this business (it also becomes the browser tab icon).
    logo = _text(raw.get('logo'), 400)
    if logo and not (logo.startswith(f'/images/business/{business_id}/') and '..' not in logo):
        raise ContentError('Upload the logo here: it must be a photo uploaded to your shop.')
    return {'accent': accent, 'header': header, 'logo': logo, 'show_name': raw.get('show_name') is not False}


# --- Custom page text -------------------------------------------------------------

ALLOWED_TAGS = {'p', 'br', 'h2', 'h3', 'h4', 'strong', 'b', 'em', 'i', 'u', 'ul', 'ol', 'li', 'a', 'blockquote', 'hr',
                'figure', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td'}
VOID_TAGS = {'br', 'hr', 'img'}
DROP_WITH_CONTENT = {'script', 'style', 'iframe', 'object', 'embed', 'noscript', 'template'}


class _Cleaner(HTMLParser):
    def __init__(self, business_id):
        super().__init__(convert_charrefs=True)
        self.business_id = business_id
        self.out = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED_TAGS:
            return
        kept = []
        for name, value in attrs:
            value = value or ''
            if tag == 'a' and name == 'href':
                try:
                    link = clean_link(value) if not value.startswith('mailto:') else value
                except ContentError:
                    continue
                kept.append(f'href="{escape(link)}"')
            elif tag == 'img' and name in ('src', 'alt'):
                if name == 'src':
                    try:
                        value = clean_image(value, self.business_id)
                    except ContentError:
                        continue
                kept.append(f'{name}="{escape(value)}"')
        self.out.append(f"<{tag}{(' ' + ' '.join(kept)) if kept else ''}>")

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(self.skip - 1, 0)
            return
        if not self.skip and tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.out.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data, quote=False))


def clean_html(html, business_id):
    cleaner = _Cleaner(business_id)
    cleaner.feed(str(html or '')[:50000])
    cleaner.close()
    return ''.join(cleaner.out)
