import requests
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = "8784146347:AAGdLHuXObJih1sXPbRFbTn94QylxlGeVcs"
CHAT_ID = "531163029"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    res = requests.post(url, json=payload)
    return res.json()

# ────────────────────────────────────────────
# Bybit 데이터 (지역 제한 없음)
# ────────────────────────────────────────────

def get_btc_price():
    """OKX 현재가"""
    url = "https://www.okx.com/api/v5/market/ticker"
    res = requests.get(url, params={"instId": "BTC-USDT-SWAP"})
    print(f"가격 응답 상태코드: {res.status_code}")
    print(f"가격 응답 내용: {res.text[:200]}")
    data = res.json()
    return float(data["data"][0]["last"])

def get_long_short_ratio():
    """OKX 전체 계정 롱/숏 비율"""
    try:
        url = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
        res = requests.get(url, params={"ccy": "BTC", "period": "1H"})
        data = res.json()
        items = data.get("data", [])
        if items:
            ratio = float(items[0][1])
            long_pct = ratio / (1 + ratio) * 100
            short_pct = 100 - long_pct
            return ratio, long_pct, short_pct
    except Exception as e:
        print(f"롱숏 비율 오류: {e}")
    return None, None, None

def get_top_trader_ratio():
    """OKX 탑트레이더 롱/숏 비율"""
    try:
        url = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract-top-trader"
        res = requests.get(url, params={"ccy": "BTC", "period": "1H"})
        data = res.json()
        items = data.get("data", [])
        if items:
            ratio = float(items[0][1])
            long_pct = ratio / (1 + ratio) * 100
            short_pct = 100 - long_pct
            return ratio, long_pct, short_pct
    except Exception as e:
        print(f"탑트레이더 비율 오류: {e}")
    return None, None, None

def get_liquidations():
    """OKX 청산 데이터 - public liquidation-orders API"""
    long_liq = 0.0
    short_liq = 0.0
    try:
        url = "https://www.okx.com/api/v5/public/liquidation-orders"
        # uly 파라미터로 BTC 선물 전체 조회
        res = requests.get(url, params={
            "instType": "SWAP",
            "uly": "BTC-USDT",
            "state": "filled",
            "limit": 100
        })
        print(f"청산 응답: {res.status_code} {res.text[:400]}")
        data = res.json()
        items = data.get("data", [])
        for item in items:
            details = item.get("details", [])
            for d in details:
                sz = float(d.get("sz", 0))
                px = float(d.get("bkPx", 0))
                side = d.get("side", "")
                val = sz * px
                if side == "sell":   # 롱 포지션 청산
                    long_liq += val
                elif side == "buy":  # 숏 포지션 청산
                    short_liq += val
    except Exception as e:
        print(f"청산 데이터 오류: {e}")
    return long_liq, short_liq

def get_open_interest():
    """OKX OI 변화 (선물 유입/유출) - OI는 계약수가 아닌 USD 기준"""
    try:
        # open-interest-volume: [ts, oi_usd, volume_usd]
        url = "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume"
        res = requests.get(url, params={"ccy": "BTC", "period": "1H"})
        print(f"OI 응답: {res.status_code} {res.text[:300]}")
        data = res.json()
        items = data.get("data", [])
        if len(items) >= 2:
            # items[0][1] = OI in USD (이미 달러 환산된 값)
            latest_oi = float(items[0][1])
            prev_oi = float(items[1][1])
            change_usd = latest_oi - prev_oi
            return change_usd, latest_oi
    except Exception as e:
        print(f"OI 오류: {e}")
    return None, None

# ────────────────────────────────────────────
# Deribit 옵션 데이터
# ────────────────────────────────────────────

def get_expiry_code(dt):
    month_map = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
                 7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}
    return f"{str(dt.day).zfill(2)}{month_map[dt.month]}{str(dt.year)[-2:]}"

def get_weekly_expiry():
    now = datetime.now(timezone.utc)
    today = now + timedelta(days=1) if now.hour >= 8 else now
    days_to_friday = (4 - today.weekday()) % 7
    if days_to_friday == 0:
        days_to_friday = 7
    return get_expiry_code(today + timedelta(days=days_to_friday))

def fetch_options_for_expiry(expiry_code):
    url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    res = requests.get(url, params={"currency": "BTC", "kind": "option"})
    data = res.json()
    if "result" not in data:
        return []
    return [item for item in data["result"] if expiry_code in item.get("instrument_name", "")]

