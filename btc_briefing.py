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

    # 이번 주 만기: 다음 금요일 (weekday 4)
    days_to_friday = (4 - today.weekday()) % 7
    if days_to_friday == 0:
        days_to_friday = 7
    weekly = today + timedelta(days=days_to_friday)
    weekly_code = get_expiry_code(weekly)

    # 이번 달 말 만기: 다음달 첫번째 금요일의 전주 금요일 (월말 마지막 금요일)
    # 간단하게: 이달 마지막 금요일
    year = today.year
    month = today.month
    # 다음달 1일
    if month == 12:
        next_month_first = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month_first = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    # 다음달 1일에서 뒤로 가서 마지막 금요일 찾기
    last_friday = next_month_first - timedelta(days=1)
    while last_friday.weekday() != 4:
        last_friday -= timedelta(days=1)
    monthly_code = get_expiry_code(last_friday)

    # 중복 제거
    expiries = []
    seen = set()
    for label, code, dt in [("오늘", today_code, today),
                              ("이번 주", weekly_code, weekly),
                              ("이번 달", monthly_code, last_friday)]:
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

def send_briefing():
    expiries = get_target_expiries()
    all_options = fetch_all_options()
    current_price = get_btc_price()

    time_str = datetime.now(timezone.utc).strftime("%m/%d %H:%M UTC")

    msg = f"📊 BTC 세력 지도 브리핑 ({time_str})\n"
    msg += f"현재가 : ${current_price:,.0f}\n"

    today_max_pain = None

    for label, code, dt in expiries:
        options = filter_by_expiry(all_options, code)
        if not options:
            msg += f"\n📅 {label} 만기 ({code})\n데이터 없음\n"
            continue

        max_pain, call_wall, put_wall = calculate_max_pain(options)
        if max_pain is None:
            msg += f"\n📅 {label} 만기 ({code})\n계산 실패\n"
            continue

        if label == "오늘":
            today_max_pain = max_pain

        msg += f"\n📅 {label} 만기 ({code})\n"
        msg += f"Put Wall  : ${put_wall:,}\n"
        msg += f"Max Pain  : ${max_pain:,}\n"
        msg += f"Call Wall : ${call_wall:,}\n"

    if today_max_pain:
        position = "위" if current_price > today_max_pain else "아래"
        msg += f"\n------------------------------\n"
        msg += f"현재가는 오늘 Max Pain(${today_max_pain:,})보다 {position}"

    result = send_telegram(msg)
    print("전송 완료:", result)
    print(msg)

if __name__ == "__main__":
    send_briefing()
