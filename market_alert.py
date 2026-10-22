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
    """Bybit 현재가"""
    url = "https://api.bybit.com/v5/market/tickers"
    res = requests.get(url, params={"category": "linear", "symbol": "BTCUSDT"})
    data = res.json()
    return float(data["result"]["list"][0]["lastPrice"])

def get_long_short_ratio():
    """Bybit 롱/숏 비율 (전체 계정)"""
    url = "https://api.bybit.com/v5/market/account-ratio"
    res = requests.get(url, params={
        "category": "linear",
        "symbol": "BTCUSDT",
        "period": "1h",
        "limit": 1
    })
    data = res.json()
    items = data.get("result", {}).get("list", [])
    if items:
        buy_ratio = float(items[0]["buyRatio"])
        sell_ratio = float(items[0]["sellRatio"])
        long_pct = buy_ratio * 100
        short_pct = sell_ratio * 100
        ratio = buy_ratio / sell_ratio if sell_ratio > 0 else 1.0
        return ratio, long_pct, short_pct
    return None, None, None

def get_liquidations():
    """Bybit 최근 1시간 청산 데이터"""
    url = "https://api.bybit.com/v5/market/liquidations"
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    one_hour_ago_ms = now_ms - 3600000

    long_liq = 0.0
    short_liq = 0.0

    try:
        # Buy side = 숏 포지션 청산 (숏 청산)
        # Sell side = 롱 포지션 청산 (롱 청산)
        for side in ["Buy", "Sell"]:
            res = requests.get(url, params={
                "category": "linear",
                "symbol": "BTCUSDT",
                "side": side,
                "startTime": one_hour_ago_ms,
                "endTime": now_ms,
                "limit": 200
            })
            orders = res.json().get("result", {}).get("list", [])
            total = sum(float(o.get("qty", 0)) * float(o.get("price", 0)) for o in orders)
            if side == "Sell":
                long_liq = total  # 롱 포지션이 청산됨
            else:
                short_liq = total  # 숏 포지션이 청산됨
    except Exception as e:
        print(f"청산 데이터 오류: {e}")

    return long_liq, short_liq

def get_open_interest():
    """Bybit OI 변화 (선물 유입/유출)"""
    url = "https://api.bybit.com/v5/market/open-interest"
    res = requests.get(url, params={
        "category": "linear",
        "symbol": "BTCUSDT",
        "intervalTime": "1h",
        "limit": 2
    })
    data = res.json()
    items = data.get("result", {}).get("list", [])
    if len(items) >= 2:
        # list[0]이 최신, list[1]이 이전
        latest_oi = float(items[0]["openInterest"])
        prev_oi = float(items[1]["openInterest"])
        price = get_btc_price()
        change_usd = (latest_oi - prev_oi) * price
        return change_usd, latest_oi * price
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
        return f"롱이 {long_ratio*100:.0f}% 청산당하는 중\n      → 하락 추세 진행 중", "하락"
    elif long_ratio <= 0.2:
        return f"숏이 {(1-long_ratio)*100:.0f}% 청산당하는 중\n      → 상승 추세 진행 중", "상승"
    else:
        return f"롱/숏 혼합 청산 (롱 {long_ratio*100:.0f}%)\n      → 변동성 구간", "혼합"

def interpret_oi_change(change_usd):
    if change_usd is None:
        return "데이터 없음", "없음"
    abs_c = abs(change_usd)
    if abs_c < 1_000_000:
        return "유입/유출 미미 — 관망 중", "미미"
    elif change_usd > 0:
        if abs_c > 10_000_000:
            return "강한 매수세 유입 중\n      → 상승 모멘텀 강화", "강한유입"
        else:
            return "매수세 유입 중\n      → 저점 매수 들어오는 중", "유입"
    else:
        if abs_c > 10_000_000:
            return "강한 매도세 유출 중\n      → 하락 추세 강화", "강한유출"
        else:
            return "포지션 정리 중\n      → 추세 약화 신호", "유출"

