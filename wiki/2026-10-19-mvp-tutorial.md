---
type: Tutorial
title: Protein Explorer MVP 처음부터 실행하기
description: Docker로 검색 코퍼스와 화면을 준비하고, 서열 검색·GPU 예측·3D 비교·결과 다운로드를 안내합니다.
tags: [tutorial, docker, search, prediction, 3d]
status: stable
generated:
  by: codex/gpt-6
  at: 2026-10-19T13:14:48Z
sources:
  - id: readme
    resource: ../README.md
    title: Protein Explorer 빠른 시작
  - id: prd
    resource: ../PRD.md
    title: Protein Explorer 제품 요구사항과 평가 기준
  - id: compose
    resource: ../compose.yaml
    title: Docker Compose 서비스 및 프로파일
  - id: rcsb
    resource: https://www.rcsb.org/
    title: RCSB Protein Data Bank
  - id: esm
    resource: https://github.com/facebookresearch/esm
    title: Meta FAIR ESM 저장소
  - id: colabfold
    resource: https://github.com/sokrypton/ColabFold
    title: ColabFold 공식 저장소
---

# Protein Explorer MVP 사용 튜토리얼

2026-10-19 기준 구현된 로컬 MVP를 설치하고 검색, 예측, 3D 확인까지 진행하는 절차입니다. API와 화면은 CPU로 실행하며, 구조 예측에는 별도의 NVIDIA GPU worker가 필요합니다.

## 1. 앱 이미지 빌드와 코퍼스 준비

저장소 루트에서 앱 이미지를 만들고 공개 참조 코퍼스를 준비합니다.

```bash
docker compose build app
docker compose --profile prepare run --rm prepare
```

준비 서비스는 RCSB PDB에서 잔기 30~300개의 실험 단백질 사슬을 찾고, 완전히 같은 서열을 제거해 최대 1,000개를 보관합니다. 고정된 ESM-2 모델을 CPU에서 실행해 코퍼스, 벡터와 인덱스 manifest를 `protein_data` 볼륨에 기록합니다. 최초 실행 시 약 32 MB의 ESM-2 8M 가중치를 `model_cache`에 내려받으며, 수집과 임베딩이 끝날 때까지 명령이 실행됩니다.

API/화면과 GPU 예측 worker를 시작합니다.

```bash
docker compose --profile gpu up -d --build app worker
```

worker 이미지에는 ColabFold와 추론 의존성이 포함되어 크기가 큽니다. Docker가 NVIDIA GPU를 볼 수 있도록 NVIDIA Container Toolkit 등을 구성해야 합니다. http://127.0.0.1:8080 을 엽니다. 포트를 바꾸려면 Compose 실행 전에 `PE_PORT`를 지정합니다. 기본 설정은 호스트 loopback에만 포트를 엽니다.

GPU worker 없이 CPU 검색만 사용하려면 앱만 시작합니다.

```bash
docker compose up -d app
```

화면과 검색은 CPU로 동작하지만, 예측 제출에는 worker가 필요하다는 오류가 표시됩니다. health endpoint http://127.0.0.1:8080/api/health 에서 `corpus.ready`가 true인지 확인하고, worker heartbeat가 도착하면 `worker_available`도 true가 되는지 확인합니다.

## 2. 서열 검색

**Load 1UBQ sample**을 눌러 human ubiquitin 76잔기 서열을 입력하거나, 입력란에 FASTA 하나 또는 원시 아미노산 서열을 붙여넣습니다. 표준 아미노산을 검사하고 잔기 수를 표시합니다. **Search sequence**를 선택합니다. 질의는 고정 revision `c731040fcd8d73dceaa04b0a8e6329b345b0f5df`의 `facebook/esm2_t6_8M_UR50D`로 CPU에서 임베딩합니다. Padding과 special token을 제외한 잔기 토큰 평균을 구하고 320차원 벡터를 L2 정규화한 후 cosine 유사도 순으로 정렬합니다.

결과를 선택하면 기록된 RCSB 실험 체인 구조를 불러옵니다. 구조 파일은 선택 시 요청해 데이터 볼륨에 캐시합니다. 결과 행에서 출처 metadata와 cosine 점수를 확인할 수 있습니다. 네트워크 요청이나 체인을 찾지 못하면 참조 구조를 사용할 수 없다고 표시되며, 검색 목록은 준비한 로컬 인덱스에서 제공합니다.

## 3. 구조 예측

기본 설정은 ColabFold `alphafold2_ptm`, 모델 1개, recycle 3회, `mmseqs2_uniref_env` MSA입니다. 이 MSA 모드는 서열을 ColabFold 공개 서버에 보내므로 제출 전에 **Use the public MSA service** checkbox를 선택해야 합니다. 현재 웹 화면에서 비공개 모드를 전환하는 설정은 제공하지 않습니다. 서열을 공개 서비스에 보낼 수 없다면 웹 화면에서 예측을 제출하지 말고, 기존 CLI의 `--msa-mode single_sequence` 또는 API 설정으로 단일 서열 모드를 지정하세요. 로컬 MSA 데이터베이스 구성은 이 MVP 범위에 없습니다.

