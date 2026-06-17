from flask import Flask, render_template, jsonify, request
import yfinance as yf
import feedparser
import requests
import re
import os
from datetime import datetime, timedelta
from urllib.parse import quote

app = Flask(__name__)

# Browser-like session — reduces Yahoo Finance 429 rate-limit errors
_YF_SESSION = requests.Session()
_YF_SESSION.headers.update({
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
})

# ---------------------------------------------------------------------------
# News helpers
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
    url_lower = url.lower()
    for domain, name in SOURCE_DOMAINS.items():
        if domain in url_lower:
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


def fetch_news(ticker, company_name, start_date, end_date):
    items = []
    seen  = set()

    query     = '"{}" OR "{}" ({})'.format(company_name, ticker, SOURCES_SITE_QUERY)
    gnews_url = (
        'https://news.google.com/rss/search'
        '?q={}&hl=en-US&gl=US&ceid=US:en'.format(quote(query))
    )
    yahoo_url = 'https://finance.yahoo.com/rss/headline?s={}'.format(ticker)

    for url in [gnews_url, yahoo_url]:
        try:
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
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
            print('[news] Error fetching {}: {}'.format(url, exc))

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
# FX helpers
# ---------------------------------------------------------------------------

def build_fx_map(currency, start_str, end_str):
    if currency == 'EUR':
        return {}
    pair      = currency + 'EUR=X'
    end_plus  = (datetime.strptime(end_str, '%Y-%m-%d') + timedelta(days=2)).strftime('%Y-%m-%d')
    fx_hist   = yf.Ticker(pair, session=_YF_SESSION).history(start=start_str, end=end_plus)
    return {idx.date(): row['Close'] for idx, row in fx_hist.iterrows()}


def get_fx(d, fx_map, currency):
    if currency == 'EUR' or not fx_map:
        return 1.0
    if d in fx_map:
        return fx_map[d]
    closest = min(fx_map.keys(), key=lambda x: abs((x - d).days))
    return fx_map[closest]


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
        stock = yf.Ticker(ticker, session=_YF_SESSION)

        # Step 1: fast_info — light endpoint, rarely rate-limited
        try:
            fi       = stock.fast_info
            currency = getattr(fi, 'currency', None) or 'USD'
            exchange = getattr(fi, 'exchange', None) or ''
        except Exception:
            currency = 'USD'
            exchange = ''

        # Step 2: full info for company name / ETF details — may 429, so optional
        company_name = ticker
        is_etf       = False
        etf_holdings = []
        try:
            info         = stock.get_info()
            company_name = info.get('longName') or info.get('shortName') or ticker
            currency     = info.get('currency')  or currency
            exchange     = info.get('exchange')  or exchange
            quote_type   = info.get('quoteType', 'EQUITY')
            is_etf       = quote_type in ('ETF', 'MUTUALFUND')
            if is_etf:
                for h in (info.get('holdings') or [])[:5]:
                    etf_holdings.append({
                        'symbol': h.get('symbol',       ''),
                        'name':   h.get('holdingName',  ''),
                        'pct':    round((h.get('holdingPercent') or 0) * 100, 2),
                    })
        except Exception as exc:
            print('[info] Metadata unavailable for {} ({}); using fast_info fallback.'.format(ticker, exc))

        # Step 3: historical prices — different endpoint, usually works fine
        end_fetch = (end_date + timedelta(days=1)).isoformat()
        hist      = stock.history(start=start_str, end=end_fetch)
        if hist.empty:
            return jsonify({
                'error': 'No price data found for "{}". Check the ticker symbol and date range.'.format(ticker)
            }), 404

        # FX conversion map
        fx_map = build_fx_map(currency, start_str, end_fetch)

        def make_point(d, close):
            fx = get_fx(d, fx_map, currency)
            return {
                'date':           d.isoformat(),
                'price':          round(close * fx, 2),
                'original_price': round(close, 2),
                'fx_rate':        round(fx, 4),
            }

        if is_weekly:
            weekly     = hist.resample('W').last()
            price_data = []
            for idx, row in weekly.iterrows():
                d = idx.date()
                if d < start_date:
                    continue
                pt              = make_point(d, row['Close'])
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

        # News
        news_items     = fetch_news(ticker, company_name, start_date, end_date)
        news_by_period = group_news(news_items, price_data, is_weekly)

        # Stats
        prices    = [p['price'] for p in price_data]
        min_price = min(prices)
        max_price = max(prices)
        min_idx   = prices.index(min_price)
        max_idx   = prices.index(max_price)
        pct_ret   = round((prices[-1] - prices[0]) / prices[0] * 100, 2)

        return jsonify({
            'ticker':       ticker,
            'company':      company_name,
            'currency':     currency,
            'exchange':     exchange,
            'is_etf':       is_etf,
            'etf_holdings': etf_holdings,
            'is_weekly':    is_weekly,
            'prices':       price_data,
            'news':         news_by_period,
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
