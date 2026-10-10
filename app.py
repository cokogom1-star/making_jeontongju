from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
import os
import re
from urllib.parse import urlparse

from flask import Flask, abort, render_template_string, request

app = Flask(__name__)

from cafe24 import init_app, protected, public_catalog
from coupang import KST, configured as coupang_configured, demo_orders, order_summaries, product_url
from prelaunch import dry_run
from direct_checkout import bp as direct_checkout_bp, enabled as direct_checkout_test_enabled
from test_orders import bp as test_orders_bp
init_app(app)
app.register_blueprint(direct_checkout_bp)
app.register_blueprint(test_orders_bp)


def store_live():
    return os.environ.get('STORE_LIVE', '').lower() == 'true' and bool(approved_product_numbers())


def approved_product_numbers():
    return {int(value.strip()) for value in os.environ.get('LIVE_PRODUCT_NOS', '').split(',')
            if value.strip().isdigit() and int(value.strip()) > 0}


def purchase_enabled(product):
    return (store_live() and product['selling'] and product['number'] in approved_product_numbers()
            and '샘플' not in product['name'] and bool(product['mall_url']))


def products_for_page(product_no=None, page=1, with_next=False):
    try:
        fetched = public_catalog(product_no, page=page, with_next=with_next)
        products, has_next = fetched if with_next else (fetched, False)
    except Exception:
        app.logger.error('Catalog request failed')
        return [], '상품 정보를 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.', False
    if products is None:
        return [], '상품 준비 중입니다. 카페24 연결 후 이곳에 상품이 표시됩니다.', False
    result = []
    seen_numbers = set()
    mall_id = os.environ.get('CAFE24_MALL_ID', '')
    valid_mall = bool(re.fullmatch(r'[a-z0-9][a-z0-9-]*', mall_id))
    for product in products:
        try:
            number = int(product['product_no'])
            if number <= 0 or number in seen_numbers:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        image = product.get('list_image') or product.get('detail_image')
        if image and (not isinstance(image, str) or not image.startswith('https://') or not urlparse(image).hostname):
            image = None
        try:
            amount = Decimal(str(product['price']))
            price = f"{amount:,.0f}원" if amount.is_finite() and amount >= 0 else '가격 문의'
        except (InvalidOperation, KeyError, TypeError):
            price = '가격 문의'
        seen_numbers.add(number)
        result.append({'number': number, 'name': product.get('product_name') or '우리술',
                       'image': image, 'price': price,
                       'summary': product.get('summary_description') or '',
                       'selling': product.get('selling') == 'T',
                       'purchase_enabled': False,
                       'mall_url': (f'https://{mall_id}.cafe24.com/product/detail.html?product_no={number}'
                                    if valid_mall else None),
                       'coupang_url': product_url(number)})
        result[-1]['purchase_enabled'] = purchase_enabled(result[-1])
    return result, None, has_next