def generate_direction(price, ls_state, liq_state, oi_state, max_pain, call_wall, put_wall):
    lines = []
    bear = sum([
        ls_state in ("과열", "우세"),
        liq_state == "하락",
        oi_state in ("강한유출", "유출"),
    ])
    bull = sum([
        ls_state in ("숏과열", "숏우세"),
        liq_state == "상승",
        oi_state in ("강한유입", "유입"),
    ])

    if bear >= 3:
        lines.append("📉 하락 압력 강함")
    elif bear == 2 and bull == 0:
        lines.append("📉 하락 우세")
    elif bear == 2 and bull >= 1:
        lines.append("📉 하락 중이지만 저점 매수 유입 중")
    elif bull >= 3:
        lines.append("📈 상승 모멘텀 강함")
    elif bull == 2 and bear == 0:
        lines.append("📈 상승 우세")
    elif bull >= 1 and bear >= 1:
        lines.append("↔️ 눈치보기 구간 (혼조)")
    else:
        lines.append("↔️ 방향 불명확 — 관망")

    if max_pain and put_wall and call_wall:
        if price < put_wall:
            lines.append(f"→ ${put_wall:,} 풋월 지지 확인 필요")
            if oi_state in ("유입", "강한유입"):
                lines.append(f"→ 지지 확인되면 ${max_pain:,} Max Pain까지 반등 가능")
            lines.append(f"→ 반등 시 ${call_wall:,} 콜월 저항")
        elif price > call_wall:
            lines.append(f"→ ${call_wall:,} 콜월 돌파 시도 중")
            if bear >= 2:
                lines.append(f"→ 롱 과열 — 콜월 위에서 숏 유리")
            else:
                lines.append(f"→ 돌파 성공 시 추가 상승 가능")
        else:
            if price > max_pain:
                lines.append(f"→ Max Pain(${max_pain:,}) 위 — 하방 압력 존재")
                if bear >= 2:
                    lines.append(f"→ ${put_wall:,} 풋월까지 흘러내릴 가능성")
                lines.append(f"→ 반등 목표: ${call_wall:,} 콜월")
            else:
                lines.append(f"→ Max Pain(${max_pain:,}) 아래 — 상방 수렴 기대")
                if bull >= 1:
                    lines.append(f"→ ${max_pain:,}~${call_wall:,} 구간 반등 후 숏 유리")
    else:
        if bear >= 2 and bull >= 1:
            lines.append("→ 현재 지지 확인되면 단기 반등 가능")
            lines.append("→ 반등 후 저항선 근처에서 숏 유리")
        elif bear >= 3:
            lines.append("→ 지지선 이탈 시 추가 하락 대비")
        elif bull >= 2:
            lines.append("→ 저항선 돌파 시 추가 상승 가능")

    return "\n".join(lines)

# ────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────

def send_alert():
    time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    price = get_btc_price()
    ls_ratio, ls_long, ls_short = get_long_short_ratio()
    long_liq, short_liq = get_liquidations()
    oi_change, oi_total = get_open_interest()

    weekly_code = get_weekly_expiry()
    options = fetch_options_for_expiry(weekly_code)
    max_pain, call_wall, put_wall = calculate_levels(options) if options else (None, None, None)

    ls_label, ls_state = interpret_ls_ratio(ls_ratio, ls_long, ls_short) if ls_ratio else ("데이터 없음", "중립")
    liq_text, liq_state = interpret_liquidations(long_liq, short_liq)
    oi_text, oi_state = interpret_oi_change(oi_change)

    msg = f"⚡ BTC 시장 현황 ({time_str})\n"
    msg += f"현재가 : ${price:,.0f}\n"

    msg += f"\n📊 포지션\n"
    if ls_ratio:
        msg += f"롱/숏 비율  : {ls_ratio:.2f} {ls_label}\n"
        msg += f"롱 {ls_long:.1f}% / 숏 {ls_short:.1f}%\n"
    else:
        msg += f"롱/숏 비율  : 데이터 없음\n"

    msg += f"\n💥 청산 (1시간)\n"
    if long_liq >= 1_000_000:
        msg += f"롱 청산 : ${long_liq/1e6:.2f}M\n"
    else:
        msg += f"롱 청산 : ${long_liq/1000:.0f}K\n"
    if short_liq >= 1_000_000:
        msg += f"숏 청산 : ${short_liq/1e6:.2f}M\n"
    else:
        msg += f"숏 청산 : ${short_liq/1000:.0f}K\n"
    msg += f"해석 : {liq_text}\n"

    msg += f"\n🌊 선물 유입/유출\n"
    if oi_change is not None:
        sign = "+" if oi_change >= 0 else ""
        color = "🟢" if oi_change > 0 else "🔴"
        msg += f"순유입 : {sign}${oi_change/1e6:.2f}M {color}\n"
    else:
        msg += f"순유입 : 데이터 없음\n"
    msg += f"해석 : {oi_text}\n"

    msg += f"\n------------------------------\n"
    msg += f"🧭 종합 방향\n"
    msg += generate_direction(price, ls_state, liq_state, oi_state, max_pain, call_wall, put_wall)

    result = send_telegram(msg)
    print("전송 완료:", result)
    print(msg)

if __name__ == "__main__":
    send_alert()