def calculate_levels(options):
    strikes = {}
    for opt in options:
        parts = opt.get("instrument_name", "").split("-")
        if len(parts) < 4:
            continue
        try:
            strike = float(parts[2])
            opt_type = parts[3]
            oi = opt.get("open_interest", 0) or 0
            if strike not in strikes:
                strikes[strike] = {"C": 0, "P": 0}
            strikes[strike][opt_type] += oi
        except:
            continue

    if not strikes:
        return None, None, None

    strike_list = sorted(strikes.keys())
    pain = {}
    for s in strike_list:
        total_loss = sum(
            v["C"] * (s - k) if s > k else v["P"] * (k - s) if s < k else 0
            for k, v in strikes.items()
        )
        pain[s] = total_loss

    max_pain = min(pain, key=pain.get)
    call_wall = max(strike_list, key=lambda s: strikes[s]["C"])
    put_wall = max(strike_list, key=lambda s: strikes[s]["P"])
    return int(max_pain), int(call_wall), int(put_wall)

# ────────────────────────────────────────────
# 해석 로직
# ────────────────────────────────────────────

def interpret_ls_ratio(ratio, long_pct, short_pct):
    if ratio >= 2.0:
        return "🔴 롱 과열", "과열"
    elif ratio >= 1.3:
        return "🟡 롱 우세", "우세"
    elif ratio <= 0.7:
        return "🔵 숏 과열", "숏과열"
    elif ratio <= 0.9:
        return "🟡 숏 우세", "숏우세"
    else:
        return "⚪ 중립", "중립"

def interpret_liquidations(long_liq, short_liq):
    total = long_liq + short_liq
    if total < 100000:
        return "청산 미미 — 조용한 구간", "조용"
    long_ratio = long_liq / total if total > 0 else 0.5
    if long_ratio >= 0.8:
        return f"롱 {long_ratio*100:.0f}% 청산 — 하락 추세", "하락"
    elif long_ratio <= 0.2:
        return f"숏 {(1-long_ratio)*100:.0f}% 청산 — 상승 추세", "상승"
    else:
        return f"롱/숏 혼합 청산 (롱 {long_ratio*100:.0f}%) — 변동성 구간", "혼합"

def interpret_oi_change(change_usd):
    if change_usd is None:
        return "데이터 없음", "없음"
    abs_c = abs(change_usd)
    if abs_c < 1_000_000:
        return "유입/유출 미미 — 관망 중", "미미"
    elif change_usd > 0:
        if abs_c > 10_000_000:
            return "강한 신규 매수 유입 — 상승 모멘텀", "강한유입"
        else:
            return "매수 유입 중 — 저점 매수 들어오는 중", "유입"
    else:
        if abs_c > 10_000_000:
            return "강한 포지션 청산 — 하락 추세 강화", "강한유출"
        else:
            return "포지션 정리 중 — 추세 약화", "유출"

def generate_direction(price, ls_state, top_state, liq_state, oi_state, max_pain, call_wall, put_wall):
    bear = sum([
        ls_state in ("과열", "우세"),
        top_state in ("과열", "우세"),
        liq_state == "하락",
        oi_state in ("강한유출", "유출"),
    ])
    bull = sum([
        ls_state in ("숏과열", "숏우세"),
        top_state in ("숏과열", "숏우세"),
        liq_state == "상승",
        oi_state in ("강한유입", "유입"),
    ])

    # 탑트레이더 vs 개인 엇갈림 (가장 중요한 시그널)
    top_vs_retail_short = ls_state in ("과열", "우세") and top_state in ("숏과열", "숏우세")
    top_vs_retail_long = ls_state in ("숏과열", "숏우세") and top_state in ("과열", "우세")

    if top_vs_retail_short and liq_state == "하락":
        now = "개인 롱 과열 + 탑트레이더 숏 + 롱 청산 진행 중"
        then = "📉 숏 우위 — 반등 시 숏 진입 유리, 롱은 손절 빠르게"
    elif top_vs_retail_short and oi_state in ("강한유출", "유출"):
        now = "개인 롱 과열인데 탑트레이더는 숏, 자금도 빠지는 중"
        then = "📉 하락 가능성 높음 — 롱 진입 금지, 숏 대기"
    elif top_vs_retail_long and liq_state == "상승":
        now = "개인 숏 과열 + 탑트레이더 롱 + 숏 청산 나오는 중"
        then = "📈 롱 우위 — 눌림목에서 롱, 숏은 손절 빠르게"
    elif top_vs_retail_long and oi_state in ("강한유입", "유입"):
        now = "개인 숏인데 탑트레이더는 롱, 자금도 들어오는 중"
        then = "📈 상승 가능성 높음 — 숏 진입 금지, 롱 대기"
    elif bear >= 3:
        now = "전체+탑트레이더 롱 과열, 청산 나오고 자금도 빠짐"
        then = "📉 강한 하락 — 반등 짧게 보고 숏 유지"
    elif bear == 2 and bull == 0:
        now = "하락 신호 2개, 받쳐주는 힘 없음"
        then = "📉 흘러내릴 가능성 — 숏 유리, 롱은 관망"
    elif bull >= 3:
        now = "전체+탑트레이더 숏 과열, 청산 나오고 자금도 들어옴"
        then = "📈 강한 상승 — 조정 짧게 보고 롱 유지"
    elif bull == 2 and bear == 0:
        now = "상승 신호 2개, 누르는 힘 없음"
        then = "📈 올라갈 가능성 — 롱 유리, 숏은 관망"
    elif liq_state == "혼합":
        now = "롱/숏 동시 청산 — 변동성 구간"
        then = "⚡ 방향 터질 수 있음 — 브레이크아웃 확인 후 진입"
    elif bull >= 1 and bear >= 1:
        now = "상승/하락 신호 혼재"
        then = "↔️ 횡보 가능성 — 방향 나오기 전까지 관망"
    else:
        now = "신호 없음"
        then = "↔️ 관망"

    return "\n".join([now, then])