HTML = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>우리술 | 시간을 빚다</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f6f2e9;color:#24221e;font-family:Georgia,"Noto Serif KR",serif;line-height:1.7}
header{height:76px;display:flex;align-items:center;justify-content:space-between;padding:0 7%;border-bottom:1px solid #d8d1c4;background:#f6f2e9;position:sticky;top:0;z-index:10}
.logo{font-size:27px;font-weight:bold;color:inherit;text-decoration:none;white-space:nowrap}.desktop-nav{display:flex;align-items:center;gap:22px}.desktop-nav a,.mobile-nav a,footer a{color:inherit;text-decoration:none;font-size:14px}.desktop-nav a:hover,.mobile-nav a:hover,footer a:hover{text-decoration:underline}.mobile-nav{display:none}section{scroll-margin-top:76px}
.hero{min-height:650px;padding:10% 9%;display:flex;align-items:center;background:linear-gradient(90deg,rgba(246,242,233,.97),rgba(246,242,233,.68)),url("https://images.unsplash.com/photo-1536935338788-846bb9981813?auto=format&fit=crop&w=1800&q=80") center/cover}
.eyebrow{letter-spacing:3px;font-size:11px}.hero h1{font-size:clamp(45px,7vw,78px);line-height:1.15;font-weight:500;margin:15px 0}.lead{font-size:17px}.button{display:inline-block;margin-top:20px;padding:11px 25px;border:1px solid #24221e;text-decoration:none;color:inherit}
section{max-width:1200px;margin:auto;padding:110px 7%}.intro h2,.story h2{font-size:clamp(35px,5vw,58px);line-height:1.25;font-weight:500}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;padding-top:0}.card{border-top:1px solid #888;padding:30px 0}.card h3{font-size:30px;font-weight:500;margin:15px 0}
.story{border-top:1px solid #d8d1c4}.story p{font-size:18px;max-width:760px}
.section-title{font-size:clamp(34px,4vw,52px);font-weight:500;line-height:1.25;margin:14px 0 30px}.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}.steps article{border-top:1px solid #8c8274;padding-top:18px}.steps strong{font-size:19px;font-weight:500}.steps p{color:#625b51}.guide{border-top:1px solid #d8d1c4}.faq{border-top:1px solid #d8d1c4}.faq details{border-bottom:1px solid #d8d1c4;padding:18px 0}.faq summary{cursor:pointer;font-size:18px}.faq details p{margin:12px 0 0;color:#625b51}.footer-nav{display:flex;flex-wrap:wrap;gap:18px;margin-top:18px}
.catalog{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}.product{color:inherit;text-decoration:none;border:1px solid #d8d1c4;background:#fff;display:block}.product-image{aspect-ratio:1/1;background:#ebe5d9;display:grid;place-items:center;color:#777}.product-image img{width:100%;height:100%;object-fit:cover}.product-copy{padding:18px 22px}.product-copy h3{font-size:22px;font-weight:500;margin:0}.product-copy p{margin:5px 0}.notice{padding:25px 0;color:#665e53}.detail{display:grid;grid-template-columns:1fr 1fr;gap:45px;align-items:center}.detail h1{font-size:clamp(36px,5vw,60px);line-height:1.2;font-weight:500}.detail .product-image{min-height:300px}.back{display:inline-block;margin-top:22px;color:inherit}
.preview{padding:12px 7%;background:#e7dbc4;font-size:14px}
.page-hero{padding:85px 7% 55px;max-width:1200px;margin:auto;border-bottom:1px solid #d8d1c4}.page-hero h1{font-size:clamp(42px,6vw,70px);font-weight:500;line-height:1.2;margin:12px 0 22px}.page-hero p{max-width:720px;font-size:18px}.page-section{padding-top:60px;padding-bottom:70px}.page-section h2{font-size:clamp(29px,4vw,42px);font-weight:500}.page-section p{max-width:780px}.page-links{display:flex;gap:14px;flex-wrap:wrap}.page-links a,.text-link{color:inherit;text-underline-offset:5px}.flow-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}.flow-grid article{padding:26px;border:1px solid #d8d1c4}.flow-grid strong{font-size:21px;font-weight:500}.flow-grid p{color:#625b51}.active-link{text-decoration:underline!important;text-underline-offset:7px}.product:hover,.button:hover{background:#eee7d9}.text-link{display:inline-block;margin-top:20px}
.card-link{display:block;color:inherit;text-decoration:none}.card-link:hover h3,.card-link:focus-visible h3{text-decoration:underline;text-underline-offset:6px}.flow-grid article[id]{scroll-margin-top:96px}.breadcrumb{font-size:13px;margin:0 0 22px;color:#625b51}.breadcrumb a{color:inherit;text-underline-offset:4px}.status-label{display:block;font-size:13px;color:#625b51;margin:5px 0}.skip-link{position:absolute;left:10px;top:-80px;background:#24221e;color:#fff;padding:10px;z-index:20}.skip-link:focus{top:10px}a:focus-visible,summary:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid #7a4824;outline-offset:4px}
.detail-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.detail-actions .button{margin-top:8px}.detail-note{padding:15px 18px;border-left:3px solid #8c8274;background:#eee7d9}.detail-note p{margin:0}.detail-note a{color:inherit;text-underline-offset:4px}
.taste-categories{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}.taste-category{display:block;padding:24px;border:1px solid #d8d1c4;color:inherit;text-decoration:none;background:#fff}.taste-category:hover,.taste-category:focus-visible{background:#eee7d9}.taste-category strong{display:block;font-size:24px;font-weight:500}.taste-category p{margin-bottom:0}.taste-form fieldset{border:1px solid #d8d1c4;margin:0 0 18px;padding:20px}.taste-form legend{font-size:21px;padding:0 6px}.taste-form label{display:flex;align-items:center;min-height:44px;margin:4px 0;cursor:pointer}.taste-form input{margin-right:12px;accent-color:#7a4824}.taste-form button{font:inherit;background:#24221e;color:#fff;border:0;padding:12px 26px;cursor:pointer}.taste-result{border-top:2px solid #8c8274;padding-top:24px;scroll-margin-top:96px}.taste-result li{margin:12px 0}.taste-note{color:#625b51}.taste-results{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.taste-style{background:#fff;border:1px solid #d8d1c4;padding:22px;min-width:0}.taste-style h3{font-size:25px;font-weight:500;line-height:1.25;margin:8px 0}.taste-style p{font-size:15px;line-height:1.65}.taste-form label:focus-within{outline:2px solid #7a4824;outline-offset:2px}
footer{padding:60px 7%;border-top:1px solid #d8d1c4}@media(max-width:1150px){.desktop-nav{display:none}.mobile-nav{display:block;position:relative}.mobile-nav summary{cursor:pointer;list-style:none;border:1px solid #9b9184;padding:7px 13px}.mobile-nav summary::-webkit-details-marker{display:none}.mobile-nav nav{position:absolute;right:0;top:42px;width:180px;background:#f6f2e9;border:1px solid #d8d1c4;box-shadow:0 12px 25px #24221e22;display:grid;padding:10px}.mobile-nav:not([open]) nav{display:none}.mobile-nav a{padding:10px 12px}}
@media(max-width:700px){.hero{min-height:570px;padding:18% 7%}.cards,.steps,.flow-grid,.taste-categories,.taste-results{grid-template-columns:1fr}section{padding:80px 7%}.catalog{grid-template-columns:1fr 1fr;gap:12px}.detail{grid-template-columns:1fr}.product-copy{padding:13px}.product-copy h3{font-size:18px}}
@media(max-width:480px){.catalog{grid-template-columns:1fr}}
</style>
</head>
<body>
<a class="skip-link" href="#main">본문으로 건너뛰기</a>
{% if not live %}<div class="preview">운영 준비 중 · 현재 상품은 시연용이며 이 사이트에서는 주문을 받지 않습니다.</div>{% endif %}
<header><a class="logo" href="/">우리술</a><nav class="desktop-nav" aria-label="주요 메뉴"><a href="/about">브랜드</a><a href="/stories">술 이야기</a><a href="/taste-explorer">취향 탐색</a><a href="/products">상품</a><a href="/craft">양조 이야기</a><a href="/guide">이용 안내</a><a href="/faq">자주 묻는 질문</a><a href="/contact">문의</a></nav><details class="mobile-nav"><summary>메뉴</summary><nav aria-label="모바일 메뉴"><a href="/about">브랜드</a><a href="/stories">술 이야기</a><a href="/taste-explorer">취향 탐색</a><a href="/products">상품</a><a href="/craft">양조 이야기</a><a href="/guide">이용 안내</a><a href="/faq">자주 묻는 질문</a><a href="/contact">문의</a></nav></details></header>
<main id="main"><div class="hero"><div><div class="eyebrow">TRADITIONAL KOREAN LIQUOR</div><h1>오래된 지혜를<br>오늘의 술로.</h1><p class="lead">우리 조상이 빚어온 술에는 시간과 계절, 사람의 손길이 담겨 있습니다.<br>우리술은 그 가치를 오늘에 전하고자 합니다.</p><a class="button" href="#collection">우리술 만나기</a></div></div>
<section id="about" class="intro"><div class="eyebrow">OUR STORY</div><h2>마시기 위한 술,<br>기억하기 위한 전통.</h2><p>세계에는 각 나라를 대표하는 술이 있습니다. 우리에게도 오랜 시간 이어져 온 훌륭한 술과 그것을 빚어온 지혜가 있습니다.</p><p>우리술은 그 전통을 오늘의 사람들에게 다시 소개하고, 한국의 술 문화를 세계에 전하고자 합니다.</p><a class="text-link" href="/about">브랜드 이야기 더 보기 →</a></section>
<section id="collection"><div class="eyebrow">OUR COLLECTION</div><div class="cards"><a class="card card-link" href="/stories#cheongju"><span>01</span><h3>청주</h3><p>맑고 섬세한 향을 가진 우리 술</p><span>이야기 보기 →</span></a><a class="card card-link" href="/stories#takju"><span>02</span><h3>탁주</h3><p>쌀과 누룩이 만들어내는 깊은 풍미</p><span>이야기 보기 →</span></a><a class="card card-link" href="/stories#soju"><span>03</span><h3>소주</h3><p>앞으로 만나게 될 새로운 우리술</p><span>이야기 보기 →</span></a></div><a class="text-link" href="/stories">술 이야기 더 보기 →</a></section>
<section id="shop"><div class="eyebrow">SHOP OURISUL</div><h2>우리술 둘러보기</h2>{% if notice %}<p class="notice">{{notice}}</p>{% elif not products %}<p class="notice">등록된 상품이 없습니다.</p>{% else %}<div class="catalog">{% for product in products %}<a class="product" href="/products/{{product.number}}"><div class="product-image">{% if product.image %}<img src="{{product.image}}" alt="{{product.name}}" loading="lazy" referrerpolicy="no-referrer">{% else %}우리술{% endif %}</div><div class="product-copy"><h3>{{product.name}}</h3><p>{{product.price}}{% if not live %} · 시연용 가격{% endif %}</p><span class="status-label">{% if product.purchase_enabled %}판매 중{% elif live %}판매 준비 중{% else %}시연용 · 주문 불가{% endif %}</span></div></a>{% endfor %}</div>{% endif %}<a class="text-link" href="/products">상품 전체 보기 →</a> <a class="text-link" href="/guide">이용 안내 →</a></section>
<section id="craft" class="story"><div class="eyebrow">CRAFT NOTES</div><h2 class="section-title">한 잔에 담기는 시간</h2><div class="steps"><article><strong>원료</strong><p>술의 바탕이 되는 재료를 살핍니다.</p></article><article><strong>발효</strong><p>누룩과 시간이 빚어내는 향과 맛을 소개합니다.</p></article><article><strong>기록</strong><p>앞으로 선보일 술의 제작 과정을 이곳에 기록할 예정입니다.</p></article></div><a class="text-link" href="/craft">양조 이야기 더 보기 →</a></section>
<section id="guide" class="guide"><div class="eyebrow">SHOPPING GUIDE</div><h2 class="section-title">이용 안내</h2>{% if live %}<p>상품 상세에서 판매 채널을 선택하면 해당 채널의 주문 페이지로 이동합니다. 주문·결제·배송 안내는 선택한 판매 채널에서 확인해 주세요.</p>{% else %}<p>현재는 운영 준비 단계입니다. 표시된 상품은 시연용이며 이 사이트에서 주문을 받지 않습니다. 정식 상품과 판매 채널이 준비되면 구매 방법을 안내하겠습니다.</p>{% endif %}</section>
<section id="faq" class="faq"><div class="eyebrow">FAQ</div><h2 class="section-title">자주 묻는 질문</h2><details><summary>지금 상품을 주문할 수 있나요?</summary><p>{% if live %}상품 상세에 표시된 판매 채널에서 주문할 수 있습니다.{% else %}아직 주문을 받지 않습니다. 현재 상품은 시연용입니다.{% endif %}</p></details><details><summary>어떤 술을 소개하나요?</summary><p>청주와 탁주를 중심으로 우리 술의 종류와 이야기를 소개하고 있습니다. 실제 판매 상품은 출시 전에 안내합니다.</p></details><details><summary>쿠팡에서도 주문할 수 있나요?</summary><p>{% if live %}쿠팡에 등록된 상품은 상세 페이지에 쿠팡 구매 버튼이 표시됩니다.{% else %}쿠팡 판매 연동을 준비하고 있습니다. 실제 상품 등록 전에는 쿠팡 주문을 받지 않습니다.{% endif %}</p></details></section>
<section id="contact" class="story"><div class="eyebrow">CONTACT</div><h2 class="section-title">우리의 술을<br>우리의 이름으로.</h2><p>정성껏 빚은 술을 소개합니다. 공식 문의 창구는 운영 시작 전에 안내하겠습니다.</p><a class="text-link" href="/contact">문의 안내 보기 →</a></section></main>
<footer><strong>우리술</strong><br>우리의 술, 우리의 시간, 우리의 이야기.<nav class="footer-nav" aria-label="하단 메뉴"><a href="/about">브랜드</a><a href="/taste-explorer">취향 탐색</a><a href="/products">상품</a><a href="/guide">이용 안내</a><a href="/faq">FAQ</a><a href="/contact">문의</a></nav></footer>
<script>document.querySelectorAll('.mobile-nav a').forEach(function(link){link.addEventListener('click',function(){document.querySelector('.mobile-nav').open=false})})</script>
</body></html>
"""

@app.route("/")
def home():
    products, notice, _ = products_for_page()
    return render_template_string(HTML.replace('href="#collection">우리술 만나기', 'href="#shop">우리술 만나기'), products=products, notice=notice, live=store_live())


PAGE_META = {
    'about': ('OUR STORY', '브랜드', '우리술은 전통주와 그 뒤에 있는 사람과 시간을 소개하기 위해 준비 중인 브랜드입니다.'),
    'stories': ('OUR COLLECTION', '술 이야기', '청주와 탁주를 비롯한 우리 술의 여러 모습을 차근차근 소개합니다.'),
    'products': ('OURISUL COLLECTION', '상품', '현재 보이는 상품은 연결과 화면 확인을 위한 시연용 상품입니다.'),
    'craft': ('CRAFT NOTES', '양조 이야기', '원료에서 발효, 기록까지 술이 만들어지는 과정을 담을 공간입니다.'),
    'guide': ('SHOPPING GUIDE', '이용 안내', '상품을 살펴보고 주문하는 흐름을 안내합니다.'),
    'faq': ('FAQ', '자주 묻는 질문', '현재 운영 단계와 구매 방법에 관한 질문을 모았습니다.'),
    'contact': ('CONTACT', '문의', '우리술에 관한 소식을 전할 공식 창구를 준비하고 있습니다.'),
}

PAGE_BODY = {
    'about': '''<h2>우리 술의 이야기를 오늘에</h2><p>세계에는 각 나라를 대표하는 술이 있습니다. 우리에게도 오랜 시간 이어져 온 술과 그것을 빚어온 지혜가 있습니다.</p><p>우리술은 청주와 탁주 등 우리 술을 소개하고, 앞으로 만들어 갈 제품과 양조 기록을 이곳에 담으려 합니다.</p><div class="page-links"><a class="button" href="/stories">술 이야기 보기</a><a class="button" href="/craft">양조 이야기 보기</a></div>''',
    'stories': '''<h2>우리 술을 알아가기</h2><div class="flow-grid"><article id="cheongju"><strong>청주</strong><p>맑게 걸러낸 술. 원료와 양조 방식에 따라 향과 맛이 다양합니다.</p></article><article id="takju"><strong>탁주</strong><p>쌀과 누룩이 빚어내는 질감과 풍미를 만날 수 있습니다.</p></article><article id="soju"><strong>소주</strong><p>증류를 통해 만들어지는 술. 앞으로 소개할 내용을 준비 중입니다.</p></article></div><p>이곳의 설명은 술 종류에 관한 소개이며 현재 판매 상품의 성분이나 특징을 뜻하지 않습니다.</p><a class="button" href="/products">상품 보기</a>''',
    'products': '''{% if notice %}<p class="notice">{{notice}}</p>{% elif not products %}<p class="notice">등록된 상품이 없습니다.</p>{% else %}<div class="catalog">{% for product in products %}<a class="product" href="/products/{{product.number}}"><div class="product-image">{% if product.image %}<img src="{{product.image}}" alt="{{product.name}}" loading="lazy" referrerpolicy="no-referrer">{% else %}우리술{% endif %}</div><div class="product-copy"><h3>{{product.name}}</h3><p>{{product.price}}{% if not live %} · 시연용 가격{% endif %}</p><span class="status-label">{% if product.purchase_enabled %}판매 중{% elif live %}판매 준비 중{% else %}시연용 · 주문 불가{% endif %}</span><span>상세 보기 →</span></div></a>{% endfor %}</div>{% endif %}<p class="notice">{% if live %}구매 가능 여부와 판매 채널은 각 상품의 상세 화면에서 확인해 주세요.{% else %}상품을 눌러 상세 화면을 볼 수 있습니다. 현재는 주문을 받지 않습니다.{% endif %} <a href="/guide">이용 안내 보기</a></p>''',
    'craft': '''<h2>한 잔에 담기는 시간</h2><div class="flow-grid"><article><strong>01 · 원료</strong><p>술의 바탕이 되는 재료와 그 선택에 관한 기록을 준비합니다.</p></article><article><strong>02 · 발효</strong><p>누룩과 시간이 빚어내는 변화를 앞으로 소개합니다.</p></article><article><strong>03 · 기록</strong><p>실제로 선보일 술의 제작 과정과 결과는 확인 후 게시합니다.</p></article></div><p>제품과 양조 기록은 준비되는 대로 소개하겠습니다.</p><a class="button" href="/stories">술 종류 알아보기</a>''',
    'guide': '''<h2>이용 흐름</h2><div class="flow-grid"><article><strong>01 · 상품 살펴보기</strong><p>상품 목록에서 관심 있는 술을 선택합니다.</p></article><article><strong>02 · 상세 확인하기</strong><p>상품별 설명과 판매 준비 상태를 확인합니다.</p></article><article><strong>03 · 판매 채널 이동</strong><p>{% if live %}판매가 시작된 상품은 상세 화면에서 연결된 판매 채널로 이동합니다.{% else %}정식 판매가 시작되면 상세 화면에서 판매 채널을 안내할 예정입니다.{% endif %}</p></article></div><p>{% if live %}주문, 결제, 배송 및 교환·반품 조건은 실제 주문하는 판매 채널의 안내를 확인해 주세요.{% else %}현재는 시연 단계로 주문, 결제, 배송을 제공하지 않습니다. 실제 판매 조건과 고객 응대 채널은 운영 시작 전에 게시합니다.{% endif %}</p><a class="button" href="/products">상품 목록으로</a>''',
    'faq': '''<div class="faq"><details><summary>지금 상품을 주문할 수 있나요?</summary><p>{% if live %}판매 중인 상품은 상세 화면에 표시된 판매 채널로 이동해 주문할 수 있습니다.{% else %}아직 주문을 받지 않습니다. 화면에 보이는 상품은 시연용입니다.{% endif %}</p></details><details><summary>쿠팡에서도 주문할 수 있나요?</summary><p>{% if live %}쿠팡 구매 링크가 표시된 상품만 해당 채널에서 확인할 수 있습니다.{% else %}쿠팡 판매 연동을 준비 중이며 실제 상품 등록과 주문은 시작하지 않았습니다.{% endif %}</p></details><details><summary>상품 가격과 정보가 확정되었나요?</summary><p>{% if live %}최신 가격과 상세 정보는 연결된 판매 채널에서 확인해 주세요.{% else %}현재 상품은 시연용이므로 실제 출시 가격이나 제품 정보를 의미하지 않습니다.{% endif %}</p></details><details><summary>배송이나 교환·반품은 어디에서 확인하나요?</summary><p>{% if live %}주문한 판매 채널의 안내 및 고객센터를 이용해 주세요.{% else %}아직 주문이 열리지 않았습니다. 운영 시작 전에 판매 채널별 안내를 게시하겠습니다.{% endif %}</p></details><details><summary>문의는 어떻게 하나요?</summary><p>공식 문의 창구를 준비하고 있습니다. 준비가 완료되면 문의 화면에 게시하겠습니다.</p></details></div><a class="button" href="/contact">문의 안내 보기</a>''',
    'contact': '''<h2>공식 문의 안내</h2><p>현재 공식 문의용 이메일과 연락처를 공개하기 전입니다. 운영 시작 전 이 화면에서 문의 방법과 응대 시간을 안내하겠습니다.</p><p>상품 주문에 관한 문의는 판매가 시작된 뒤 실제 주문한 채널의 고객센터를 통해 접수할 수 있습니다.</p><div class="page-links"><a class="button" href="/faq">자주 묻는 질문</a><a class="button" href="/">홈으로 돌아가기</a></div>''',
}

PAGE_BODY['products'] += '''<nav class="page-links" aria-label="상품 페이지">
{% if page_number > 1 %}<a href="/products?page={{page_number - 1}}">이전 상품</a>{% endif %}
<span>상품 {{page_number}}페이지</span>
{% if has_next %}<a href="/products?page={{page_number + 1}}">다음 상품</a>{% endif %}
</nav>{% if has_next is none %}<p class="notice">이 목록의 표시 한도에 도달했습니다. 나머지 상품은 카페24 상점에서 확인해 주세요.</p>{% endif %}'''


@app.route('/<page>')
def content_page(page):
    if page not in PAGE_META:
        abort(404)
    eyebrow, title, lead = PAGE_META[page]
    if page == 'products' and store_live():
        lead = '우리술 상품을 살펴보세요. 구매 가능한 상품은 상세 화면에서 판매 채널을 확인할 수 있습니다.'
    # Only fixed, application-owned page markup is inserted into this template.
    shell = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{title}} | 우리술</title><style>''' + HTML.split('<style>', 1)[1].split('</style>', 1)[0] + '''</style></head><body><a class="skip-link" href="#main">본문으로 건너뛰기</a>{% if not live %}<div class="preview">운영 준비 중 · 현재 상품은 시연용이며 이 사이트에서는 주문을 받지 않습니다.</div>{% endif %}''' + HTML.split('<header>', 1)[1].split('</header>', 1)[0].join(['<header>', '</header>']) + '''<main id="main"><div class="page-hero"><nav class="breadcrumb" aria-label="현재 위치"><a href="/">홈</a> / <span aria-current="page">{{title}}</span></nav><span class="eyebrow">{{eyebrow}}</span><h1>{{title}}</h1><p>{{lead}}</p></div><section class="page-section">''' + PAGE_BODY[page] + '''</section></main>''' + HTML.split('<footer>', 1)[1].split('</footer>', 1)[0].join(['<footer>', '</footer>']) + '''<script>document.querySelectorAll('.mobile-nav a').forEach(function(link){link.addEventListener('click',function(){document.querySelector('.mobile-nav').open=false})});document.querySelectorAll('a[href="/'''+page+'''"]').forEach(function(link){link.classList.add('active-link');link.setAttribute('aria-current','page')})</script></body></html>'''
    page_number = 1
    if page == 'products':
        raw_page = request.args.get('page', '1')
        if len(raw_page) > 3 or not raw_page.isascii() or not raw_page.isdecimal() or not 1 <= int(raw_page) <= 209:
            abort(404)
        page_number = int(raw_page)
    products, notice, has_next = (products_for_page(page=page_number, with_next=True)
                                  if page == 'products' else ([], None, False))
    return render_template_string(shell, eyebrow=eyebrow, title=title, lead=lead,
                                  products=products, notice=notice, live=store_live(),
                                  page_number=page_number, has_next=has_next)


def shared_chrome():
    css = HTML.split('<style>', 1)[1].split('</style>', 1)[0]
    header = '<header>' + HTML.split('<header>', 1)[1].split('</header>', 1)[0] + '</header>'
    footer = '<footer>' + HTML.split('<footer>', 1)[1].split('</footer>', 1)[0] + '</footer>'
    return css, header, footer


# Editorial style archetypes, never product attributes or measured compatibility.
TASTE_QUESTIONS = (
    ('aroma', '먼저 끌리는 향은 무엇인가요?', (
        ('fruit', '과일', '익은 과일이나 베리의 인상'), ('floral', '꽃·허브', '꽃과 허브의 인상'),
        ('grain', '곡물·빵', '쌀, 곡물이나 빵의 인상'), ('roast', '커피·볶은 향', '볶은 곡물이나 커피의 인상'),
        ('smoke', '연기·흙', '연기나 흙의 인상'), ('spice', '향신료·나무', '향신료와 나무의 인상'))),
    ('sweetness', '단맛은 어느 정도가 좋나요?', (
        ('dry', '드라이', '단맛이 적은 쪽'), ('round', '은은한 단맛', '부드럽게 균형 잡힌 쪽'),
        ('sweet', '뚜렷한 단맛', '단맛이 선명한 쪽'))),
    ('body', '입안의 무게감은 어떤가요?', (
        ('light', '가벼움', '가볍고 산뜻한 질감'), ('medium', '중간', '적당히 도톰한 질감'),
        ('full', '묵직함', '입안을 채우는 질감'))),
    ('acidity', '상큼한 산미는 어느 쪽이 끌리나요?', (
        ('soft', '부드러운 쪽', '산미가 둥글게 느껴지는 쪽'),
        ('bright', '선명한 쪽', '산미가 또렷하고 경쾌한 쪽'))),
    ('bitterness', '쌉쌀함은 어느 정도가 좋나요?', (
        ('low', '적은 쪽', '쓴맛이 두드러지지 않는 쪽'),
        ('pronounced', '뚜렷한 쪽', '쓴맛이 인상에 남는 쪽'))),
    ('texture', '어떤 질감이 궁금한가요?', (
        ('clear', '맑고 매끈함', '맑고 매끈한 질감'),
        ('creamy', '부드럽고 걸쭉함', '부드럽고 포근한 질감'),
        ('bubbly', '기포와 청량감', '기포가 주는 청량감'))),
    ('finish', '마신 뒤의 인상은 어떤가요?', (
        ('clean', '깔끔하게 끝남', '짧고 정돈된 마무리'),
        ('long', '천천히 남음', '길게 이어지는 여운'))),
)
TASTE_WEIGHTS = (3, 2, 2, 2, 2, 2, 1)


def taste_style(key, category, name, character, explore, compare, profile):
    return {'key': key, 'category': category, 'name': name, 'character': character,
            'explore': explore, 'compare': compare, 'profile': profile.split()}


TASTE_STYLES = (
    taste_style('whisky-bourbon', '위스키', '버번', '옥수수 원료와 새로 태운 오크통에서 오는 바닐라·캐러멜 같은 인상이 대표적입니다. 실제 풍미는 제품마다 다릅니다.', '바닐라와 구운 나무 향, 둥근 단맛, 묵직한 질감을 비교해 보세요.', '라이 위스키는 향신료와 드라이한 마무리가 더 두드러지는 경우가 많습니다.', 'spice round full soft low clear long'),
    taste_style('whisky-rye', '위스키', '라이 위스키', '호밀을 사용하는 위스키로 후추 같은 향신료, 곡물과 드라이한 인상을 비교하기 좋습니다.', '향신료 향이 단맛과 어떤 균형을 이루는지 살펴보세요.', '버번의 바닐라·캐러멜 인상과 나란히 비교해 보세요.', 'spice dry medium soft pronounced clear long'),
    taste_style('whisky-sherry', '위스키', '셰리 캐스크 위스키', '셰리 와인을 담았던 오크통에서 숙성한 위스키를 가리키며 말린 과일·견과·향신료 인상이 나타나기도 합니다.', '말린 과일의 향과 둥근 질감, 오래 남는 여운을 살펴보세요.', '버번 캐스크 위스키의 바닐라·코코넛 계열 향과 비교해 보세요.', 'fruit round full soft low clear long'),
    taste_style('whisky-peat', '위스키', '피트 위스키', '피트를 연료로 사용한 맥아에서 연기, 재, 흙 같은 향이 생길 수 있습니다. 강도는 산지와 제조법마다 다릅니다.', '연기 향이 단맛과 여운 위에 어떻게 겹치는지 살펴보세요.', '피트를 쓰지 않은 몰트 위스키와 향의 차이를 비교해 보세요.', 'smoke dry full soft pronounced clear long'),
    taste_style('whisky-fruity-malt', '위스키', '과일 향 몰트 위스키', '발효와 증류, 숙성의 조합에서 사과·배 같은 과일 향이 표현되는 몰트 위스키의 한 방향입니다.', '가벼운 과일 향과 오크의 존재감이 어떻게 다른지 살펴보세요.', '셰리 캐스크의 말린 과일 인상과 신선한 과일 인상을 비교해 보세요.', 'fruit dry light bright low clear clean'),
    taste_style('whisky-oaky', '위스키', '오크 중심 위스키', '숙성 오크의 나무, 토스트, 바닐라 인상이 앞에 오는 스타일 방향입니다. 통의 종류와 숙성 조건에 따라 달라집니다.', '나무 향과 질감이 강한 향신료 인상 없이 어떻게 이어지는지 살펴보세요.', '셰리 캐스크의 말린 과일 향과 오크 중심의 나무 향을 비교해 보세요.', 'spice round medium soft low clear clean'),
    taste_style('whisky-smoky-light', '위스키', '가벼운 스모키 위스키', '연기 향은 느껴지지만 질감은 비교적 가벼운 위스키를 탐색하는 방향입니다.', '연기 향과 깔끔한 끝맛이 함께 나타나는지 비교해 보세요.', '묵직하고 긴 여운의 피트 위스키와 비교해 보세요.', 'smoke dry light bright low clear clean'),
    taste_style('beer-pilsner', '맥주', '필스너', '맑은 라거의 한 종류로 곡물·홉 향과 경쾌한 탄산, 깔끔한 끝맛을 살펴볼 수 있습니다.', '곡물 향 뒤에 오는 홉의 쌉쌀함과 청량감을 살펴보세요.', '헬레스 라거의 보다 부드러운 홉 인상과 비교해 보세요.', 'grain dry light bright pronounced bubbly clean'),
    taste_style('beer-helles', '맥주', '헬레스 라거', '맥아의 빵 같은 인상이 중심이 되는 밝은 라거로, 홉의 쓴맛은 대체로 절제됩니다.', '곡물의 은은한 단맛과 깔끔한 마무리를 살펴보세요.', '필스너의 홉 쓴맛이 더 선명한 방향과 비교해 보세요.', 'grain round light soft low bubbly clean'),
    taste_style('beer-wheat', '맥주', '바이젠·밀맥주', '밀을 쓴 맥주 중 효모에서 바나나·정향을 연상시키는 향과 풍성한 거품이 나는 스타일입니다.', '향과 부드러운 질감, 탄산이 함께 만드는 인상을 살펴보세요.', '맑고 드라이한 필스너와 질감을 비교해 보세요.', 'fruit round medium soft low creamy clean'),
    taste_style('beer-ipa', '맥주', 'IPA', '홉에서 오는 과일·수지·허브 향과 쌉쌀함이 중심인 에일 계열입니다. 종류별 쓴맛의 폭이 큽니다.', '홉 향의 종류와 쓴맛이 여운에 남는 방식을 살펴보세요.', '페일 에일보다 홉 인상이 강한지 비교해 보세요.', 'fruit dry medium bright pronounced bubbly long'),
    taste_style('beer-pale-ale', '맥주', '페일 에일', '맥아와 홉의 균형을 보는 에일로 감귤 또는 꽃 향과 적당한 쌉쌀함이 나타날 수 있습니다.', '홉 향과 빵 같은 맥아 향의 균형을 살펴보세요.', 'IPA의 더 강한 홉 인상과 비교해 보세요.', 'floral round medium bright pronounced bubbly clean'),
    taste_style('beer-stout', '맥주', '드라이 스타우트', '볶은 맥아의 커피·코코아 같은 향과 드라이한 마무리가 특징적인 검은 맥주 방향입니다.', '볶은 향과 쌉쌀함이 입안에 남는 방식을 살펴보세요.', '포터의 더 둥근 맥아 단맛과 비교해 보세요.', 'roast dry medium soft pronounced creamy long'),
    taste_style('beer-porter', '맥주', '포터', '볶은 맥아에서 오는 초콜릿·커피 같은 인상을 지닌 어두운 에일의 한 갈래입니다.', '볶은 곡물 향과 부드러운 단맛의 균형을 살펴보세요.', '드라이 스타우트의 더 드라이한 끝맛과 비교해 보세요.', 'roast round full soft low creamy long'),
    taste_style('wine-sauvignon', '와인', '소비뇽 블랑', '감귤, 풋과일 또는 풀 같은 향과 선명한 산미가 나타날 수 있는 화이트 와인 품종입니다.', '향의 초록빛 인상과 산미가 끝맛을 정리하는지 살펴보세요.', '샤르도네의 더 둥근 질감과 비교해 보세요.', 'floral dry light bright low clear clean'),
    taste_style('wine-chardonnay', '와인', '오크 숙성 샤르도네', '지역과 양조에 따라 상큼한 사과 향부터 오크·버터 같은 인상까지 폭이 넓은 화이트 품종입니다.', '과일 향과 오크 사용 여부에 따른 질감 차이를 살펴보세요.', '소비뇽 블랑의 선명한 산미와 비교해 보세요.', 'fruit round full soft low clear clean'),
    taste_style('wine-riesling', '와인', '단맛 있는 리슬링', '꽃·사과·감귤 향과 높은 산미를 지닌 품종으로, 드라이한 와인부터 달콤한 와인까지 나옵니다.', '산미가 단맛을 얼마나 산뜻하게 받치는지 살펴보세요.', '드라이 리슬링과 단맛이 있는 리슬링의 균형을 비교해 보세요.', 'floral sweet light bright low clear long'),
    taste_style('wine-pinot-noir', '와인', '피노 누아', '붉은 베리와 흙 같은 향, 비교적 가벼운 질감의 레드 와인 품종입니다. 지역별 차이가 큽니다.', '붉은 과일 향과 섬세한 질감, 산미의 관계를 살펴보세요.', '카베르네 소비뇽의 더 묵직한 구조감과 비교해 보세요.', 'fruit dry light bright low clear long'),
    taste_style('wine-cabernet', '와인', '카베르네 소비뇽', '검은 과일과 허브 향, 탄닌에 따른 구조감이 나타날 수 있는 레드 와인 품종입니다.', '입안을 마르게 하는 탄닌과 과일 향의 균형을 살펴보세요.', '피노 누아의 가벼운 질감과 비교해 보세요.', 'fruit dry full soft pronounced clear long'),
    taste_style('wine-syrah', '와인', '시라·쉬라즈', '검은 과일, 후추 같은 향신료, 묵직한 질감이 나타날 수 있는 레드 와인 품종입니다.', '후추 향과 진한 과일 향이 어떻게 어울리는지 살펴보세요.', '카베르네 소비뇽의 허브와 탄닌 인상과 비교해 보세요.', 'spice dry full bright pronounced clear long'),
    taste_style('wine-sparkling', '와인', '브뤼 스파클링 와인', '단맛이 적고 기포가 있는 와인의 한 방향으로, 사과·감귤·빵 같은 향이 나타나기도 합니다.', '기포와 산미, 드라이한 끝맛을 따로 느껴 보세요.', '정지 화이트 와인과 기포가 질감에 주는 차이를 비교해 보세요.', 'fruit dry light bright low bubbly clean'),
    taste_style('traditional-cheongju', '전통주', '청주·약주', '쌀과 누룩 등으로 빚어 맑게 거른 술로, 쌀 향과 섬세한 산미가 제품마다 다르게 표현됩니다.', '곡물 향과 맑은 질감, 담백한 마무리를 살펴보세요.', '탁주에서 느껴지는 부드러운 침전물 질감과 비교해 보세요.', 'grain dry light bright low clear clean'),
    taste_style('traditional-takju', '전통주', '탁주', '발효한 술덧을 거칠게 걸러 쌀의 질감과 산미가 함께 나타나는 술의 갈래입니다.', '쌀 향과 부드러운 질감, 산미의 균형을 살펴보세요.', '청주·약주의 맑은 질감과 비교해 보세요.', 'grain round medium bright low creamy clean'),
    taste_style('traditional-makgeolli', '전통주', '달콤한 막걸리', '탁주 중 단맛이 뚜렷하고 부드러운 질감을 지닌 방향입니다. 단맛과 탄산은 제품마다 다릅니다.', '단맛과 쌀 향, 산미가 어떻게 어우러지는지 살펴보세요.', '드라이한 탁주와 단맛의 차이를 비교해 보세요.', 'grain sweet medium bright low creamy long'),
    taste_style('traditional-soju', '전통주', '증류식 소주', '발효한 곡물 술을 증류한 소주로 원료와 증류법에 따라 향이 달라집니다.', '곡물·허브 인상과 맑은 질감, 긴 여운을 살펴보세요.', '맑게 거른 청주·약주와 증류가 주는 차이를 비교해 보세요.', 'grain dry full soft low clear long'),
    taste_style('traditional-fruit-wine', '전통주', '복분자주', '복분자 과실로 빚거나 과실을 더한 술의 방향으로, 베리 향과 산미·단맛의 균형이 다양합니다.', '과실 향과 단맛, 산미의 균형을 살펴보세요.', '쌀이 중심인 청주·약주와 원료 향을 비교해 보세요.', 'fruit sweet medium bright low clear long'),
    taste_style('traditional-flower-wine', '전통주', '꽃향 가향주', '꽃이나 향기 나는 재료를 더해 빚는 전통주의 한 방향입니다. 원료 표기를 확인해 보세요.', '꽃 향이 기본 쌀 향 위에 어떻게 얹히는지 살펴보세요.', '과실을 주재료로 한 복분자주와 향의 출처를 비교해 보세요.', 'floral round light soft low clear long'),
    taste_style('traditional-herbal-wine', '전통주', '약재 가향주', '허브나 약재를 향미 재료로 사용하는 술의 방향입니다. 재료와 제조법에 따라 맛이 크게 달라집니다.', '향신료·허브의 인상이 쌀 향과 여운에 어떻게 남는지 살펴보세요.', '꽃향 가향주의 가벼운 향과 비교해 보세요.', 'spice sweet medium soft pronounced clear long'),
)


def rank_taste_styles(answers):
    """Independent weighted sensory overlap; ties use stable editorial key order."""
    scored = [(sum(weight for answer, feature, weight in zip(answers, style['profile'], TASTE_WEIGHTS)
                   if answer == feature), style) for style in TASTE_STYLES]
    return sorted(scored, key=lambda row: (-row[0], row[1]['key']))


@app.route('/taste-explorer')
def taste_explorer():
    def invalid_selection():
        css, header, footer = shared_chrome()
        markup = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>선택을 확인해 주세요 | 우리술</title><style>''' + css + '''</style></head><body><a class="skip-link" href="#main">본문으로 건너뛰기</a>''' + header + '''<main id="main"><section class="page-section"><h1>선택을 확인해 주세요</h1><p>일곱 가지 질문에 각각 하나씩 답해 주세요.</p><a class="button" href="/taste-explorer">취향 탐색 다시 시작하기</a></section></main>''' + footer + '''</body></html>'''
        return markup, 400, {'Cache-Control': 'no-store'}

    keys = tuple(question[0] for question in TASTE_QUESTIONS)
    if any(key not in keys or len(request.args.getlist(key)) != 1 for key in request.args):
        return invalid_selection()
    if request.args and set(request.args) != set(keys):
        return invalid_selection()
    selections = []
    answers = []
    if request.args:
        for key, prompt, options in TASTE_QUESTIONS:
            selected = next((option for option in options if option[0] == request.args[key]), None)
            if selected is None:
                return invalid_selection()
            answers.append(selected[0])
            selections.append((prompt, selected[1], selected[2]))
    all_ranked = rank_taste_styles(answers) if answers else []
    first_score = all_ranked[0][0] if all_ranked else None
    top_tie_count = sum(score == first_score for score, _ in all_ranked) if all_ranked else 0
    # A tied leading group is displayed in full, even when it exceeds three cards.
    ranked = all_ranked[:max(3, top_tie_count)]
    # Explain matched answers in plain language, never expose a synthetic percentage.
    matches = []
    for score, style in ranked:
        labels = [choice[1] for (_, _, options), value, feature in zip(TASTE_QUESTIONS, answers, style['profile'])
                  for choice in options if choice[0] == value == feature]
        matches.append({'style': style, 'score': score, 'labels': labels,
                        'tied': sum(other_score == score for other_score, _ in all_ranked) > 1})
    css, header, footer = shared_chrome()
    markup = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>취향 탐색 | 우리술</title><style>''' + css + '''</style></head><body><a class="skip-link" href="#main">본문으로 건너뛰기</a><div class="preview">취향 탐색은 정보용입니다. 이 사이트에서는 주문을 받지 않습니다.</div>''' + header + '''<main id="main"><div class="page-hero"><nav class="breadcrumb" aria-label="현재 위치"><a href="/">홈</a> / <span aria-current="page">취향 탐색</span></nav><span class="eyebrow">TASTE EXPLORER</span><h1>나의 술 취향 살펴보기</h1><p>일곱 가지 감각을 고르면 위스키·맥주·와인·전통주를 함께 비교해 볼 수 있습니다. 원하는 술 종류를 먼저 고를 필요가 없습니다.</p></div><section class="page-section"><h2>끌리는 맛과 향을 골라 주세요</h2><p class="taste-note">각 답과 스타일의 대표 감각이 겹치는 정도를 비교합니다. 향을 조금 더 살피고 다른 감각도 함께 비교합니다. 동점 후보는 일정한 순서로 나열하며 표시 순서는 우열을 뜻하지 않습니다. 결과는 성격 판정이나 측정된 상품 적합도가 아닌 취향 탐색 안내입니다.</p><form class="taste-form" method="get" action="/taste-explorer#taste-result">{% for key, prompt, options in questions %}<fieldset><legend>{{loop.index}}. {{prompt}}</legend>{% for value, label, note in options %}<label><input type="radio" name="{{key}}" value="{{value}}" required {% if request.args.get(key) == value %}checked{% endif %}><span><strong>{{label}}</strong> · {{note}}</span></label>{% endfor %}</fieldset>{% endfor %}<button type="submit">스타일 살펴보기</button></form>{% if matches %}<div class="taste-result" id="taste-result" role="region" aria-labelledby="taste-result-title" tabindex="-1"><h2 id="taste-result-title">{% if top_tie_count > 1 %}함께 살펴볼 스타일{% else %}먼저 살펴볼 스타일: {{matches[0].style.name}}{% endif %}</h2><p class="taste-note">네 종류의 술을 한 목록에서 비교한 편집형 탐색 결과입니다. {% if top_tie_count > 1 %}가장 앞선 {{top_tie_count}}가지 스타일은 동점이며 표시 순서에 우열이 없습니다.{% else %}가장 많이 겹친 감각의 스타일을 먼저 보여 줍니다.{% endif %}</p><div class="taste-results">{% for match in matches %}<article class="taste-style"><span class="eyebrow">{{loop.index}} · {{match.style.category}}{% if match.tied %} · 동점{% endif %}</span><h3>{{match.style.name}}</h3><p>{{match.style.character}}</p><p><strong>살펴볼 감각</strong> · {{match.style.explore}}</p><p><strong>비교해 볼 점</strong> · {{match.style.compare}}</p><p class="taste-note">답과 겹친 감각: {{match.labels | join(' · ') if match.labels else '없음'}}</p></article>{% endfor %}</div>{% if ties_beyond %}<p class="taste-note">같은 점수의 다른 스타일도 있습니다. 셋째 결과와 동점인 다른 후보는 화면에 표시되지 않습니다.</p>{% endif %}<h3>내가 고른 감각</h3><ol>{% for prompt, label, note in selections %}<li><strong>{{label}}</strong> · {{note}}</li>{% endfor %}</ol><p class="taste-note">실제 향과 맛은 제품과 사람에 따라 다릅니다. 특정 상품, 구매 가능 여부나 측정된 적합도를 뜻하지 않습니다.</p><a href="/taste-explorer">처음부터 다시 살펴보기</a></div>{% endif %}</section></main>''' + footer + '''</body></html>'''
    response = app.make_response(render_template_string(markup, questions=TASTE_QUESTIONS,
        selections=selections, matches=matches,
        top_tie_count=top_tie_count,
        ties_beyond=len(ranked) == 3 and len(all_ranked) > 3 and all_ranked[3][0] == ranked[2][0]))
    response.headers['Cache-Control'] = 'no-store'
    return response


def message_page(title, message, status):
    css, header, footer = shared_chrome()
    markup = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{title}} | 우리술</title><style>''' + css + '''</style></head><body><a class="skip-link" href="#main">본문으로 건너뛰기</a>''' + header + '''<main id="main"><div class="page-hero"><span class="eyebrow">OURISUL</span><h1>{{title}}</h1><p>{{message}}</p><div class="page-links"><a class="button" href="/">홈으로</a><a class="button" href="/products">상품 목록</a></div></div></main>''' + footer + '''</body></html>'''
    return render_template_string(markup, title=title, message=message), status


@app.errorhandler(404)
def missing_page(error):
    return message_page('페이지를 찾을 수 없습니다', '주소를 확인하거나 메뉴에서 다시 찾아주세요.', 404)


@app.route('/products/<int:product_no>')
def product_detail(product_no):
    if product_no <= 0:
        abort(404)
    products, notice, _ = products_for_page(product_no)
    if not products:
        if notice:
            return message_page('상품을 불러오지 못했습니다', notice + ' 잠시 후 다시 시도해 주세요.', 503)
        abort(404)
    product = next((item for item in products if item['number'] == product_no), None)
    if product is None:
        abort(404)
    css, header, footer = shared_chrome()
    markup = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{p.name}} | 우리술</title><style>''' + css + '''</style></head><body><a class="skip-link" href="#main">본문으로 건너뛰기</a>{% if not live %}<div class="preview">운영 준비 중 · 시연용 상품이며 주문을 받지 않습니다.</div>{% endif %}''' + header + '''<main id="main"><section class="detail"><div class="product-image">{% if p.image %}<img src="{{p.image}}" alt="{{p.name}}">{% else %}우리술{% endif %}</div><div><nav class="breadcrumb" aria-label="현재 위치"><a href="/">홈</a> / <a href="/products">상품</a> / <span aria-current="page">{{p.name}}</span></nav><span class="eyebrow">OURISUL COLLECTION</span><h1>{{p.name}}</h1><p>{% if p.summary %}{{p.summary|striptags}}{% else %}실제 상품 정보는 출시 전에 안내합니다.{% endif %}</p><h2>{{p.price}}</h2>{% if not live %}<p>시연용 가격 · 출시 가격 미정</p>{% endif %}{% if p.purchase_enabled %}<div class="detail-note"><p>구매 버튼을 누르면 선택한 판매 채널로 이동합니다. 주문·결제·배송 안내는 해당 채널에서 확인해 주세요.</p></div><div class="detail-actions"><a class="button" href="{{p.mall_url}}" rel="noopener noreferrer">카페24로 이동하여 주문하기</a>{% if p.coupang_url %}<a class="button" href="{{p.coupang_url}}" rel="noopener noreferrer">쿠팡으로 이동하여 주문하기</a>{% endif %}</div>{% else %}<div class="detail-note"><p>{% if live %}이 상품은 판매 준비 중입니다.{% else %}시연용 상품입니다. 현재 주문을 받지 않습니다.{% endif %}</p></div>{% endif %}<p><a href="/guide">이용 안내 보기 →</a></p><a class="back" href="/products">← 상품 목록으로</a></div></section></main>''' + footer + '''<script>document.querySelectorAll('.mobile-nav a').forEach(function(link){link.addEventListener('click',function(){document.querySelector('.mobile-nav').open=false})});document.querySelectorAll('a[href="/products"]').forEach(function(link){link.classList.add('active-link');link.setAttribute('aria-current','true')})</script></body></html>'''
    return render_template_string(markup, p=product, live=store_live())



@app.route('/admin/launch-check')
@protected
def launch_check():
    products = public_catalog()
    sample = any('샘플' in p.get('product_name', '') for p in (products or []))
    return {'store_live': store_live(), 'cafe24_catalog_connected': products is not None,
            'cafe24_product_count': len(products or []), 'sample_products_present': sample,
            'approved_product_numbers': sorted(approved_product_numbers()),
            'store_live_requested': os.environ.get('STORE_LIVE', '').lower() == 'true',
            'coupang_api_configured': coupang_configured(),
            'coupang_api_verified': False,
            'coupang_order_read_enabled': os.environ.get('COUPANG_ORDER_READ_ENABLED', '').lower() == 'true',
            'cafe24_order_read_enabled': os.environ.get('CAFE24_ORDER_READ_ENABLED', '').lower() == 'true',
            'slack_order_webhook_configured': bool(os.environ.get('SLACK_ORDER_WEBHOOK_URL')),
            'direct_checkout_test_enabled': direct_checkout_test_enabled(),
            'synthetic_order_test_enabled': os.environ.get('SYNTHETIC_ORDER_TEST_ENABLED') == 'true',
            'direct_checkout_live_enabled': False,
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
    if not coupang_configured() or os.environ.get('COUPANG_ORDER_READ_ENABLED', '').lower() != 'true':
        return {'mode': 'demo', 'orders': demo_orders(),
                'message': '실제 주문 조회가 꺼져 있어 시연 데이터만 표시합니다.'}
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


