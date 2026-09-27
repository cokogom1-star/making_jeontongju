from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
import os
from urllib.parse import urlparse

from flask import Flask, abort, render_template_string

app = Flask(__name__)

from cafe24 import init_app, protected, public_catalog
from coupang import KST, configured as coupang_configured, demo_orders, order_summaries, product_url
from prelaunch import dry_run
init_app(app)


def store_live():
    return os.environ.get('STORE_LIVE', '').lower() == 'true'


def products_for_page(product_no=None):
    try:
        products = public_catalog(product_no)
    except Exception:
        app.logger.error('Catalog request failed')
        return [], '상품 정보를 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.'
    if products is None:
        return [], '상품 준비 중입니다. 카페24 연결 후 이곳에 상품이 표시됩니다.'
    result = []
    for product in products:
        try:
            number = int(product['product_no'])
            if number <= 0:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        image = product.get('list_image') or product.get('detail_image')
        if image and (not isinstance(image, str) or not image.startswith('https://') or not urlparse(image).hostname):
            image = None
        try:
            price = f"{Decimal(str(product['price'])):,.0f}원"
        except (InvalidOperation, KeyError, TypeError):
            price = '가격 문의'
        result.append({'number': number, 'name': product.get('product_name') or '우리술',
                       'image': image, 'price': price,
                       'summary': product.get('summary_description') or '',
                       'selling': product.get('selling') == 'T',
                       'mall_url': f'https://{os.environ.get("CAFE24_MALL_ID", "posteam1")}.cafe24.com/product/detail.html?product_no={number}',
                       'coupang_url': product_url(number)})
    return result, None

