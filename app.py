from flask import Flask, render_template, jsonify, request
import feedparser
import requests
import re
import os
from datetime import datetime, timedelta
from urllib.parse import quote

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Price data — stooq (free, no API key, not IP-blocked)
# ---------------------------------------------------------------------------

def get_price_history(ticker, start_str, end_str):
    """
    Fetch daily OHLCV from stooq via pandas_datareader.
    Tries <TICKER>.US first (covers all US stocks/ETFs), then bare ticker.
    Returns (DataFrame, stooq_ticker_used) or (None, None).
    """
    from pandas_datareader import data as pdr

    candidates = [ticker + '.US', ticker]
    if '.' in ticker:
        candidates = [ticker, ticker + '.US']

    for sticker in candidates:
        try:
            df = pdr.DataReader(sticker, 'stooq', start_str, end_str)
            if df is not None and not df.empty:
                return df.sort_index(), sticker
        except Exception as exc:
            print('[stooq] {} failed: {}'.format(sticker, exc))

    return None, None


# ---------------------------------------------------------------------------
# FX conversion — Frankfurter API (European Central Bank, free, no key)
# ---------------------------------------------------------------------------

def get_fx_map(currency, start_str, end_str):
    """
    Returns {date: rate} mapping currency -> EUR via ECB/Frankfurter.
    e.g. currency='USD' => how many EUR per 1 USD on each trading day.
    """
    if currency == 'EUR':
        return {}

    url = 'https://api.frankfurter.app/{}..{}?from={}&to=EUR'.format(
        start_str, end_str, currency
    )
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            fx_map = {}
            for date_str, currencies in data.get('rates', {}).items():
                d = datetime.strptime(date_str, '%Y-%m-%d').date()
                fx_map[d] = currencies.get('EUR', 1.0)
            if fx_map:
                print('[fx] Got {} ECB rates for {}'.format(len(fx_map), currency))
                return fx_map
        else:
            print('[fx] Frankfurter returned {}'.format(resp.status_code))
    except Exception as exc:
        print('[fx] Frankfurter failed for {}: {}'.format(currency, exc))

    return {}


def lookup_fx(d, fx_map, currency):
    """Get FX rate for date d, using nearest available trading day if needed."""
    if currency == 'EUR' or not fx_map:
        return 1.0
    if d in fx_map:
        return fx_map[d]
    closest = min(fx_map.keys(), key=lambda x: abs((x - d).days))
    return fx_map[closest]


# ---------------------------------------------------------------------------
# News — Google News RSS (free, no key, aggregates all target sources)
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
    """Search Google News RSS for articles about ticker within date range."""
    items = []
    seen  = set()

    query     = '{} ({})'.format(ticker, SOURCES_SITE_QUERY)
    gnews_url = (
        'https://news.google.com/rss/search'
        '?q={}&hl=en-US&gl=US&ceid=US:en'.format(quote(query))
    )

    try:
        feed = feedparser.parse(
            gnews_url,
            request_headers={'User-Agent': 'Mozilla/5.0'}
        )
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
        # 1 — Price data from stooq
        hist, stooq_ticker = get_price_history(ticker, start_str, end_str)
        if hist is None or hist.empty:
            return jsonify({
                'error': (
                    'No price data found for "{}". '
                    'Check the ticker symbol and date range. '
                    'Note: cryptocurrency is not supported.'.format(ticker)
                )
            }), 404

        # stooq .US tickers are priced in USD; detect currency from suffix
        currency = 'EUR' if stooq_ticker.endswith('.DE') else 'USD'

        # 2 — FX rates from Frankfurter/ECB
        fx_map = get_fx_map(currency, start_str, end_str)

        def make_point(d, close):
            fx = lookup_fx(d, fx_map, currency)
            return {
                'date':           d.isoformat(),
                'price':          round(close * fx, 2),
                'original_price': round(float(close), 2),
                'fx_rate':        round(fx, 4),
            }

        # 3 — Build price_data list
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

        # 4 — News from Google News RSS
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
            'exchange':  '',
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
