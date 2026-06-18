# -*- coding: utf-8 -*-
"""
FRED(米セントルイス連銀)から実データを取得し、株価(S&P500/日経225)と
4指標(経済成長率/インフレ率/雇用者数/政策金利)を月次にそろえ、
相互相関で「指標→株価」の時間差を自動計算し、自己完結の index.html を生成する。

データ取得は curl 経由（社内プロキシ/CI どちらでも動く）。
環境変数 FRED_API_KEY があれば公式APIを、無ければ登録不要のCSVを使う（データは同じ）。
"""
import os
import json
import subprocess
import datetime as dt
from collections import OrderedDict

API_KEY = os.environ.get("FRED_API_KEY", "").strip()
COSD = "1990-01-01"
COED = "2026-12-31"

SERIES = {
    "US": {
        "name": "アメリカ", "stockName": "S&P500", "flag": "🇺🇸",
        "defs": {
            "stock": ("SP500", "daily"),
            "rate":  ("FEDFUNDS", "monthly"),
            "cpi":   ("CPIAUCSL", "cpi_index"),
            "gdp":   ("A191RL1Q225SBEA", "quarterly"),
            "emp":   ("PAYEMS", "level"),
        },
    },
    "JP": {
        "name": "日本", "stockName": "日経平均225", "flag": "🇯🇵",
        "defs": {
            "stock": ("NIKKEI225", "daily"),
            "rate":  ("IRSTCI01JPM156N", "monthly"),
            "cpi":   ("JPNCPIALLMINMEI", "jp_cpi_splice"),
            "gdp":   ("JPNRGDPEXP", "gdp_index_q"),
            "emp":   ("LFEMTTTTJPM647S", "level"),
        },
    },
}

LABELS = {"rate": "政策金利 (%)", "cpi": "インフレ率 前年比(%)",
          "gdp": "経済成長率 (%)", "emp": "雇用者数", "stock": "株価"}

def curl(url):
    r = subprocess.run(["curl", "-s", "--max-time", "70", url],
                       capture_output=True, text=True, encoding="utf-8")
    return r.stdout

def fetch(series_id):
    """(date, value) のリストを返す。APIキーがあれば公式API、無ければCSV。"""
    if API_KEY:
        url = ("https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={API_KEY}&file_type=json"
               f"&observation_start={COSD}&observation_end={COED}")
        try:
            obs = json.loads(curl(url)).get("observations", [])
            rows = []
            for o in obs:
                v = o.get("value", ".")
                if v not in (".", "", "NA"):
                    rows.append((o["date"], float(v)))
            if rows:
                return rows
        except Exception:
            pass  # APIが失敗したらCSVにフォールバック
    text = curl("https://fred.stlouisfed.org/graph/fredgraph.csv"
                f"?id={series_id}&cosd={COSD}&coed={COED}")
    rows = []
    for i, line in enumerate(text.splitlines()):
        if i == 0:
            continue
        p = line.split(",")
        if len(p) < 2 or p[1] in (".", "", "NA"):
            continue
        try:
            rows.append((p[0], float(p[1])))
        except ValueError:
            continue
    return rows

def mkey(d):
    y, m, _ = d.split("-")
    return f"{y}-{m}"

def month_avg(rows):
    b = OrderedDict()
    for d, v in rows:
        b.setdefault(mkey(d), []).append(v)
    return OrderedDict((k, sum(vs) / len(vs)) for k, vs in b.items())

def ffill_quarterly(rows):
    m = OrderedDict()
    for d, v in rows:
        y, mo, _ = d.split("-")
        for off in range(3):
            mm, yy = int(mo) + off, int(y)
            if mm > 12:
                mm -= 12; yy += 1
            m[f"{yy}-{mm:02d}"] = v
    return m

def yoy_index(mm):
    out = OrderedDict()
    for k in mm:
        y, m = k.split("-")
        prev = f"{int(y)-1}-{m}"
        if prev in mm and mm[prev] != 0:
            out[k] = (mm[k] / mm[prev] - 1.0) * 100.0
    return out

# 日本の月次CPIはFREDで2021年6月に打ち切られたため、
# 月次(OECD,〜2021/6)＋年次(世界銀行,2021/7以降)を接ぎ木して直近まで延ばす。
WB_JP_CPI = "FPCPITOTLZGJPN"  # 世界銀行: 日本のインフレ率(年次・前年比%)

