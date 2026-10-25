import requests
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = "8784146347:AAGdLHuXObJih1sXPbRFbTn94QylxlGeVcs"
CHAT_ID = "531163029"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    res = requests.post(url, json=payload)
    return res.json()

def get_expiry_code(dt):
    month_map = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
                 7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}
    day = str(dt.day).zfill(2)
    month = month_map[dt.month]
    year = str(dt.year)[-2:]
    return f"{day}{month}{year}"

def get_target_expiries():
    now = datetime.now(timezone.utc)

    # 오늘 만기: UTC 08시 이후면 다음날
    today = now + timedelta(days=1) if now.hour >= 8 else now
    today_code = get_expiry_code(today)

    # 내일 만기
    tomorrow = today + timedelta(days=1)
    tomorrow_code = get_expiry_code(tomorrow)

    # 모레 만기
    day_after = today + timedelta(days=2)
    day_after_code = get_expiry_code(day_after)

    # 이번 주 만기: 다음 금요일 (weekday 4)
    days_to_friday = (4 - today.weekday()) % 7
    if days_to_friday == 0:
        days_to_friday = 7
    weekly = today + timedelta(days=days_to_friday)
    weekly_code = get_expiry_code(weekly)

    # 월간 만기: 이달 마지막 금요일
    def last_friday_of_month(year, month):
        if month == 12:
            nxt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            nxt = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        lf = nxt - timedelta(days=1)
        while lf.weekday() != 4:
            lf -= timedelta(days=1)
        return lf

    last_friday = last_friday_of_month(today.year, today.month)
    if last_friday.date() <= weekly.date():
        nm = today.month + 1 if today.month < 12 else 1
        ny = today.year if today.month < 12 else today.year + 1
        last_friday = last_friday_of_month(ny, nm)
    monthly_code = get_expiry_code(last_friday)

    # 중복 제거하면서 순서 유지
    expiries = []
    seen = set()
    for label, code, dt in [
        ("오늘", today_code, today),
        ("내일", tomorrow_code, tomorrow),
        ("모레", day_after_code, day_after),
        ("이번 주 금요일", weekly_code, weekly),
        ("이번 달", monthly_code, last_friday)
    ]:
        if code not in seen:
            seen.add(code)
            expiries.append((label, code, dt))

    return expiries

def fetch_all_options():
    url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    params = {"currency": "BTC", "kind": "option"}
    res = requests.get(url, params=params)
    data = res.json()
    if "result" not in data:
        return []
    return data["result"]

def filter_by_expiry(all_options, expiry_code):
    return [item for item in all_options if expiry_code in item.get("instrument_name", "")]

def calculate_max_pain(options):
    strikes = {}
    for opt in options:
        name = opt.get("instrument_name", "")
        parts = name.split("-")
        if len(parts) < 4:
            continue
        try:
            strike = float(parts[2])
            opt_type = parts[3]
            oi = opt.get("open_interest", 0) or 0
            if strike not in strikes:
                strikes[strike] = {"C": 0, "P": 0}
            if opt_type == "C":
                strikes[strike]["C"] += oi
            elif opt_type == "P":
                strikes[strike]["P"] += oi
        except:
            continue

    if not strikes:
        return None, None, None

    strike_list = sorted(strikes.keys())
    pain = {}
    for s in strike_list:
        total_loss = 0
        for k, v in strikes.items():
            if s > k:
                total_loss += v["C"] * (s - k)
            if s < k:
                total_loss += v["P"] * (k - s)
        pain[s] = total_loss

    max_pain = min(pain, key=pain.get)
    call_wall = max(strike_list, key=lambda s: strikes[s]["C"])
    put_wall = max(strike_list, key=lambda s: strikes[s]["P"])
    return int(max_pain), int(call_wall), int(put_wall)

def get_btc_price():
    url = "https://www.deribit.com/api/v2/public/get_index_price"
    params = {"index_name": "btc_usd"}
    res = requests.get(url, params=params)
    data = res.json()
    return data["result"]["index_price"]

