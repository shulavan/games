# Edogawa iCal v3.2.13 修正版

## 変更点
- 記事URLの /YYYY/ を年の根拠として取得し、年なし開催日を古い記事年に固定。
- 記事本文に開催年が明記されている場合は、その開催年を優先。
- 詳細記事で本文/URL/公開情報から年を確定できない月日は採用しない。
- 明示的な毎日開催や休館日除外がない単なる開催期間は、日別VEVENTへ分割せず期間1件で保持。
- 明示された毎日開催、毎週/第N曜日、休館日/休業日除外は従来どおり処理。
- 汎用 parse_date_spans の従来互換は維持。詳細記事の抽出では require_year=True を使用し、根拠のない未来日付を防止。

## 適用
ZIP内の edogawa_ical.py をリポジトリ直下へ上書きし、.github/workflows/update_ical_v3_2_12.yml の中身を既存Workflowへ反映してください。
tests/test_date_regressions_v3_2_13.py を既存 tests/ へ追加してください。
Workflowファイル名は既存のままにし、別Workflowを増やさないでください。

## テスト
このZIP内の追加回帰テスト7件は成功。Python環境での既存テスト全体（GitHub Actionsの157件）は未実行のため、GitHub Actionsで再確認してください。

既存テストの更新が必要です。詳細は TEST_MIGRATION_v3_2_13.txt を参照してください。特に APP_VERSION の期待値と、通常の複数日期間を日別5件に展開する旧テストは新仕様に合わせて更新します。明示的な毎日開催・休館日除外のテストは維持します。