def jp_cpi_merged():
    oecd = yoy_index(month_avg(fetch("JPNCPIALLMINMEI")))  # 月次 前年比% (〜2021/6)
    out = OrderedDict(oecd)
    last = list(oecd)[-1] if oecd else "2021-06"           # 'YYYY-MM'
    for d, v in fetch(WB_JP_CPI):                           # 年次 前年比%
        yr = d.split("-")[0]
        for m in range(1, 13):
            k = f"{yr}-{m:02d}"
            if k > last:                                    # OECD月次の後ろだけ年次で補う
                out[k] = v
    return OrderedDict(sorted(out.items()))

def yoy_quarter_index(rows):
    vals = OrderedDict((mkey(d), v) for d, v in rows)
    keys = list(vals.keys())
    g = []
    for i, k in enumerate(keys):
        if i >= 4 and vals[keys[i-4]] != 0:
            g.append((k + "-01", (vals[k] / vals[keys[i-4]] - 1.0) * 100.0))
    return ffill_quarterly(g)

def process(sid, kind):
    rows = fetch(sid)
    if not rows:
        return OrderedDict()
    if kind in ("daily", "monthly", "level"):
        return month_avg(rows)
    if kind == "cpi_index":
        return yoy_index(month_avg(rows))
    if kind == "quarterly":
        return ffill_quarterly(rows)
    if kind == "gdp_index_q":
        return yoy_quarter_index(rows)
    if kind == "jp_cpi_splice":
        return jp_cpi_merged()
    return OrderedDict()

def month_add(k, n):
    y, m = map(int, k.split("-"))
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12}-{idx % 12 + 1:02d}"

def pearson(xs, ys):
    n = len(xs)
    if n < 24:
        return 0.0
    mx, my = sum(xs)/n, sum(ys)/n
    num = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    dx = sum((x-mx)**2 for x in xs) ** 0.5
    dy = sum((y-my)**2 for y in ys) ** 0.5
    return num/(dx*dy) if dx and dy else 0.0

def stock_yoy(sm):
    out = OrderedDict()
    for k in sm:
        prev = month_add(k, -12)
        if prev in sm and sm[prev] != 0:
            out[k] = (sm[k]/sm[prev] - 1.0) * 100.0
    return out

MAX_LAG = 24

def best_lag(syoy, ind):
    best = {"lag": None, "corr": 0.0, "n": 0}
    for lag in range(0, MAX_LAG + 1):
        xs, ys = [], []
        for k in ind:
            sk = month_add(k, lag)
            if sk in syoy:
                xs.append(ind[k]); ys.append(syoy[sk])
        if len(xs) >= 24:
            c = pearson(xs, ys)
            if abs(c) > abs(best["corr"]):
                best = {"lag": lag, "corr": round(c, 3), "n": len(xs)}
    return best

