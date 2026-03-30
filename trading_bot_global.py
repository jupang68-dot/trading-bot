import yfinance as yf
import requests
import pandas as pd
import os, csv, time, json

# =============================
# CONFIG
# =============================
TICKERS = [
"D05.SI","O39.SI","U11.SI","C6L.SI","U96.SI",
"AAPL","NVDA","MSFT","TSLA","AMZN","AMD",
"GLD","SLV","IBIT",
"BTC-USD","ETH-USD","ADA-USD","XRP-USD","SOL-USD"
]

DESC = {
"D05.SI":"DBS","O39.SI":"OCBC","U11.SI":"UOB",
"C6L.SI":"SIA","U96.SI":"Sembcorp",
"AAPL":"Apple","NVDA":"NVIDIA","MSFT":"Microsoft",
"TSLA":"Tesla","AMZN":"Amazon","AMD":"AMD",
"GLD":"Gold ETF","SLV":"Silver ETF","IBIT":"Bitcoin ETF",
"BTC-USD":"Bitcoin","ETH-USD":"Ethereum","ADA-USD":"Cardano",
"XRP-USD":"XRP","SOL-USD":"Solana"
}

TOKEN="8215343725:AAFkflNUxWxi5_-2fmZfpSjOJ6XUdw3Hl8I"
CHAT_ID="175022374"
INTERVAL = 900

# =============================
# RISK SETTINGS
# =============================
STARTING_CAPITAL = 1000
RISK_PER_TRADE = 0.01
MAX_OPEN_TRADES = 3
MAX_PORTFOLIO_RISK = 0.05

FEE_PER_TRADE = 2.5
MIN_TRADE_USD = 50

# =============================
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id":CHAT_ID,"text":msg}
        )
    except:
        pass

# =============================
def rsi(df,n=14):
    d=df["Close"].diff()
    g=d.clip(lower=0).rolling(n).mean()
    l=(-d.clip(upper=0)).rolling(n).mean()
    rs=g/l
    return 100-(100/(1+rs))

def atr(df,n=14):
    tr = pd.concat([
        df["High"]-df["Low"],
        (df["High"]-df["Close"].shift()).abs(),
        (df["Low"]-df["Close"].shift()).abs()
    ],axis=1).max(axis=1)
    return tr.rolling(n).mean()

# =============================
# MARKET REGIME
# =============================
def get_market_regime():
    try:
        df = yf.Ticker("SPY").history(period="3mo")
        df["MA50"] = df["Close"].rolling(50).mean()
        df["RSI"] = rsi(df)

        x = df.iloc[-1]

        if x["Close"] > x["MA50"] and x["RSI"] > 50:
            return "RISK-ON 🟢"
        elif x["Close"] < x["MA50"] and x["RSI"] < 50:
            return "RISK-OFF 🔴"
        else:
            return "NEUTRAL 🟡"
    except:
        return "UNKNOWN"

# =============================
def analyze(ticker, regime):

    df = yf.Ticker(ticker).history(period="1mo", interval="1h").dropna()
    if len(df) < 30:
        return None

    df["MA20"]=df["Close"].rolling(20).mean()
    df["MA50"]=df["Close"].rolling(50).mean()
    df["RSI"]=rsi(df)
    df["ATR"]=atr(df)
    df["VOL_AVG"]=df["Volume"].rolling(20).mean()
    df = df.dropna()

    x = df.iloc[-1]

    # =============================
    # LONG BREAKOUT
    # =============================
    if regime in ["RISK-ON 🟢","NEUTRAL 🟡"]:
        recent_high = df["High"].rolling(12).max().iloc[-2]

        if (
            x["Close"] > x["MA20"] > x["MA50"]
            and x["RSI"] > 52
            and x["Close"] > recent_high
            and x["Volume"] > 1.3 * x["VOL_AVG"]
        ):
            return {"side":"LONG","type":"BREAKOUT","price":x["Close"],"atr":x["ATR"]}

    # =============================
    # SHORT BREAKDOWN
    # =============================
    if regime == "RISK-OFF 🔴":
        recent_low = df["Low"].rolling(10).min().iloc[-2]

        if (
            x["Close"] < x["MA20"] < x["MA50"]
            and x["RSI"] < 48
            and x["Close"] < recent_low
            and x["Volume"] > 1.3 * x["VOL_AVG"]
        ):
            return {"side":"SHORT","type":"BREAKDOWN","price":x["Close"],"atr":x["ATR"]}

    # =============================
    # SHORT PULLBACK (ZONE)
    # =============================
    if regime == "RISK-OFF 🔴":

        dist_to_ma20 = abs(x["Close"] - x["MA20"]) / x["MA20"]

        if (
            x["Close"] < x["MA50"]
            and dist_to_ma20 < 0.02
            and 40 < x["RSI"] < 55
        ):
            return {"side":"SHORT","type":"PULLBACK","price":x["Close"],"atr":x["ATR"]}

    return None

# =============================
def get_equity():
    if not os.path.exists("equity.json"):
        return STARTING_CAPITAL
    return json.load(open("equity.json"))["capital"]

def log_trade(ticker, side, ttype, entry, tp, sl, qty, capital, risk):
    exists=os.path.exists("trades.csv")
    with open("trades.csv","a",newline="") as f:
        w=csv.writer(f)
        if not exists:
            w.writerow(["Ticker","Side","Type","Entry","TP","SL","Qty","Capital","Risk"])
        w.writerow([ticker,side,ttype,entry,tp,sl,qty,capital,risk])

# =============================
def run_once():
    print("\n⏳",time.strftime("%H:%M:%S"))

    regime = get_market_regime()
    print("📊 Market:", regime)

    equity=get_equity()
    risk_amt=equity*RISK_PER_TRADE

    open_df=pd.read_csv("trades.csv") if os.path.exists("trades.csv") else pd.DataFrame()
    open_set=set(open_df["Ticker"]) if not open_df.empty else set()

    msg=f"📊 Market: {regime}\n\n"

    for t in TICKERS:
        try:
            sig = analyze(t, regime)
            if not sig:
                continue

            if t in open_set or len(open_set)>=MAX_OPEN_TRADES:
                continue

            entry=sig["price"]
            atr_val=sig["atr"]

            if sig["side"] == "LONG":
                sl=entry-atr_val
                tp=entry+2*atr_val
            else:
                sl=entry+atr_val
                tp=entry-2*atr_val

            qty=risk_amt/(abs(entry-sl))
            trade_val=qty*entry

            # ✅ POSITION SIZE CAP (NEW)
            max_position = equity * 0.2
            if trade_val > max_position:
                trade_val = max_position
                qty = trade_val / entry

            if trade_val < MIN_TRADE_USD:
                continue

            log_trade(t,sig["side"],sig["type"],entry,tp,sl,qty,trade_val,risk_amt)
            open_set.add(t)

            name=DESC.get(t,"")
            msg+=f"{sig['side']} {sig['type']} 🚀 {t} ({name}) | ${round(trade_val,2)}\n"

        except:
            continue

    if msg.strip() != f"📊 Market: {regime}":
        send(msg)
        print(msg)
    else:
        print("😴 No trades")

# =============================
print("🚀 FINAL BOT (RISK CONTROLLED) STARTED")

while True:
    try:
        run_once()
        time.sleep(INTERVAL)
    except Exception as e:
        print("Error:",e)
        time.sleep(60)