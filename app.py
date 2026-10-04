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
.card-link{display:block;color:inherit;text-decoration:none}.card-link:hover h3,.card-link:focus-visible h3{text-decoration:underline;text-underline-offset:6px}.flow-grid article[id]{scroll-margin-top:96px}.breadcrumb{font-size:13px;margin:0 0 22px;color:#625b51}.breadcrumb a{color:inherit;text-underline-offset:4px}.status-label{display:block;font-size:13px;color:#625b51;margin:5px 0}.skip-link{position:absolute;left:10px;top:-80px;background:#24221e;color:#fff;padding:10px;z-index:20}.skip-link:focus{top:10px}a:focus-visible,summary:focus-visible{outline:2px solid #7a4824;outline-offset:4px}
.detail-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.detail-actions .button{margin-top:8px}.detail-note{padding:15px 18px;border-left:3px solid #8c8274;background:#eee7d9}.detail-note p{margin:0}.detail-note a{color:inherit;text-underline-offset:4px}
footer{padding:60px 7%;border-top:1px solid #d8d1c4}@media(max-width:900px){.desktop-nav{display:none}.mobile-nav{display:block;position:relative}.mobile-nav summary{cursor:pointer;list-style:none;border:1px solid #9b9184;padding:7px 13px}.mobile-nav summary::-webkit-details-marker{display:none}.mobile-nav nav{position:absolute;right:0;top:42px;width:180px;background:#f6f2e9;border:1px solid #d8d1c4;box-shadow:0 12px 25px #24221e22;display:grid;padding:10px}.mobile-nav:not([open]) nav{display:none}.mobile-nav a{padding:10px 12px}}
@media(max-width:700px){.hero{min-height:570px;padding:18% 7%}.cards,.steps,.flow-grid{grid-template-columns:1fr}section{padding:80px 7%}.catalog{grid-template-columns:1fr 1fr;gap:12px}.detail{grid-template-columns:1fr}.product-copy{padding:13px}.product-copy h3{font-size:18px}}
@media(max-width:480px){.catalog{grid-template-columns:1fr}}
</style>
</head>
<body>
<a class="skip-link" href="#main">본문으로 건너뛰기</a>
{% if not live %}<div class="preview">운영 준비 중 · 현재 상품은 시연용이며 이 사이트에서는 주문을 받지 않습니다.</div>{% endif %}
<header><a class="logo" href="/">우리술</a><nav class="desktop-nav" aria-label="주요 메뉴"><a href="/about">브랜드</a><a href="/stories">술 이야기</a><a href="/products">상품</a><a href="/craft">양조 이야기</a><a href="/guide">이용 안내</a><a href="/faq">자주 묻는 질문</a><a href="/contact">문의</a></nav><details class="mobile-nav"><summary>메뉴</summary><nav aria-label="모바일 메뉴"><a href="/about">브랜드</a><a href="/stories">술 이야기</a><a href="/products">상품</a><a href="/craft">양조 이야기</a><a href="/guide">이용 안내</a><a href="/faq">자주 묻는 질문</a><a href="/contact">문의</a></nav></details></header>
<main id="main"><div class="hero"><div><div class="eyebrow">TRADITIONAL KOREAN LIQUOR</div><h1>오래된 지혜를<br>오늘의 술로.</h1><p class="lead">우리 조상이 빚어온 술에는 시간과 계절, 사람의 손길이 담겨 있습니다.<br>우리술은 그 가치를 오늘에 전하고자 합니다.</p><a class="button" href="#collection">우리술 만나기</a></div></div>
<section id="about" class="intro"><div class="eyebrow">OUR STORY</div><h2>마시기 위한 술,<br>기억하기 위한 전통.</h2><p>세계에는 각 나라를 대표하는 술이 있습니다. 우리에게도 오랜 시간 이어져 온 훌륭한 술과 그것을 빚어온 지혜가 있습니다.</p><p>우리술은 그 전통을 오늘의 사람들에게 다시 소개하고, 한국의 술 문화를 세계에 전하고자 합니다.</p><a class="text-link" href="/about">브랜드 이야기 더 보기 →</a></section>
<section id="collection"><div class="eyebrow">OUR COLLECTION</div><div class="cards"><a class="card card-link" href="/stories#cheongju"><span>01</span><h3>청주</h3><p>맑고 섬세한 향을 가진 우리 술</p><span>이야기 보기 →</span></a><a class="card card-link" href="/stories#takju"><span>02</span><h3>탁주</h3><p>쌀과 누룩이 만들어내는 깊은 풍미</p><span>이야기 보기 →</span></a><a class="card card-link" href="/stories#soju"><span>03</span><h3>소주</h3><p>앞으로 만나게 될 새로운 우리술</p><span>이야기 보기 →</span></a></div><a class="text-link" href="/stories">술 이야기 더 보기 →</a></section>
<section id="shop"><div class="eyebrow">SHOP OURISUL</div><h2>우리술 둘러보기</h2>{% if notice %}<p class="notice">{{notice}}</p>{% elif not products %}<p class="notice">등록된 상품이 없습니다.</p>{% else %}<div class="catalog">{% for product in products %}<a class="product" href="/products/{{product.number}}"><div class="product-image">{% if product.image %}<img src="{{product.image}}" alt="{{product.name}}" loading="lazy" referrerpolicy="no-referrer">{% else %}우리술{% endif %}</div><div class="product-copy"><h3>{{product.name}}</h3><p>{{product.price}}{% if not live %} · 시연용 가격{% endif %}</p><span class="status-label">{% if product.purchase_enabled %}판매 중{% elif live %}판매 준비 중{% else %}시연용 · 주문 불가{% endif %}</span></div></a>{% endfor %}</div>{% endif %}<a class="text-link" href="/products">상품 전체 보기 →</a> <a class="text-link" href="/guide">이용 안내 →</a></section>
<section id="craft" class="story"><div class="eyebrow">CRAFT NOTES</div><h2 class="section-title">한 잔에 담기는 시간</h2><div class="steps"><article><strong>원료</strong><p>술의 바탕이 되는 재료를 살핍니다.</p></article><article><strong>발효</strong><p>누룩과 시간이 빚어내는 향과 맛을 소개합니다.</p></article><article><strong>기록</strong><p>앞으로 선보일 술의 제작 과정을 이곳에 기록할 예정입니다.</p></article></div><a class="text-link" href="/craft">양조 이야기 더 보기 →</a></section>
<section id="guide" class="guide"><div class="eyebrow">SHOPPING GUIDE</div><h2 class="section-title">이용 안내</h2>{% if live %}<p>상품 상세에서 판매 채널을 선택하면 해당 채널의 주문 페이지로 이동합니다. 주문·결제·배송 안내는 선택한 판매 채널에서 확인해 주세요.</p>{% else %}<p>현재는 운영 준비 단계입니다. 표시된 상품은 시연용이며 이 사이트에서 주문을 받지 않습니다. 정식 상품과 판매 채널이 준비되면 구매 방법을 안내하겠습니다.</p>{% endif %}</section>
<section id="faq" class="faq"><div class="eyebrow">FAQ</div><h2 class="section-title">자주 묻는 질문</h2><details><summary>지금 상품을 주문할 수 있나요?</summary><p>{% if live %}상품 상세에 표시된 판매 채널에서 주문할 수 있습니다.{% else %}아직 주문을 받지 않습니다. 현재 상품은 시연용입니다.{% endif %}</p></details><details><summary>어떤 술을 소개하나요?</summary><p>청주와 탁주를 중심으로 우리 술의 종류와 이야기를 소개하고 있습니다. 실제 판매 상품은 출시 전에 안내합니다.</p></details><details><summary>쿠팡에서도 주문할 수 있나요?</summary><p>{% if live %}쿠팡에 등록된 상품은 상세 페이지에 쿠팡 구매 버튼이 표시됩니다.{% else %}쿠팡 판매 연동을 준비하고 있습니다. 실제 상품 등록 전에는 쿠팡 주문을 받지 않습니다.{% endif %}</p></details></section>
<section id="contact" class="story"><div class="eyebrow">CONTACT</div><h2 class="section-title">우리의 술을<br>우리의 이름으로.</h2><p>정성껏 빚은 술을 소개합니다. 공식 문의 창구는 운영 시작 전에 안내하겠습니다.</p><a class="text-link" href="/contact">문의 안내 보기 →</a></section></main>
<footer><strong>우리술</strong><br>우리의 술, 우리의 시간, 우리의 이야기.<nav class="footer-nav" aria-label="하단 메뉴"><a href="/about">브랜드</a><a href="/products">상품</a><a href="/guide">이용 안내</a><a href="/faq">FAQ</a><a href="/contact">문의</a></nav></footer>
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