def build():
    out = {"countries": {}, "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "source": "FRED API" if API_KEY else "FRED CSV(キー無し)"}
    for c, cfg in SERIES.items():
        print(f"== {c} ==")
        data, kinds = {}, {}
        for key, (sid, kind) in cfg["defs"].items():
            data[key] = process(sid, kind)
            kinds[key] = kind
            n = len(data[key])
            span = f"{list(data[key])[0]}〜{list(data[key])[-1]}" if n else "なし"
            print(f"  {key:5s} {sid:18s} {n}点 {span}")
        series_out = {k: [{"t": t, "v": round(v, 4)} for t, v in data[k].items()]
                      for k in ["stock", "rate", "cpi", "gdp", "emp"]}
        syoy = stock_yoy(data["stock"])
        lags = {}
        for key in ["gdp", "cpi", "emp", "rate"]:
            ind = yoy_index(data[key]) if kinds[key] == "level" else data[key]
            lags[key] = best_lag(syoy, ind) if ind else {"lag": None, "corr": 0.0, "n": 0}
            print(f"  lag[{key}] = {lags[key]}")
        out["countries"][c] = {"meta": {"name": cfg["name"], "stockName": cfg["stockName"],
                               "flag": cfg["flag"]}, "series": series_out, "lags": lags}
    return out

def render_html(data):
    blob = json.dumps(data, ensure_ascii=False)
    tpl = HTML_TEMPLATE.replace("/*__DATA__*/null", blob)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(tpl)
    print("\nindex.html を生成しました。")

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>株価×経済指標ダッシュボード（実データ/FRED・自動更新）</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root{--bg:#0f1623;--card:#1a2436;--card2:#222f45;--text:#e8eef7;--sub:#9fb0c8;
        --stock:#4ea1ff;--rate:#ff6b6b;--cpi:#ffd166;--gdp:#51d88a;--emp:#b794f6;--line:#2c3a52;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);line-height:1.7;
       font-family:"Segoe UI","Hiragino Kaku Gothic ProN","Meiryo",sans-serif}
  .wrap{max-width:1080px;margin:0 auto;padding:26px 18px 60px}
  h1{font-size:1.5rem;margin:0 0 6px}
  .lead{color:var(--sub);margin:0 0 14px;font-size:.92rem}
  .meta{background:#14233b;border:1px solid var(--line);border-radius:10px;
        padding:10px 14px;font-size:.82rem;color:var(--sub);margin-bottom:24px}
  h2{font-size:1.15rem;margin:34px 0 12px;border-left:5px solid var(--stock);padding-left:11px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px 16px}
  .card .t{font-weight:700;margin-bottom:4px}
  .card .lag{font-size:1.15rem;font-weight:800}
  .card .corr{color:var(--sub);font-size:.8rem;margin-top:5px}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:16px;margin-top:14px}
  .ph{display:flex;flex-wrap:wrap;gap:10px;justify-content:space-between;align-items:center;margin-bottom:8px}
  .ph .nm{font-weight:700}
  select{background:var(--card2);color:var(--text);border:1px solid var(--line);
         border-radius:8px;padding:6px 10px;font-size:.88rem}
  .box{position:relative;height:330px}
  .hint{color:var(--sub);font-size:.78rem;margin-top:7px}
  table{width:100%;border-collapse:collapse;margin-top:8px;font-size:.85rem}
  th,td{text-align:left;padding:9px 11px;border-bottom:1px solid var(--line)}
  th{color:var(--sub);font-weight:600}
  footer{color:var(--sub);font-size:.78rem;margin-top:36px;text-align:center}
  .src{display:inline-block;background:rgba(78,161,255,.15);color:var(--stock);
       padding:2px 9px;border-radius:999px;font-size:.75rem;font-weight:700}
</style>
</head>
<body>
<div class="wrap">
  <h1>📈 株価 × 経済指標ダッシュボード</h1>
  <p class="lead">アメリカ(S&amp;P500)と日本(日経225)の株価に、経済成長率・インフレ率・雇用者数・政策金利を重ね、過去データから関連性と時間差を見える化します。</p>
  <div class="meta" id="meta"></div>
  <div id="content"></div>
  <footer>
    データ出典: FRED (Federal Reserve Bank of St. Louis)。本ページは自動生成です（投資助言ではありません）。
  </footer>
</div>
<script>
const DATA = /*__DATA__*/null;
const KEYS = ["rate","cpi","gdp","emp"];
const KLABEL = {rate:"政策金利 (%)",cpi:"インフレ率 前年比(%)",gdp:"経済成長率 (%)",emp:"雇用者数(前年比%換算)"};
const KCOLOR = {stock:"#4ea1ff",rate:"#ff6b6b",cpi:"#ffd166",gdp:"#51d88a",emp:"#b794f6"};
const css=getComputedStyle(document.documentElement);
const SUB=css.getPropertyValue('--sub').trim(), LINE=css.getPropertyValue('--line').trim();

document.getElementById('meta').innerHTML =
  `🔄 最終更新: <b>${DATA.generated}</b> ／ データ取得元: <span class="src">${DATA.source}</span> ／ 時間差は「指標が動いてから株価(前年比)と最も連動するまでの月数」を相互相関で自動計算。`;

function toMap(arr){const m={};arr.forEach(o=>m[o.t]=o.v);return m;}
function allMonths(series){
  const s=new Set();
  ["stock",...KEYS].forEach(k=>series[k].forEach(o=>s.add(o.t)));
  return [...s].sort();
}
function alignNorm(arr,labels){ // min-max正規化(0-100)、欠損はnull
  const m=toMap(arr);
  const vals=labels.map(l=>l in m?m[l]:null).filter(v=>v!=null);
  const mn=Math.min(...vals),mx=Math.max(...vals),rng=(mx-mn)||1;
  return labels.map(l=>l in m?Math.round((m[l]-mn)/rng*1000)/10:null);
}
function alignRaw(arr,labels){const m=toMap(arr);return labels.map(l=>l in m?m[l]:null);}

