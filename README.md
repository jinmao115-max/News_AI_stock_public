# 株価 × 経済指標ダッシュボード

アメリカ(S&P500)と日本(日経225)の株価に、**経済成長率・インフレ率・雇用者数・政策金利**の
4指標を重ねて、関連性と「指標が動いてから株価が動くまでの時間差」を見える化するページです。

- データ出典: [FRED](https://fred.stlouisfed.org/)（米セントルイス連銀）
- 時間差は相互相関（クロスコリレーション）で自動計算
- GitHub Actions が定期的に最新データを取得し `index.html` を自動更新

## しくみ

```
GitHub Actions（毎週 + 手動）
   │ build.py がFREDから実データ取得
   ▼
index.html を生成し直してコミット
   │
   ▼
GitHub Pages で公開（設定している場合）
```

## ファイル

| ファイル | 役割 |
|---|---|
| `build.py` | FREDからデータ取得→時間差計算→`index.html`生成 |
| `index.html` | 生成される表示用ページ（Actionsが自動更新） |
| `.github/workflows/update.yml` | 自動更新のワークフロー |

## FRED APIキー（任意）

キーが無くても動きます（登録不要のCSVで同じデータを取得）。
公式APIを使いたい場合は、リポジトリの **Settings > Secrets and variables > Actions** に
`FRED_API_KEY` という名前でキーを登録してください。あれば自動でAPIを使います。

## 手動で動かす

リポジトリの **Actions** タブ →「Update stock dashboard」→「Run workflow」。

## ローカルで動かす

```bash
python build.py   # index.html が生成される
```