def generate_scenario(expiry_data, current_price):
    """옵션 데이터 기반 이번주 시나리오 자동 생성 (트레이딩 감성 직관 언어)"""

    today_mp = None
    today_put = None
    today_call = None
    tomorrow_mp = None
    weekly_mp = None
    weekly_put = None
    weekly_call = None

    for label, code, dt, max_pain, call_wall, put_wall in expiry_data:
        if max_pain is None:
            continue
        if label == "오늘":
            today_mp = max_pain
            today_put = put_wall
            today_call = call_wall
        elif label == "내일":
            tomorrow_mp = max_pain
        elif label == "이번 주 금요일":
            weekly_mp = max_pain
            weekly_put = put_wall
            weekly_call = call_wall

    if not (today_mp and weekly_mp):
        return ""

    lines = []
    now = datetime.now(timezone.utc)
    is_weekend = now.weekday() >= 5  # 토/일

    # ── 중복 예외: breakline 계산 (핵심 분기점) ──
    # 하방 시나리오: 풋벽 == 주간MP이면 현재가-500 사용
    if weekly_mp < current_price:
        if today_put and today_put != weekly_mp:
            breakline = today_put
        else:
            breakline = int(current_price - 500)
    else:
        # 상방 시나리오: 콜벽 또는 주간MP+1000
        if today_call and today_call != weekly_mp:
            breakline = today_call
        else:
            breakline = int(current_price + 500)

    weekly_dist = abs(current_price - weekly_mp)
    mp_trend = (tomorrow_mp - today_mp) if tomorrow_mp else 0

    # ── 오늘 구간 ──
    if today_mp and today_put:
        if current_price > today_mp:
            today_line = f"${today_mp:,} 콜벽 저항 돌파 시도 — 자력 위쪽, 롱 세력 우위"
        elif current_price < today_put:
            today_line = f"${today_put:,} 풋벽 아래 이탈 — 하방 스위핑 진행 중"
        elif is_weekend:
            today_line = f"${today_put:,}~${today_mp:,} 박스권 핑퐁 스캘핑 구간"
        else:
            # 현재가가 풋벽과 MP 사이 → MP 자석 위치 기준 설명
            if weekly_mp < current_price:
                today_line = f"${today_mp:,} 자석에 끌려 올라가는 반등과 하방 압력이 겹치는 박스권 핑퐁 구간"
            else:
                today_line = f"${today_mp:,} 자석 위로 상승 압력 — ${today_put:,}~${today_mp:,} 박스권 상단 돌파 여부 관건"
        lines.append(f"· 오늘      : {today_line}")

    # ── 내일~모레 방향 ──
    if tomorrow_mp:
        if mp_trend > 500:
            lines.append(f"· 내일~모레 : ${tomorrow_mp:,} 자석 위로 이동 — 상방 자력 강화, 눌림목 롱 대응")
        elif mp_trend < -500:
            lines.append(f"· 내일~모레 : MP ${tomorrow_mp:,}으로 하락 이동 — 하방 압력 증가, 반등 시 숏 준비")
        else:
            # MP 횡보 → weekly MP 방향성 기준으로 설명
            if weekly_mp < current_price:
                lines.append(f"· 내일~모레 : ${today_mp:,} 부근에서 힘겨루기 횡보 (상·하방 이탈 나오는 쪽 따라붙기)")
            else:
                lines.append(f"· 내일~모레 : ${today_mp:,} 자석 중심 횡보 — 상방 돌파 시 추세 추종")
    else:
        lines.append(f"· 내일~모레 : ${today_mp:,} 자석 중심 횡보 유지 — 이탈 방향 추종")

    # ── 수~목 핵심 분기점 ──
    if weekly_mp < current_price:
        # 하방 시나리오
        if weekly_dist > 1500:
            lines.append(f"· 수~목     : [핵심 분기점] ${breakline:,}이 무너지는지 확인 ➔ 깨지면 아래로 롱 청산 터지며 ${weekly_mp:,}까지 하방 가속")
        else:
            lines.append(f"· 수~목     : ${weekly_mp:,} 근접 수렴 — 단기 반등 나오면 즉시 숏 이탈 스위치 ON")
    else:
        # 상방 시나리오
        if weekly_dist > 1500:
            lines.append(f"· 수~목     : [핵심 분기점] ${breakline:,} 돌파 확인 ➔ 숏 청산 터지며 ${weekly_mp:,}까지 상방 가속")
        else:
            lines.append(f"· 수~목     : ${weekly_mp:,} 근접 수렴 — 단기 눌림 나오면 즉시 롱 이탈 스위치 ON")

    # ── 금요일 ──
    direction_kr = "하방" if weekly_mp < current_price else "상방"
    if weekly_dist > 2000:
        lines.append(f"· 금요일    : 주간 목표가 ${weekly_mp:,}로 가려는 힘 작용 (단, 대형 현물 호재/악재 들어오면 무효)")
    else:
        lines.append(f"· 금요일    : ${weekly_mp:,} 수렴 거의 확정 — 만기 당일 변동성 주의, 무리한 추격 금지")

    # ── 한 줄 핵심 ──
    if weekly_mp < current_price:
        if mp_trend >= 0:
            core = f'"화요일까지는 ${today_mp:,} 자력에 묶여 횡보, 수~목에 지지선(${breakline:,})이 깨지는 걸 눈으로 확인한 뒤 ${weekly_mp:,}을 향해 숏으로 따라붙는다."'
        else:
            core = f'"단기~주간 MP 모두 하방 정렬 — ${breakline:,} 이탈 확인 즉시 ${weekly_mp:,} 타깃 숏 대응."'
    else:
        if mp_trend <= 0:
            core = f'"단기 조정 이후 주간 MP ${weekly_mp:,} 상방 자력 작동 — ${breakline:,} 돌파 확인 시 롱으로 따라붙는다."'
        else:
            core = f'"단기~주간 MP 모두 상방 정렬 — ${breakline:,} 돌파 확인 즉시 ${weekly_mp:,} 타깃 롱 대응."'

    lines.append(f'\n💡 한 줄 핵심\n{core}')

    return "\n".join(lines)

