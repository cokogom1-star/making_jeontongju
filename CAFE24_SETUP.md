# 카페24 연결 준비

현재 상태: 인증 코드 추가 완료. 실제 카페24 인증·Render 배포 전입니다.

## Render 환경변수

- CAFE24_MALL_ID: 쇼핑몰 ID (도메인이나 관리자 이메일 제외)
- CAFE24_CLIENT_ID: 개발자센터 Client ID
- CAFE24_CLIENT_SECRET: 개발자센터 Secret (Render에 직접 입력)
- FLASK_SECRET_KEY: 무작위 32바이트 이상 문자열
- ADMIN_PASSWORD: 관리자 연결 페이지 전용 긴 무작위 비밀번호
- DATABASE_URL: 지속적으로 유지되는 PostgreSQL 연결 주소
- TOKEN_ENCRYPTION_KEY: cryptography.fernet.Fernet.generate_key()로 생성한 키

환경변수 값과 데이터베이스는 별도 설정해야 합니다. 코드는 DB나 유료 서비스를 생성하지 않습니다.
기존 홈페이지는 설정 전에도 열리며 인증 경로는 준비 전 503을 반환합니다.

## 연결 절차

1. 개발자센터 App URL: https://ourisul.onrender.com
2. Redirect URI: https://ourisul.onrender.com/oauth/callback
3. API 권한: mall.read_product
4. Render 환경변수 설정 및 배포 후 /admin/cafe24 열기
5. 사용자명 admin, ADMIN_PASSWORD에 입력한 비밀번호로 로그인
6. 연결 승인 버튼 → 카페24 승인 → 상품 조회 결과 확인

이 버전은 관리자 인증 및 상품 조회 확인 단계입니다. 공개 홈페이지에 상품을 자동 게시하거나 주문·결제를 처리하지 않습니다.
토큰은 PostgreSQL에 암호화해 저장합니다. DB와 암호화 키를 함께 유지해야 합니다.
토큰 갱신은 상품 조회 시 만료 응답을 받으면 실행합니다. 장기간 조회하지 않아 refresh token까지 만료되면 다시 연결해야 합니다.
OAuth 콜백의 인증 코드를 로그에 남기지 않도록 Gunicorn/프록시의 요청 URL 로그를 비활성화하거나 쿼리 문자열을 제외하십시오.

## 공식 문서

- https://developers.cafe24.com/en/app/front/app/develop/oauth/token
- https://developers.cafe24.com/en/app/front/app/develop/oauth/retoken
- https://developers.cafe24.com/app/front/app/develop/api/scope