const tick={color:SUB}, grid={color:LINE};

function lagText(lg){
  if(lg.lag==null) return "データ不足";
  const dir = lg.corr>=0 ? "同じ向き" : "逆の向き";
  return `${lg.lag}ヶ月後`;
}

const content=document.getElementById('content');

Object.keys(DATA.countries).forEach(cc=>{
  const C=DATA.countries[cc], M=C.meta, S=C.series, L=C.lags;
  const labels=allMonths(S);

  const sec=document.createElement('div');
  sec.innerHTML=`
    <h2>${M.flag} ${M.name}：${M.stockName} と 4指標</h2>
    <div class="cards">
      ${KEYS.map(k=>`<div class="card" style="border-left:4px solid ${KCOLOR[k]}">
        <div class="t">${({rate:'🏦 政策金利',cpi:'🔥 インフレ率',gdp:'📊 経済成長率',emp:'👷 雇用者数'})[k]}</div>
        <div class="lag" style="color:${KCOLOR[k]}">${lagText(L[k])}</div>
        <div class="corr">相関 r=${L[k].corr??'-'}（${L[k].corr>=0?'同じ向き':'逆の向き'}に連動）</div>
      </div>`).join('')}
    </div>
    <div class="panel">
      <div class="ph"><div class="nm">① 4指標＋株価を重ねる（形をそろえるため0〜100に正規化）</div></div>
      <div class="box"><canvas id="ov_${cc}"></canvas></div>
      <div class="hint">凡例をクリックで表示/非表示。山と谷の「ズレ」が時間差のヒントです。正規化しているので高さの絶対値ではなく“動きの形”を見てください。</div>
    </div>
    <div class="panel">
      <div class="ph"><div class="nm">② 株価×1指標（実数で精密に）</div>
        <select id="sel_${cc}">${KEYS.map(k=>`<option value="${k}">${KLABEL[k]}</option>`).join('')}</select>
      </div>
      <div class="box"><canvas id="du_${cc}"></canvas></div>
      <div class="hint">右上で指標を切替。左軸=株価、右軸=指標の実数値です。</div>
    </div>`;
  content.appendChild(sec);

  // ① オーバーレイ（正規化）
  new Chart(document.getElementById('ov_'+cc),{
    type:'line',
    data:{labels,datasets:[
      {label:M.stockName,data:alignNorm(S.stock,labels),borderColor:KCOLOR.stock,borderWidth:2.6,pointRadius:0,tension:.25,spanGaps:true},
      ...KEYS.map(k=>({label:KLABEL[k],data:alignNorm(S[k],labels),borderColor:KCOLOR[k],borderWidth:1.8,pointRadius:0,tension:.25,spanGaps:true,borderDash:[5,3]}))
    ]},
    options:{responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{legend:{labels:{color:SUB,boxWidth:14}}},
      scales:{x:{grid,ticks:{...tick,maxTicksLimit:12}},y:{grid,ticks:tick,title:{display:true,text:'正規化 0-100',color:SUB}}}}
  });

  // ② デュアル軸（実数）
  let du;
  function drawDual(k){
    if(du) du.destroy();
    du=new Chart(document.getElementById('du_'+cc),{
      type:'line',
      data:{labels,datasets:[
        {label:M.stockName,data:alignRaw(S.stock,labels),borderColor:KCOLOR.stock,yAxisID:'yS',borderWidth:2.4,pointRadius:0,tension:.25,spanGaps:true},
        {label:KLABEL[k],data:alignRaw(S[k],labels),borderColor:KCOLOR[k],yAxisID:'yI',borderWidth:2.4,pointRadius:0,tension:.25,spanGaps:true,borderDash:[5,3]}
      ]},
      options:{responsive:true,maintainAspectRatio:false,
        interaction:{mode:'index',intersect:false},
        plugins:{legend:{labels:{color:SUB,boxWidth:14}}},
        scales:{x:{grid,ticks:{...tick,maxTicksLimit:12}},
          yS:{position:'left',grid,ticks:{color:KCOLOR.stock}},
          yI:{position:'right',grid:{drawOnChartArea:false},ticks:{color:KCOLOR[k]}}}}
    });
  }
  drawDual('rate');
  document.getElementById('sel_'+cc).addEventListener('change',e=>drawDual(e.target.value));
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    render_html(build())