def send_briefing():
    expiries = get_target_expiries()
    all_options = fetch_all_options()
    current_price = get_btc_price()

    # KST 시간
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    time_str = kst.strftime("%m/%d %H:%M KST")

    msg = f"📊 BTC 세력 지도 브리핑 ({time_str})\n"
    msg += f"현재가 : ${current_price:,.0f}\n"

    expiry_data = []

    # ━━━ 단기 흐름 ━━━
    msg += "\n━━━ 단기 흐름 (매일 만기) ━━━"
    for label, code, dt in expiries:
        if label in ["오늘", "내일", "모레"]:
            options = filter_by_expiry(all_options, code)
            if not options:
                expiry_data.append((label, code, dt, None, None, None))
                continue
            max_pain, call_wall, put_wall = calculate_max_pain(options)
            expiry_data.append((label, code, dt, max_pain, call_wall, put_wall))
            if max_pain:
                msg += f"\n📅 {code}: Max Pain ${max_pain:,} | 풋 ${put_wall:,} | 콜 ${call_wall:,}"

    # ━━━ 이번주 큰 그림 ━━━
    msg += "\n\n━━━ 이번주 큰 그림 (금요일 만기) ━━━"
    for label, code, dt in expiries:
        if label == "이번 주 금요일":
            options = filter_by_expiry(all_options, code)
            if not options:
                expiry_data.append((label, code, dt, None, None, None))
                continue
            max_pain, call_wall, put_wall = calculate_max_pain(options)
            expiry_data.append((label, code, dt, max_pain, call_wall, put_wall))
            if max_pain:
                msg += f"\n📅 {code}: Max Pain ${max_pain:,} | 풋 ${put_wall:,} | 콜 ${call_wall:,}"
                dist = abs(current_price - max_pain)
                direction = "하방" if max_pain < current_price else "상방"
                msg += f"\n⚠️ 금요일 Max Pain ${max_pain:,} → {direction} 압력 존재"
                if dist > 2000:
                    # 관망 기준은 오늘 풋벽 기준으로 (weekly put wall 아님)
                    ref_put = None
                    for ed_label, ed_code, ed_dt, ed_mp, ed_call, ed_put in expiry_data:
                        if ed_label == "오늘" and ed_put:
                            ref_put = ed_put
                            break
                    if ref_put:
                        msg += f", 현재가와 거리(${dist:,.0f})가 있어 ${ref_put:,} 이탈 전까지는 관망 필수"
                    else:
                        msg += f", 현재가와 거리(${dist:,.0f})가 있어 단기 지지 이탈 전까지는 관망 필수"

    # ━━━ 월간 방향 ━━━
    msg += "\n\n━━━ 월간 방향 ━━━"
    for label, code, dt in expiries:
        if label == "이번 달":
            options = filter_by_expiry(all_options, code)
            if not options:
                expiry_data.append((label, code, dt, None, None, None))
                continue
            max_pain, call_wall, put_wall = calculate_max_pain(options)
            expiry_data.append((label, code, dt, max_pain, call_wall, put_wall))
            if max_pain:
                msg += f"\n📅 {code}: Max Pain ${max_pain:,} | 풋 ${put_wall:,} | 콜 ${call_wall:,}"
                msg += f"\n→ 이번 달 전체 기준선은 ${max_pain:,} 박스권 유지"

    # ━━━ 시나리오 ━━━
    scenario = generate_scenario(expiry_data, current_price)
    if scenario:
        msg += f"\n\n🎯 이번주 실전 시나리오\n{scenario}"

    result = send_telegram(msg)
    print("전송 완료:", result)
    print(msg)

if __name__ == "__main__":
    send_briefing()
