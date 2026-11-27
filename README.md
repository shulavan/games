# 江戸川区球場 Gemini URL Context 試験版

この試験版は、江戸川区球場の公式「大会情報」ページだけを対象に、対象月の記事を探し、記事内の画像/PDF URLを収集してGemini URL Contextに渡し、月間予定表をJSON化します。既存の `edogawa_ical.py` やGitHub Actionsは変更しません。

対象ページ: https://www.edogawa-3field.jp/baseball/taikaiguid/

## 無料枠での利用方針

- Gemini Developer APIの無料枠のみを使用します。Google AI Studioで無料枠のAPIキーを発行し、Cloud Billingをリンクしないでください。
- モデルは `gemini-3.5-flash-lite` を初期値とします。
- Google Search grounding、Document AI、Vision API、課金プランへの切替は行いません。
- 無料枠の利用可能モデル・RPM/RPD/TPMはGoogle側で変更されるため、実行前にAI Studioの利用枠を確認してください。無料枠の上限に達した場合はエラーで停止し、有料枠へ自動移行しません。
- 無料枠では入力データがGoogleの製品改善に使われる場合があります。対象は公開済みの球場予定表に限定しています。

## ローカル実行

```bash
pip install -r requirements-gemini.txt
export GEMINI_API_KEY="Google AI Studioで発行したAPIキー"
python edogawa_stadium_gemini.py --year 2026 --month 11
```

APIを呼ばず、URLの発見だけ確認する場合:

```bash
python edogawa_stadium_gemini.py --year 2026 --month 11 --discover-only
```

成功すると `edogawa_stadium_events.json` が生成されます。日付・時刻・重複・対象月をPythonで検証し、エラーがある場合は終了コード2にします。JSONを既存iCalへ取り込む処理は、試験精度を確認してから別途実装します。

## GitHub Actions

1. この試験版のファイルをリポジトリ直下へ配置します。
2. GitHub Secretsに `GEMINI_API_KEY` を登録します。
3. Actionsの `Edogawa Stadium Gemini Pilot` を手動実行するか、毎月5日の自動実行を待ちます。
4. 実行結果の `edogawa-stadium-events` ArtifactからJSONを取得します。

月次cronはUTC 02:17、すなわち日本時間11:17です。

## 制約と安全策

- URL Contextは指定したURLだけを取得し、リンク先を自動巡回しません。このスクリプトはPythonで公式一覧ページ→対象月の記事ページを取得し、そこに埋め込まれた画像/PDFの直接URLを抽出してGeminiへ明示的に渡します。
- 画像/PDFが取得できない、記事が見つからない、URLが20件を超える場合は失敗扱いにします。
- 時刻が画像に書かれていなければ `null` のままにします。
- 既存iCalへの自動追加はしません。まずJSONを人間が確認します。
- APIキーをコードやログに出力しません。

## テスト

```bash
python -m unittest discover -s tests -v
```