API는 SQLite에 queued 작업을 저장합니다. GPU worker는 작업을 한 번에 하나씩 claim하고 heartbeat를 기록하며, 결과 산출물을 데이터 볼륨 아래 보관합니다. 새로고침을 누르거나 최근 작업을 선택해 상태를 확인하세요. worker가 다시 시작되면 실행 도중 남은 작업은 재시도할 수 있는 failed 상태로 복구됩니다. 이전 로그를 살펴본 뒤 다시 제출하세요. 산출물을 검사해 성공한 작업만 완료 처리하거나 캐시에서 재사용합니다.

최초 예측은 약 3.7 GB의 AlphaFold 파라미터를 내려받습니다. 시간은 서열 길이, MSA 응답, 호스트와 설정에 따라 달라집니다. 개발 RTX 4070에서 76, 100, 199, 298잔기 공개 샘플의 예측 시간은 각각 34, 48, 59, 100초였습니다. 개별 측정이며 처리량 보장이 아닙니다. 기록한 피크는 데스크톱 사용량을 포함한 GPU 전체 장치 메모리입니다.

Docker GPU worker 통합 점검에서는 1UBQ 76잔기의 예측이 약 33초에 완료됐고 평균 pLDDT는 95.1999였습니다. PDB의 76개 CA 원자와 76개 pLDDT를 확인했으며, 같은 입력을 다시 제출하면 checksum이 검증된 성공 작업을 캐시에서 가져왔습니다. Docker CPU 검색은 1,000개 코퍼스에서 1UBQ를 cosine 0.99999994로 1위에 반환했습니다. warm localhost API 검색 지연 중앙값은 11.46 ms였습니다. 이는 모델이 이미 메모리에 올라온 상태의 다섯 번 측정이며 브라우저 렌더링 시간은 포함하지 않습니다.

## 4. 결과 보기와 내려받기

예측이 성공하면 위쪽 뷰어에서 구조를 회전하고 확대합니다. 신뢰도 색상과 pLDDT 요약은 모델의 예측 신뢰도를 나타냅니다. 검색 결과를 선택하면 아래쪽 뷰어에 참조 구조가 표시됩니다. **Compare**를 선택하면 비교 화면을 넓힙니다. 현재 viewer는 잔기 대응을 정렬하거나 RMSD/TM-score를 계산하지 않으므로 나란히 놓인 두 구조는 구조 superposition 결과가 아닙니다.

다운로드 control에서 예측 PDB와 보고서를 받을 수 있습니다. API는 score, manifest, 검사된 결과 파일과 실행 metadata를 담은 ZIP bundle도 제공합니다. 결과를 보관하거나 공유할 때 manifest를 구조 파일과 함께 유지하세요.

## 5. 검색 평가 읽기

코퍼스 자체 검색에서는 1,000개 중 1,000개가 자기 자신을 가장 가까운 결과로 반환했습니다. 임베딩/인덱스 파이프라인의 정확 query 검색을 확인하는 sanity check이며, 검색 품질 점수는 아닙니다.

현재 제공된 같은 UniProt 그룹 평가는 107개 그룹, 256개 query로 구성되며 각 query 자기 자신을 제외했습니다. ESM-2 평균 Recall@10은 90.756448%, 3-mer Jaccard 기준선은 94.237351%입니다. ESM-2 HitRate@10은 96.09375%, 기준선은 99.21875%입니다. 이 데이터에서는 단순 기준선이 더 높았습니다. 서열 family를 분리한 평가는 아니므로 보지 못한 계열에 대한 일반화 성능을 보여주지 않습니다. Family-held-out 검색 평가와 후속 모델 선택은 남아 있습니다. 자세한 값은 [검색 평가 기록](2026-10-19-search-evaluation.md)을 참고하세요.

## 6. 점수와 데이터 해석

Cosine 유사도는 서열 임베딩 순위입니다. 기능을 공유할 확률이 아니며 구조 유사도를 계산하지 않습니다. pLDDT는 예측 모델의 국소 구조 신뢰도이며, 기능 주석이나 실험 정확도 점수가 아닙니다. 참조 레코드는 RCSB 실험 구조이며 B-factor를 pLDDT로 읽으면 안 됩니다.

RCSB metadata, 공개 구조와 모델 revision은 변경되거나 사용할 수 없게 될 수 있습니다. 생성한 코퍼스에는 수집 날짜, 가능한 경우 accession, 생물종, 구조 유형, 출처 URL과 라이선스를 저장합니다. 코퍼스와 벡터는 Git 외부 Docker volume에 둡니다.

