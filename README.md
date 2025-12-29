# tsavs_tot.py

AVSファイルのTrim範囲のTOT（Time Offset Table）時刻を表示するツール。

## 概要

AVSファイルで指定されたTrim範囲（フレーム番号）または動画全編に対応する実際の放送時刻（TOT）を取得します。トリムした動画セグメントの正確な放送時刻を確認するのに便利です。

## インストール

### 1. リポジトリのクローン

```bash
git clone https://github.com/noshiket/tsavs_tot
cd tsavs_tot
```
### 2. 動作確認

```bash
python3 tsavs_cutter.py --help
```

## 必要な環境

- **Python 3.7以上**
- **外部依存なし**（Python標準ライブラリのみ使用）

## 使用方法

```bash
python3 tsavs_tot.py -i INPUT.ts -a TRIM.avs [-o OUTPUT.json]
```

### オプション

- `-i, --input`: 入力TSファイル（必須）
- `-a, --avs`: AVSファイル（Trim指定を含む）（オプション）
- `-o, --output`: 出力JSONファイル（オプション）

## 使用例

### 1. コンソール出力

```bash
python3 tsavs_tot.py -i test.ts -a my_trim.avs
```

出力例：
```
Parsing AVS file: my_trim.avs
Found 4 trim ranges

Analyzing video stream...
  Video PID: 0x100
  Total frames: 53657

Analyzing TOT timestamps...

Trim segment 1: frames [193, 3188]
  Start TOT: 2025-12-13 19:00:05 JST
  End TOT:   2025-12-13 19:01:45 JST
  Duration:  100 seconds

Trim segment 2: frames [4988, 22040]
  Start TOT: 2025-12-13 19:02:45 JST
  End TOT:   2025-12-13 19:12:15 JST
  Duration:  570 seconds

Trim segment 3: frames [23839, 46345]
  Start TOT: 2025-12-13 19:13:15 JST
  End TOT:   2025-12-13 19:25:45 JST
  Duration:  750 seconds

Trim segment 4: frames [48145, 48743]
  Start TOT: 2025-12-13 19:26:45 JST
  End TOT:   2025-12-13 19:27:05 JST
  Duration:  20 seconds
```

### 2. JSON出力

```bash
python3 tsavs_tot.py -i test.ts -a my_trim.avs -o output.json
```

JSON出力例：
```json
{
  "input_file": "test.ts",
  "avs_file": "my_trim.avs",
  "segments": [
    {
      "index": 1,
      "frames": [193, 3188],
      "start_tot": "2025-12-13 19:00:05",
      "end_tot": "2025-12-13 19:01:45",
      "duration_sec": 100
    },
    {
      "index": 2,
      "frames": [4988, 22040],
      "start_tot": "2025-12-13 19:02:45",
      "end_tot": "2025-12-13 19:12:15",
      "duration_sec": 570
    }
  ]
}
```

## 動作の仕組み

### 1. AVSファイルの解析
- AVSファイルから`Trim(start, end)`を抽出
- フレーム番号のリストを取得

### 2. ビデオインデックスの構築
- TSファイルをスキャンしてビデオPIDを検出
- 各ビデオフレームのPTS（Presentation Time Stamp）を記録

### 3. TOT検索
- 各Trim範囲の開始・終了フレームのPTSを取得
- そのPTS付近のTOT（Time Offset Table）パケットを検索
- TOTから正確な放送時刻（JST）を抽出

## 技術詳細

### TOT（Time Offset Table）

- **PID**: 0x14
- **Table ID**: 0x73
- **内容**: MJD（Modified Julian Date）+ BCD形式の時刻（ARIB規格ではJSTとして記録）
- **変換**: MJD + BCD時刻 → datetime（JST）、UTC変換は不要

### MJD to Date変換

ETSI EN 300 468 Annex Cに準拠したMJD（Modified Julian Date）から日付への変換を実施します。

### 検索範囲

各フレームのPTS付近で最大50,000パケット範囲内のTOTを検索します。

## 注意点

- フレーム範囲が総フレーム数を超える場合はエラーになります
- TOTが見つからない場合はエラーになります（検索範囲内にTOTパケットが存在しない）
- AVSファイルはUTF-8エンコーディング必須

## エラーハンドリング

### フレーム範囲エラー
```
Error: Frame range out of bounds (total: 53657)
```

### TOT未検出エラー
```
Error: TOT not found near start frame 193
```

## ライセンス
MIT Licenseです。
LICENSEファイルに記載してます。

