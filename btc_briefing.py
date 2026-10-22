import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = "8784146347:AAGdLHuXObJih1sXPbRFbTn94QylxlGeVcs"
CHAT_ID = "531163029"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    res = requests.post(url, json=payload)
    return res.json()

def get_today_expiry():
    now = datetime.now(timezone.utc)
    if now.hour >= 8:
        from datetime import timedelta
        now = now + timedelta(days=1)
    month_map = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
                 7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}
    day = str(now.day).zfill(2)
    month = month_map[now.month]
    year = str(now.year)[-2:]
    return f"{day}{month}{year}"

def fetch_option_data(expiry):
    url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
    params = {"currency": "BTC", "kind": "option"}
    res = requests.get(url, params=params)
    data = res.json()
    if "result" not in data:
        return []
    filtered = []
    for item in data["result"]:
        instrument = item.get("instrument_name", "")
        if expiry in instrument:
            filtered.append(item)
    return filtered

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
    expiry = get_today_expiry()
    options = fetch_option_data(expiry)
    if not options:
        send_telegram(f"오늘 만기({expiry}) 옵션 데이터 없음")
        return
    max_pain, call_wall, put_wall = calculate_max_pain(options)
    current_price = get_btc_price()
    if max_pain is None:
        send_telegram("Max Pain 계산 실패")
        return
    position = "위" if current_price > max_pain else "아래"
    now_utc = datetime.now(timezone.utc)
    time_str = now_utc.strftime("%m/%d %H:%M UTC")
    msg = (
        "📊 BTC 세력 지도 브리핑 (" + time_str + ")\n"
        "만기: " + expiry + "\n"
        "--------------\n"
        "현재가: $" + f"{current_price:,.0f}" + "\n"
        "--------------\n"
        "목적지\n"
        "Put Wall: $" + f"{put_wall:,}" + "\n"
        "Max Pain: $" + f"{max_pain:,}" + "\n"
        "Call Wall: $" + f"{call_wall:,}" + "\n"
        "--------------\n"
        "현재가는 Max Pain($" + f"{max_pain:,}" + ")보다 " + position + "에 있음"
    )
    result = send_telegram(msg)
    print("전송 완료:", result)

if __name__ == "__main__":
    send_briefing()
