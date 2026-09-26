from flask import Flask, render_template_string

app = Flask(__name__)

from cafe24 import init_app
init_app(app)

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
.logo{font-size:27px;font-weight:bold}nav a{margin-left:25px;color:inherit;text-decoration:none;font-size:14px}
.hero{min-height:650px;padding:10% 9%;display:flex;align-items:center;background:linear-gradient(90deg,rgba(246,242,233,.97),rgba(246,242,233,.68)),url("https://images.unsplash.com/photo-1536935338788-846bb9981813?auto=format&fit=crop&w=1800&q=80") center/cover}
.eyebrow{letter-spacing:3px;font-size:11px}.hero h1{font-size:clamp(45px,7vw,78px);line-height:1.15;font-weight:500;margin:15px 0}.lead{font-size:17px}.button{display:inline-block;margin-top:20px;padding:11px 25px;border:1px solid #24221e;text-decoration:none;color:inherit}
section{max-width:1200px;margin:auto;padding:110px 7%}.intro h2,.story h2{font-size:clamp(35px,5vw,58px);line-height:1.25;font-weight:500}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;padding-top:0}.card{border-top:1px solid #888;padding:30px 0}.card h3{font-size:30px;font-weight:500;margin:15px 0}
.story{border-top:1px solid #d8d1c4}.story p{font-size:18px;max-width:760px}
footer{padding:60px 7%;border-top:1px solid #d8d1c4}@media(max-width:700px){nav a{margin-left:10px;font-size:12px}.hero{min-height:570px;padding:18% 7%}.cards{grid-template-columns:1fr}section{padding:80px 7%}}
</style>
</head>
<body>
<header><div class="logo">우리술</div><nav><a href="#about">브랜드</a><a href="#collection">술</a><a href="#contact">문의</a></nav></header>
<div class="hero"><div><div class="eyebrow">TRADITIONAL KOREAN LIQUOR</div><h1>오래된 지혜를<br>오늘의 술로.</h1><p class="lead">우리 조상이 빚어온 술에는 시간과 계절, 사람의 손길이 담겨 있습니다.<br>우리술은 그 가치를 오늘에 전하고자 합니다.</p><a class="button" href="#collection">우리술 만나기</a></div></div>
<section id="about" class="intro"><div class="eyebrow">OUR STORY</div><h2>마시기 위한 술,<br>기억하기 위한 전통.</h2><p>세계에는 각 나라를 대표하는 술이 있습니다. 우리에게도 오랜 시간 이어져 온 훌륭한 술과 그것을 빚어온 지혜가 있습니다.</p><p>우리술은 그 전통을 오늘의 사람들에게 다시 소개하고, 한국의 술 문화를 세계에 전하고자 합니다.</p></section>
<section id="collection"><div class="eyebrow">OUR COLLECTION</div><div class="cards"><div class="card"><span>01</span><h3>청주</h3><p>맑고 섬세한 향을 가진 우리 술</p></div><div class="card"><span>02</span><h3>탁주</h3><p>쌀과 누룩이 만들어내는 깊은 풍미</p></div><div class="card"><span>03</span><h3>소주</h3><p>앞으로 만나게 될 새로운 우리술</p></div></div></section>
<section id="contact" class="story"><div class="eyebrow">OURISUL</div><h2>우리의 술을<br>우리의 이름으로.</h2><p>제품 판매와 주문 기능은 Cafe24 연동 단계에서 연결합니다.</p></section>
<footer><strong>우리술</strong><br>우리의 술, 우리의 시간, 우리의 이야기.<br><small>현재 브랜드 홈페이지 개발 단계</small></footer>
</body></html>
"""

@app.route("/")
def home():
    return render_template_string(HTML)

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