HTML = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>우리술 | 시간을 빚다</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f6f2e9;color:#24221e;font-family:Georgia,"Noto Serif KR",serif;line-height:1.7}
header{height:76px;display:flex;align-items:center;justify-content:space-between;padding:0 7%;border-bottom:1px solid #d8d1c4}
.logo{font-size:27px;font-weight:bold;color:inherit;text-decoration:none}nav a{margin-left:25px;color:inherit;text-decoration:none;font-size:14px}
.hero{min-height:650px;padding:10% 9%;display:flex;align-items:center;background:linear-gradient(90deg,rgba(246,242,233,.97),rgba(246,242,233,.68)),url("https://images.unsplash.com/photo-1536935338788-846bb9981813?auto=format&fit=crop&w=1800&q=80") center/cover}
.eyebrow{letter-spacing:3px;font-size:11px}.hero h1{font-size:clamp(45px,7vw,78px);line-height:1.15;font-weight:500;margin:15px 0}.lead{font-size:17px}.button{display:inline-block;margin-top:20px;padding:11px 25px;border:1px solid #24221e;text-decoration:none;color:inherit}
section{max-width:1200px;margin:auto;padding:110px 7%}.intro h2,.story h2{font-size:clamp(35px,5vw,58px);line-height:1.25;font-weight:500}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;padding-top:0}.card{border-top:1px solid #888;padding:30px 0}.card h3{font-size:30px;font-weight:500;margin:15px 0}
.story{border-top:1px solid #d8d1c4}.story p{font-size:18px;max-width:760px}
.catalog{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}.product{color:inherit;text-decoration:none;border:1px solid #d8d1c4;background:#fff;display:block}.product-image{aspect-ratio:1/1;background:#ebe5d9;display:grid;place-items:center;color:#777}.product-image img{width:100%;height:100%;object-fit:cover}.product-copy{padding:18px 22px}.product-copy h3{font-size:22px;font-weight:500;margin:0}.product-copy p{margin:5px 0}.notice{padding:25px 0;color:#665e53}.detail{display:grid;grid-template-columns:1fr 1fr;gap:45px;align-items:center}.detail h1{font-size:clamp(36px,5vw,60px);line-height:1.2;font-weight:500}.detail .product-image{min-height:300px}.back{display:inline-block;margin-top:22px;color:inherit}
.preview{padding:12px 7%;background:#e7dbc4;font-size:14px}
footer{padding:60px 7%;border-top:1px solid #d8d1c4}@media(max-width:700px){nav a{margin-left:10px;font-size:12px}.hero{min-height:570px;padding:18% 7%}.cards{grid-template-columns:1fr}section{padding:80px 7%}}
@media(max-width:700px){.catalog{grid-template-columns:1fr 1fr;gap:12px}.detail{grid-template-columns:1fr}.product-copy{padding:13px}.product-copy h3{font-size:18px}}
</style>
</head>
<body>
{% if not live %}<div class="preview">운영 준비 중 · 현재 상품은 시연용이며 이 사이트에서는 주문을 받지 않습니다.</div>{% endif %}
<header><div class="logo">우리술</div><nav><a href="#about">브랜드</a><a href="#collection">술</a><a href="#contact">문의</a></nav></header>
<div class="hero"><div><div class="eyebrow">TRADITIONAL KOREAN LIQUOR</div><h1>오래된 지혜를<br>오늘의 술로.</h1><p class="lead">우리 조상이 빚어온 술에는 시간과 계절, 사람의 손길이 담겨 있습니다.<br>우리술은 그 가치를 오늘에 전하고자 합니다.</p><a class="button" href="#collection">우리술 만나기</a></div></div>
<section id="about" class="intro"><div class="eyebrow">OUR STORY</div><h2>마시기 위한 술,<br>기억하기 위한 전통.</h2><p>세계에는 각 나라를 대표하는 술이 있습니다. 우리에게도 오랜 시간 이어져 온 훌륭한 술과 그것을 빚어온 지혜가 있습니다.</p><p>우리술은 그 전통을 오늘의 사람들에게 다시 소개하고, 한국의 술 문화를 세계에 전하고자 합니다.</p></section>
<section id="collection"><div class="eyebrow">OUR COLLECTION</div><div class="cards"><div class="card"><span>01</span><h3>청주</h3><p>맑고 섬세한 향을 가진 우리 술</p></div><div class="card"><span>02</span><h3>탁주</h3><p>쌀과 누룩이 만들어내는 깊은 풍미</p></div><div class="card"><span>03</span><h3>소주</h3><p>앞으로 만나게 될 새로운 우리술</p></div></div></section>
<section id="shop"><div class="eyebrow">SHOP OURISUL</div><h2>우리술 둘러보기</h2>{% if notice %}<p class="notice">{{notice}}</p>{% elif not products %}<p class="notice">등록된 상품이 없습니다.</p>{% else %}<div class="catalog">{% for product in products %}<a class="product" href="/products/{{product.number}}"><div class="product-image">{% if product.image %}<img src="{{product.image}}" alt="{{product.name}}" loading="lazy" referrerpolicy="no-referrer">{% else %}우리술{% endif %}</div><div class="product-copy"><h3>{{product.name}}</h3><p>{{product.price}}{% if not product.selling %} · 판매 준비 중{% endif %}</p></div></a>{% endfor %}</div>{% endif %}</section>
<section id="contact" class="story"><div class="eyebrow">OURISUL</div><h2>우리의 술을<br>우리의 이름으로.</h2><p>정성껏 빚은 술을 소개합니다.</p></section>
<footer><strong>우리술</strong><br>우리의 술, 우리의 시간, 우리의 이야기.</footer>
</body></html>
"""

@app.route("/")
def home():
    products, notice = products_for_page()
    return render_template_string(HTML.replace('href="#collection">우리술 만나기', 'href="#shop">우리술 만나기'), products=products, notice=notice, live=store_live())


@app.route('/products/<int:product_no>')
def product_detail(product_no):
    if product_no <= 0:
        abort(404)
    products, notice = products_for_page(product_no)
    if not products:
        if notice:
            return render_template_string('<meta charset="utf-8"><p>{{notice}}</p><a href="/">홈으로</a>', notice=notice), 503
        abort(404)
    product = products[0]
    return render_template_string('''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{p.name}} | 우리술</title><style>{{css}}</style>{% if not live %}<div class="preview">운영 준비 중 · 주문을 받지 않습니다.</div>{% endif %}<header><a class="logo" href="/">우리술</a><nav><a href="/#shop">모든 술</a></nav></header><section class="detail"><div class="product-image">{% if p.image %}<img src="{{p.image}}" alt="{{p.name}}">{% else %}우리술{% endif %}</div><div><span class="eyebrow">OURISUL COLLECTION</span><h1>{{p.name}}</h1><p>{{p.summary|striptags}}</p><h2>{{p.price}}</h2>{% if live and p.selling %}<a class="button" href="{{p.mall_url}}" rel="noopener noreferrer">카페24 쇼핑몰에서 구매하기</a>{% if p.coupang_url %} <a class="button" href="{{p.coupang_url}}" rel="noopener noreferrer">쿠팡에서 구매하기</a>{% endif %}{% else %}<p>주문 준비 중입니다.</p>{% endif %}<br><a class="back" href="/#shop">← 목록으로</a></div></section>''', p=product, live=store_live(), css=HTML.split('<style>',1)[1].split('</style>',1)[0])


@app.route('/admin/launch-check')
@protected
def launch_check():
    products = public_catalog()
    sample = any('샘플' in p.get('product_name', '') for p in (products or []))
    return {'store_live': store_live(), 'cafe24_catalog_connected': products is not None,
            'cafe24_product_count': len(products or []), 'sample_products_present': sample,
            'coupang_api_configured': coupang_configured(),
            'coupang_api_verified': False if not coupang_configured() else '별도 주문 조회 필요',
            'coupang_product_links_configured': bool(os.environ.get('COUPANG_PRODUCT_URLS')),
            'automatic_inventory_sync': False, 'shipping_and_returns_automation': False,
            'dry_run_url': '/admin/dry-run',
            'ready_to_launch': False,
            'message': '실제 상품·판매 자격·성인인증·주문/재고 운영 절차 확인 후 출시하세요.'}


@app.route('/admin/dry-run')
@protected
def prelaunch_dry_run():
    return dry_run()


@app.route('/admin/coupang/orders')
@protected
def coupang_orders():
    if not coupang_configured():
        return {'mode': 'demo', 'orders': demo_orders(), 'message': 'Wing API 연결 전 시연 데이터입니다.'}
    now = datetime.now(KST)
    try:
        orders = order_summaries(now - timedelta(minutes=60), now)
    except Exception:
        app.logger.error('Coupang order check failed')
        return {'error': '쿠팡 주문 조회에 실패했습니다.'}, 502
    return {'mode': 'live', 'orders': orders}

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