공개 ColabFold MSA 서버가 사용하는 데이터베이스 버전은 요청자가 고정할 수 없습니다. 실행 설정, 가중치 checksum, 질의와 가능한 MSA metadata는 저장하지만 원격 MSA 요청의 bitwise 재현성을 보장할 수 없습니다. Docker worker는 upstream ColabFold CUDA 1.6.2 이미지의 고정 digest에서 시작해 ColabFold 패키지 1.6.3을 설치합니다. 독립 호스트 runtime도 ColabFold 1.6.3을 사용하지만 Docker의 JAX는 0.10.2, 독립 runtime은 0.11.2입니다. 두 환경 버전은 각각 따로 기록합니다.

## 평가와 실측 다시 실행하기

이 단계는 이미지 준비 후 앱과 worker가 실행 중일 때 선택적으로 진행합니다. 같은 평가 기준의 결과를 대체하지 말고 새 실행 결과를 별도 파일로 저장하세요.

코퍼스에 대한 self-retrieval 및 UniProt 그룹 평가와 3-mer 비교를 다시 실행하려면:

```bash
docker compose run --rm app python scripts/evaluate_search.py /data/corpus --limit 1000
```

GPU 예측 시간을 재측정하면 공개 서열이 기본 MSA 사용으로 외부 서비스에 전송됩니다. 작업은 순차 실행되며 동일 입력의 기존 유효 결과가 있으면 worker가 cache hit을 반환할 수 있습니다. 새로운 예측 실측이 필요하면 격리 runtime을 준비하고 별도 파일에 기록하세요. benchmark 스크립트는 API 컨테이너 안에서 실행할 수 있습니다. CPU 앱 컨테이너에는 GPU 장치와 nvidia-smi가 없으므로 GPU 메모리 값은 null이 됩니다. 메모리까지 측정하려면 GPU가 보이는 호스트에서 같은 스크립트를 실행하고 API URL과 복사한 코퍼스 경로를 지정하세요. 초기 요청이 캐시였는지는 initial_cache_hit으로 구분하며, 캐시된 manifest의 예측 시간은 이전 실행의 시간입니다.

```bash
docker compose exec app python scripts/benchmark_predictions.py \
  --url http://127.0.0.1:8000 \
  --corpus /data/corpus \
  --output /data/benchmark.json
```

`mkdir -p .runtime` 후 `docker compose cp app:/data/benchmark.json .runtime/benchmark.json` 명령으로 보고서를 Git 외부 호스트 경로에 복사할 수 있습니다. 지표 그림을 다시 그리려면 `matplotlib`가 설치된 Python 환경에서 저장된 종합 결과를 입력합니다.

```bash
python3 scripts/render_metrics.py docs/validation/2026-10-19-results.json \
  --output docs/assets/validation.png
```

브라우저 점검과 화면 캡처용 `scripts/capture_ui.py`에는 Playwright 브라우저와 실행 중인 로컬 앱이 필요합니다. 현재 캡처 및 검증 결과는 [검증 JSON](../docs/validation/2026-10-19-results.json)에 연결되어 있습니다.

## 문제 해결

- 코퍼스 준비가 실패하면 RCSB 연결과 디스크 여유를 확인하고 prepare profile을 다시 실행합니다. 인덱스는 데이터 volume에 기록됩니다. 앱은 index timestamp가 바뀌면 인덱스를 다시 읽습니다.
- 검색에서 인덱스가 없다고 하면 prepare 작업을 완료하고 `/api/health`에서 `corpus.ready: true`인지 확인합니다.
- 예측이 비활성화되면 GPU profile 실행 여부, worker의 NVIDIA 장치 인식, `worker_available` 값을 확인합니다. RTX 4070 Docker 검증에서는 1UBQ 예측이 성공했고 동일 요청을 캐시에서 재사용했습니다.
- MSA가 실패하면 작업 log와 공개 서비스 응답을 봅니다. 기본 MSA는 서열을 외부 서비스에 보내며 서비스 가용성과 요청 제한의 영향을 받습니다.
- 결과 산출물이 없거나 불완전하면 worker는 성공 대신 실패로 기록합니다. 원인을 해결한 뒤 log를 확인하고 재제출합니다.

2026-10-19 Playwright 브라우저 점검 7개가 페이지 오류 없이 통과했습니다. 공개 MSA 동의 확인, 예측 WebGL 화면, 1,000개 검색 및 실험 참조 구조 표시, 두 구조 비교, 실제 ATOM 레코드를 포함한 PDB 다운로드, 새로고침 후 완료 작업 복원, 잘못된 입력 거부 및 390 px 모바일 가로 넘침을 확인했습니다. 실제 validation은 [검증 JSON](../docs/validation/2026-10-19-results.json)에 남아 있습니다.

관련 문서: [README](../README.md), [PRD](../PRD.md), [구조 결정](2026-10-19-architecture.md), [GPU 실행 안내](2026-10-19-gpu-runbook.md), [검색 평가](2026-10-19-search-evaluation.md), [작업 기록](2026-10-19-work-log.md).
