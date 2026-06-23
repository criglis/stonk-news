from flask import Flask, render_template, jsonify, request
import feedparser
import requests
import pandas as pd
import re
import os
from datetime import datetime, timedelta
from urllib.parse import quote

app = Flask(__name__)

TWELVE_DATA_KEY = os.environ.get('TWELVE_DATA_KEY', '')

# ---------------------------------------------------------------------------
# Price data — Twelve Data REST API (free tier: 800 req/day, no IP blocks)
# Sign up free at https://twelvedata.com  →  copy your API key  →
# add TWELVE_DATA_KEY=<key> in Render dashboard → Environment
# ---------------------------------------------------------------------------

YAHOO_SUFFIX_MAP = {
    # Europe
    'ASE': '.AT', 'XATH': '.AT',
    'LSE': '.L',  'XLON': '.L',
    'XETRA': '.DE', 'XFRA': '.DE',
    'XPAR': '.PA',
    'XAMS': '.AS',
    'XMIL': '.MI',
    'XMAD': '.MC',
    'XLIS': '.LS',
    'XBRU': '.BR',
    'XHEL': '.HE',
    'XWBO': '.VI',
    'XDUB': '.IR',
    'XOSL': '.OL',
    'XSTO': '.ST',
    'XCSE': '.CO',
    'XICE': '.IC',
    'XWAR': '.WA',
    'XPRA': '.PR',
    'XBUD': '.BD',
    'XBUL': '.SO',
    'XBSE': '.RO',
    'XZAG': '.ZA',
    'XLJU': '.LJ',
    # China
    'XSHG': '.SS', 'SSE': '.SS', 'Shanghai': '.SS',
    'XSHE': '.SZ', 'SZSE': '.SZ', 'Shenzhen': '.SZ',
    # Hong Kong
    'XHKG': '.HK', 'HKSE': '.HK',
    # India
    'XNSE': '.NS', 'NSE': '.NS',
    'XBOM': '.BO', 'BSE': '.BO',
    # Japan & Korea
    'XTKS': '.T',  'TSE': '.T',
    'XKRX': '.KS', 'KRX': '.KS', 'KOSDAQ': '.KQ',
    # Southeast Asia
    'XSES': '.SI',
    'XKLS': '.KL',
    'XBKK': '.BK',
    'XIDX': '.JK',
    'XPHS': '.PS',
    # Australia & New Zealand
    'XASX': '.AX', 'ASX': '.AX',
    'XNZE': '.NZ',
    # Middle East
    'XSAU': '.SR', 'Tadawul': '.SR',
    'XDFM': '.AE',
    'XADS': '.AE',
    'XQAT': '.QA',
    'XKUW': '.KW',
    'XBAH': '.BH',
    'XMUS': '.OM',
    'XTAE': '.TA', 'TASE': '.TA',
    # Latin America
    'XBSP': '.SA', 'BOVESPA': '.SA', 'BVMF': '.SA',
    'XMEX': '.MX', 'BMV': '.MX',
    'XBUE': '.BA',
    'XSGO': '.SN', 'BCS': '.SN',
    'XLIM': '.LM',
    'XBOG': '.CL',
    # Africa
    'XJSE': '.JO',
    'XCAI': '.CA',
    'XNSA': '.NL',
    # Canada
    'XTSE': '.TO', 'TSX': '.TO',
    'XTSX': '.V',
    # Taiwan
    'XTAI': '.TW', 'TWSE': '.TW',
}


def _twelvedata_search(ticker):
    """Search Twelve Data for the ticker to find its exchange."""
    try:
        resp = requests.get(
            'https://api.twelvedata.com/symbol_search',
            params={'symbol': ticker, 'apikey': TWELVE_DATA_KEY},
            timeout=10,
        )
        results = resp.json().get('data', [])
        for r in results:
            if r.get('symbol', '').upper() == ticker.upper():
                return r
    except Exception:
        pass
    return None


def _yahoo_symbol(ticker, exchange=''):
    """Convert a ticker + exchange to a Yahoo Finance symbol."""
    suffix = YAHOO_SUFFIX_MAP.get(exchange, '')
    if suffix:
        return ticker + suffix
    return ticker


def _yahoo_search(ticker):
    """Search Yahoo Finance for the correct symbol."""
    try:
        resp = requests.get(
            'https://query1.finance.yahoo.com/v1/finance/search',
            params={'q': ticker, 'quotesCount': 5, 'newsCount': 0},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=10,
        )
        quotes = resp.json().get('quotes', [])
        for q in quotes:
            sym = q.get('symbol', '')
            if sym.upper().startswith(ticker.upper()):
                print('[yahoo-search] {} → {}'.format(ticker, sym))
                return sym
    except Exception:
        pass
    return None


