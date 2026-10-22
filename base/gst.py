"""GST (India) for the counter and the online shop.

A shop is either not registered (no GST), on the composition scheme (no GST charged to
customers; the bill is a "Bill of Supply"), or registered for regular GST. Under regular
GST each line is taxed at its item's rate: CGST + SGST (half each) when the customer is in
the shop's state, IGST when they are in another state. Prices either include GST (like an
MRP) or have it added on top. A bill discount or coupon is shared across the lines before
tax, so the tax is on what the customer actually pays.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal('0.01')

# GST rates in use (percent). Since 22 Sept 2025 most goods are 0, 5 or 18%; 40% for
# luxury and harmful goods. The older 12% and 28% are kept for past records.
RATES = (Decimal('0'), Decimal('0.25'), Decimal('3'), Decimal('5'), Decimal('12'), Decimal('18'), Decimal('28'), Decimal('40'))

# State or union territory -> GST state code (the first two digits of a GSTIN).
STATE_CODES = {
    'Jammu and Kashmir': '01', 'Himachal Pradesh': '02', 'Punjab': '03', 'Chandigarh': '04', 'Uttarakhand': '05',
    'Haryana': '06', 'Delhi': '07', 'Rajasthan': '08', 'Uttar Pradesh': '09', 'Bihar': '10', 'Sikkim': '11',
    'Arunachal Pradesh': '12', 'Nagaland': '13', 'Manipur': '14', 'Mizoram': '15', 'Tripura': '16', 'Meghalaya': '17',
    'Assam': '18', 'West Bengal': '19', 'Jharkhand': '20', 'Odisha': '21', 'Chhattisgarh': '22', 'Madhya Pradesh': '23',
    'Gujarat': '24', 'Dadra and Nagar Haveli and Daman and Diu': '26', 'Maharashtra': '27', 'Karnataka': '29', 'Goa': '30',
    'Lakshadweep': '31', 'Kerala': '32', 'Tamil Nadu': '33', 'Puducherry': '34', 'Andaman and Nicobar Islands': '35',
    'Telangana': '36', 'Andhra Pradesh': '37', 'Ladakh': '38',
}

GSTIN_PATTERN = re.compile(r'^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')
_CHARS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


class GSTError(ValueError):
    pass


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def gstin_ok(gstin):
    """Format and check digit of a GSTIN (15 characters)."""
    if not GSTIN_PATTERN.match(gstin or ''):
        return False
    total = 0
    for i, ch in enumerate(gstin[:14]):
        product = _CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _CHARS[(36 - total % 36) % 36] == gstin[14]


def clean_rate(value):
    try:
        rate = Decimal(str(value))
    except Exception:  # noqa: BLE001 - anything that isn't a number
        raise GSTError('Choose a GST rate.')
    if rate not in RATES:
        raise GSTError(f'GST rate must be one of {", ".join(f"{r:g}%" for r in RATES)}.')
    return rate


def clean_hsn(value):
    hsn = re.sub(r'\s', '', str(value or ''))
    if hsn and not re.fullmatch(r'\d{4}|\d{6}|\d{8}', hsn):
        raise GSTError('HSN code must be 4, 6 or 8 digits.')
    return hsn


def charges_gst(business):
    return business is not None and business.gst_mode == 'regular'


def is_interstate(business, customer_state):
    """IGST when the customer is in another state (both states known)."""
    return bool(business.state and customer_state and customer_state.strip().lower() != business.state.strip().lower())


def tax_lines(lines, discount, inclusive):
    """GST for a bill. `lines` is a list of (line value, rate); `discount` is taken off before
    tax, shared across lines by value. Returns per line (taxable, tax) and the bill's tax,
    taxable value and how much is added on top of the line values (0 when prices include GST)."""
    gross = sum((v for v, _ in lines), Decimal('0'))
    share = (gross - min(discount, gross)) / gross if gross else Decimal('1')
    out, tax_total, taxable_total = [], Decimal('0'), Decimal('0')
    for value, rate in lines:
        after = value * share
        if inclusive:
            tax = money(after * rate / (Decimal(100) + rate))
            taxable = money(after) - tax
        else:
            taxable = money(after)
            tax = money(taxable * rate / Decimal(100))
        out.append((taxable, tax))
        tax_total += tax
        taxable_total += taxable
    return out, tax_total, taxable_total, (Decimal('0') if inclusive else tax_total)


def split(tax_total, interstate):
    """CGST, SGST, IGST for a bill's tax."""
    if interstate:
        return Decimal('0'), Decimal('0'), money(tax_total)
    half = money(tax_total / 2)
    return half, money(tax_total) - half, Decimal('0')
