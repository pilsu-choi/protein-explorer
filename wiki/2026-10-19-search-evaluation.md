---
type: Evaluation
title: ESM-2 검색 평가와 기준선 비교
description: 1,000개 코퍼스의 sanity check와 UniProt 그룹 일치 검색을 3-mer Jaccard 기준선과 비교합니다.
tags: [evaluation, search, esm2, baseline, quality]
status: stable
generated:
  by: codex/gpt-6
  at: 2026-10-19T13:14:48Z
sources:
  - id: results
    resource: ../docs/validation/2026-10-19-results.json
    title: 날짜가 기록된 검색 및 예측 실측 결과
  - id: evaluator
    resource: ../scripts/evaluate_search.py
    title: 코퍼스 검색 평가 스크립트
  - id: prd
    resource: ../PRD.md
    title: Protein Explorer 검색 평가 범위
  - id: esm
    resource: https://github.com/facebookresearch/esm
    title: Meta FAIR ESM 저장소
---

# 평가 데이터와 결과

이 평가는 1,000개 단백질 코퍼스에서 고정된 ESM-2 임베딩 검색과 단순 3-mer Jaccard 검색을 비교합니다. 실제 수치와 평가 조건은 Git에 포함된 [JSON 결과](../docs/validation/2026-10-19-results.json)에 보존합니다.

## Self-search sanity check

1,000개 코퍼스 레코드 각각을 query로 검색했을 때 자기 자신이 top 10에 포함된 결과는 1,000/1,000이었습니다. 이는 벡터와 인덱스 연결, 검색 경로가 동작함을 확인합니다. 자기 자신이 query와 corpus에 동시에 포함되므로 품질 점수로 사용하지 않습니다.

## 같은 UniProt 그룹 평가

107개 반복 accession 그룹에서 선택한 query 256개마다 자기 레코드를 제외하고 같은 accession을 가진 다른 레코드가 top 10에 검색되는지 셌습니다.

| 방법 | Recall@10 | HitRate@10 |
|---|---:|---:|
| ESM-2 8M cosine | 90.756448% | 96.09375% |
| 3-mer Jaccard | 94.237351% | 99.21875% |

각 방법에서 가장 높은 점수 순으로 결과를 정렬하고, query 자체만 제외했습니다. 표의 Recall@10은 query별로 검색된 관련 레코드 수를 합산한 micro-average 비율이며 HitRate@10은 관련 레코드가 하나 이상 나온 query의 비율입니다. 단순 기준선이 두 지표 모두 ESM-2보다 높은 결과입니다.

이 레이블은 같은 UniProt accession을 공유한다는 사실만 평가합니다. 서열 계열을 분리한 train/test split이 아니므로 held-out family 일반화를 측정하지 않습니다. 그룹 크기, 유사 중복 서열, 코퍼스 구성에 민감하며 기능 동등성도 검증하지 않습니다. ESM-2 검색 구현과 초기 기준선 비교는 완료됐고, family-held-out 평가와 후속 모델 선택이 남아 있습니다.

## 다음 평가

평가 split을 단백질 family 기준으로 고정하고, query와 평가 코퍼스 사이의 중복을 제거한 뒤 기존 ESM-2 및 기준선과 같은 지표를 비교해야 합니다. 새 방법이나 데이터로 다시 실행할 때 기존 결과 JSON을 덮어쓰지 말고 새 날짜 기록을 추가합니다. 검색 점수는 기능 또는 구조 유사도 확률로 표시하지 않습니다.

관련: [MVP 튜토리얼](2026-10-19-mvp-tutorial.md), [PRD](../PRD.md), [평가 데이터](../docs/validation/2026-10-19-results.json).