# ────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────

def send_alert():
    time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    price = get_btc_price()
    ls_ratio, ls_long, ls_short = get_long_short_ratio()
    top_ratio, top_long, top_short = get_top_trader_ratio()
    long_liq, short_liq = get_liquidations()
    oi_change, oi_total = get_open_interest()

    ls_label, ls_state = interpret_ls_ratio(ls_ratio, ls_long, ls_short) if ls_ratio else ("데이터 없음", "중립")
    top_label, top_state = interpret_ls_ratio(top_ratio, top_long, top_short) if top_ratio else ("데이터 없음", "중립")
    liq_text, liq_state = interpret_liquidations(long_liq, short_liq)
    oi_text, oi_state = interpret_oi_change(oi_change)

    msg = f"⚡ BTC 시장 현황 ({time_str})\n"
    msg += f"현재가 : ${price:,.0f}\n"

    msg += f"\n📊 포지션\n"
    if ls_ratio:
        msg += f"전체  : {ls_ratio:.2f} {ls_label}\n"
        msg += f"롱 {ls_long:.1f}% / 숏 {ls_short:.1f}%\n"
    else:
        msg += f"전체  : 데이터 없음\n"
    if top_ratio:
        msg += f"탑트레이더 : {top_ratio:.2f} {top_label}\n"
    else:
        msg += f"탑트레이더 : 데이터 없음\n"
    # 포지션 해석
    if ls_state in ("과열", "우세") and top_state in ("숏과열", "숏우세"):
        msg += f"→ 개인 롱 과열, 탑트레이더는 반대 — 개인 털릴 가능성\n"
    elif ls_state in ("과열", "우세") and top_state in ("과열", "우세"):
        msg += f"→ 전체+탑트레이더 모두 롱 과열 — 강한 하락 압력\n"
    elif ls_state in ("숏과열", "숏우세") and top_state in ("과열", "우세"):
        msg += f"→ 개인 숏, 탑트레이더 롱 — 숏 스퀴즈 주의\n"
    elif ls_state in ("숏과열", "숏우세") and top_state in ("숏과열", "숏우세"):
        msg += f"→ 전체+탑트레이더 모두 숏 과열 — 강한 반등 압력\n"
    elif ls_state == "중립" and top_state == "중립":
        msg += f"→ 방향 대기 중\n"
    else:
        msg += f"→ 혼조 — 방향 불명확\n"

    msg += f"\n💥 청산 (1시간)\n"
    if long_liq >= 1_000_000:
        msg += f"롱 청산 : ${long_liq/1e6:.2f}M\n"
    else:
        msg += f"롱 청산 : ${long_liq/1000:.0f}K\n"
    if short_liq >= 1_000_000:
        msg += f"숏 청산 : ${short_liq/1e6:.2f}M\n"
    else:
        msg += f"숏 청산 : ${short_liq/1000:.0f}K\n"
    msg += f"→ {liq_text}\n"

    msg += f"\n🌊 선물 유입/유출\n"
    if oi_change is not None:
        sign = "+" if oi_change >= 0 else ""
        color = "🟢" if oi_change > 0 else "🔴"
        msg += f"순유입 : {sign}${oi_change/1e6:.2f}M {color}\n"
    else:
        msg += f"순유입 : 데이터 없음\n"
    msg += f"→ {oi_text}\n"

    msg += f"\n------------------------------\n"
    msg += f"🧭 종합 방향\n"
    msg += generate_direction(price, ls_state, top_state, liq_state, oi_state, None, None, None)

    result = send_telegram(msg)
    print("전송 완료:", result)
    print(msg)

if __name__ == "__main__":
    send_alert()