def _get_price_yahoo(ticker, start_str, end_str, exchange_hint=''):
    """Fallback: fetch price data from Yahoo Finance chart API."""
    yahoo_sym = _yahoo_symbol(ticker, exchange_hint)

    if yahoo_sym == ticker:
        searched = _yahoo_search(ticker)
        if searched:
            yahoo_sym = searched

    start_dt = datetime.strptime(start_str, '%Y-%m-%d')
    end_dt   = datetime.strptime(end_str,   '%Y-%m-%d') + timedelta(days=1)
    period1  = int(start_dt.timestamp())
    period2  = int(end_dt.timestamp())

    resp = requests.get(
        'https://query1.finance.yahoo.com/v8/finance/chart/{}'.format(yahoo_sym),
        params={'interval': '1d', 'period1': period1, 'period2': period2},
        headers={'User-Agent': 'Mozilla/5.0'},
        timeout=20,
    )
    data = resp.json()
    result = (data.get('chart', {}).get('result') or [None])[0]
    if not result:
        return None, None, None

    meta       = result.get('meta', {})
    currency   = meta.get('currency', 'USD')
    exchange   = meta.get('fullExchangeName', exchange_hint)
    timestamps = result.get('timestamp', [])
    closes     = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])

    records = []
    for ts, c in zip(timestamps, closes):
        if c is None:
            continue
        records.append({
            'Date':  pd.to_datetime(datetime.utcfromtimestamp(ts).date()),
            'Close': float(c),
        })

    if not records:
        return None, None, None

    df = pd.DataFrame(records).set_index('Date').sort_index()
    print('[yahoo] {} → {} ({} points)'.format(ticker, yahoo_sym, len(df)))
    return df, currency, exchange


def get_price_history(ticker, start_str, end_str):
    """
    Returns (DataFrame with DatetimeIndex + 'Close' column, currency, exchange)
    or raises RuntimeError with a user-friendly message.
    Tries Twelve Data first, falls back to Yahoo Finance.
    """
    if not TWELVE_DATA_KEY:
        raise RuntimeError(
            'TWELVE_DATA_KEY is not configured. '
            'See the setup instructions to add your free API key.'
        )

    resp = requests.get(
        'https://api.twelvedata.com/time_series',
        params={
            'symbol':     ticker,
            'interval':   '1day',
            'start_date': start_str,
            'end_date':   end_str,
            'outputsize': 5000,
            'apikey':     TWELVE_DATA_KEY,
        },
        timeout=20,
    )
    data = resp.json()

    needs_fallback = False
    if data.get('status') == 'error':
        msg = data.get('message', 'Unknown error from Twelve Data')
        print('[twelvedata] {}: {}'.format(ticker, msg))
        needs_fallback = True

    values = data.get('values', []) if not needs_fallback else []
    if not needs_fallback and not values:
        needs_fallback = True

    if needs_fallback:
        search = _twelvedata_search(ticker)
        exc = search.get('exchange', '') if search else ''
        print('[fallback] Trying Yahoo Finance for {} (exchange: {})'.format(ticker, exc))
        return _get_price_yahoo(ticker, start_str, end_str, exc)

    meta     = data.get('meta', {})
    currency = meta.get('currency', 'USD')
    exchange = meta.get('exchange', '')

    records = []
    for v in values:
        try:
            records.append({
                'Date':  pd.to_datetime(v['datetime']),
                'Close': float(v['close']),
            })
        except (KeyError, ValueError):
            continue

    if not records:
        return None, None, None

    df = pd.DataFrame(records).set_index('Date').sort_index()
    return df, currency, exchange


# ---------------------------------------------------------------------------
# FX conversion — Frankfurter API (European Central Bank, free, no key)
# ---------------------------------------------------------------------------

def get_fx_map(currency, start_str, end_str):
    if currency == 'EUR':
        return {}
    url = 'https://api.frankfurter.app/{}..{}?from={}&to=EUR'.format(
        start_str, end_str, currency
    )
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            fx_map = {}
            for date_str, ccy in resp.json().get('rates', {}).items():
                d = datetime.strptime(date_str, '%Y-%m-%d').date()
                fx_map[d] = ccy.get('EUR', 1.0)
            if fx_map:
                print('[fx] {} ECB rates for {}'.format(len(fx_map), currency))
                return fx_map
    except Exception as exc:
        print('[fx] Frankfurter failed for {}: {}'.format(currency, exc))
    return {}


def lookup_fx(d, fx_map, currency):
    if currency == 'EUR' or not fx_map:
        return 1.0
    if d in fx_map:
        return fx_map[d]
    closest = min(fx_map.keys(), key=lambda x: abs((x - d).days))
    return fx_map[closest]


# ---------------------------------------------------------------------------
# News — Google News RSS
# ---------------------------------------------------------------------------

SOURCE_DOMAINS = {
    'reuters.com':      'Reuters',
    'cnbc.com':         'CNBC',
    'marketwatch.com':  'MarketWatch',
    'yahoo.com':        'Yahoo Finance',
    'fool.com':         'The Motley Fool',
    'seekingalpha.com': 'Seeking Alpha',
    'investopedia.com': 'Investopedia',
    'zacks.com':        'Zacks',
    'morningstar.com':  'Morningstar',
    'economist.com':    'The Economist',
}

SOURCES_SITE_QUERY = ' OR '.join('site:' + d for d in SOURCE_DOMAINS)


def get_source_name(url):
    for domain, name in SOURCE_DOMAINS.items():
        if domain in url.lower():
            return name
    return 'Financial News'


def strip_html(text):
    return re.sub(r'<[^>]+>', '', text or '').strip()


def parse_entry_date(entry):
    for attr in ('published_parsed', 'updated_parsed'):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6]).date()
            except Exception:
                pass
    return None


def fetch_news(ticker, start_date, end_date):
    items = []
    seen  = set()
    query     = '{} ({})'.format(ticker, SOURCES_SITE_QUERY)
    gnews_url = (
        'https://news.google.com/rss/search'
        '?q={}&hl=en-US&gl=US&ceid=US:en'.format(quote(query))
    )
    try:
        feed = feedparser.parse(gnews_url, request_headers={'User-Agent': 'Mozilla/5.0'})
        for entry in feed.entries:
            pub_date = parse_entry_date(entry)
            if not pub_date or not (start_date <= pub_date <= end_date):
                continue
            link = entry.get('link', '#')
            if link in seen:
                continue
            seen.add(link)
            title   = strip_html(entry.get('title', 'No title'))
            summary = strip_html(entry.get('summary', ''))
            if len(summary) > 450:
                summary = summary[:447] + '...'
            items.append({
                'date':    pub_date.isoformat(),
                'title':   title,
                'summary': summary,
                'link':    link,
                'source':  get_source_name(link),
            })
    except Exception as exc:
        print('[news] Google News RSS error: {}'.format(exc))
    items.sort(key=lambda x: x['date'])
    return items


def group_news(news_items, price_data, is_weekly):
    grouped = {}
    for item in news_items:
        item_date = datetime.strptime(item['date'], '%Y-%m-%d').date()
        if is_weekly:
            for period in price_data:
                p_end   = datetime.strptime(period['date'],       '%Y-%m-%d').date()
                p_start = datetime.strptime(period['week_start'], '%Y-%m-%d').date()
                if p_start <= item_date <= p_end:
                    grouped.setdefault(period['date'], []).append(item)
                    break
        else:
            grouped.setdefault(item['date'], []).append(item)
    return grouped


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/setup')
def setup_check():
    """Frontend polls this to check if the API key is configured."""
    return jsonify({'ready': bool(TWELVE_DATA_KEY)})


@app.route('/api/analyze')
def analyze():
    ticker    = request.args.get('ticker', '').strip().upper()
    start_str = request.args.get('start',  '').strip()
    end_str   = request.args.get('end',    '').strip()

    if not ticker or not start_str or not end_str:
        return jsonify({'error': 'Please provide ticker, start, and end dates.'}), 400

    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        end_date   = datetime.strptime(end_str,   '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD.'}), 400

    if end_date <= start_date:
        return jsonify({'error': 'End date must be after start date.'}), 400

    delta_days = (end_date - start_date).days
    is_weekly  = delta_days > 28

    try:
        # 1 — Price data
        hist, currency, exchange = get_price_history(ticker, start_str, end_str)
        if hist is None or hist.empty:
            return jsonify({
                'error': 'No price data found for "{}". Check the symbol and date range.'.format(ticker)
            }), 404

        # 2 — FX rates
        fx_map = get_fx_map(currency, start_str, end_str)

        def make_point(d, close):
            fx = lookup_fx(d, fx_map, currency)
            return {
                'date':           d.isoformat(),
                'price':          round(close * fx, 2),
                'original_price': round(float(close), 2),
                'fx_rate':        round(fx, 4),
            }

        # 3 — Build price_data
        if is_weekly:
            weekly     = hist.resample('W').last()
            price_data = []
            for idx, row in weekly.iterrows():
                d = idx.date()
                if d < start_date:
                    continue
                pt               = make_point(d, row['Close'])
                pt['week_start'] = (d - timedelta(days=6)).isoformat()
                price_data.append(pt)
        else:
            price_data = [
                make_point(idx.date(), row['Close'])
                for idx, row in hist.iterrows()
                if start_date <= idx.date() <= end_date
            ]

        if not price_data:
            return jsonify({'error': 'No price data in this date range.'}), 404

        # 4 — News
        news_items     = fetch_news(ticker, start_date, end_date)
        news_by_period = group_news(news_items, price_data, is_weekly)

        # 5 — Stats
        prices    = [p['price'] for p in price_data]
        min_price = min(prices)
        max_price = max(prices)
        min_idx   = prices.index(min_price)
        max_idx   = prices.index(max_price)
        pct_ret   = round((prices[-1] - prices[0]) / prices[0] * 100, 2)

        return jsonify({
            'ticker':    ticker,
            'company':   ticker,
            'currency':  currency,
            'exchange':  exchange,
            'is_etf':    False,
            'is_weekly': is_weekly,
            'prices':    price_data,
            'news':      news_by_period,
            'stats': {
                'start_price': prices[0],
                'end_price':   prices[-1],
                'min_price':   min_price,
                'min_date':    price_data[min_idx]['date'],
                'max_price':   max_price,
                'max_date':    price_data[max_idx]['date'],
                'return':      pct_ret,
            },
        })

    except RuntimeError as exc:
        return jsonify({'error': str(exc), 'setup_needed': True}), 503
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Server error: {}'.format(str(exc))}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
