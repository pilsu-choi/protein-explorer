# Protein Explorer PRD

작성일: 2026-10-19 · 상태: MVP 구현, family-held-out 검색 평가 후속 · 제품명: Protein Explorer

## 제품 목적

로컬 사용자가 단백질 서열을 검증하고 공개 단백질 데이터에서 유사 서열을 검색하며, GPU에서 구조를 예측하고 예측 구조와 참조 구조를 함께 살펴보는 학습용 도구다. 검색 점수와 구조 신뢰도를 생물학적 기능의 증거로 오해하지 않도록 출처와 평가 한계를 함께 제공한다.

대상 환경은 Docker Compose가 설치된 단일 Linux 호스트다. API와 화면은 CPU에서도 실행되며, 예측은 별도 GPU 워커에 맡긴다. 확인된 개발 호스트는 WSL2 Linux x86_64, NVIDIA RTX 4070 12,282 MiB, Python 3.12.13, ColabFold 1.6.3, JAX 0.11.2다. 다른 운영체제와 GPU 조합의 호환성은 별도 검증이 필요하다.

## 사용자 흐름

1. 샘플 또는 단일 FASTA/아미노산 서열을 입력하고 검색 작업을 시작한다.
2. ESM-2 검색 결과와 출처를 살펴보고 필요하면 참조 구조를 연다.
3. GPU 워커가 실행 중이면 구조 예측을 제출하고 작업 상태를 갱신한다.
4. 완료 후 예측 구조의 pLDDT 색상, 참조 구조, 검색 점수와 메타데이터를 확인한다.
5. PDB, 점수, manifest 또는 실행 bundle을 내려받는다.

## MVP 기능 및 현재 상태

| 영역 | 요구사항과 완료 기준 | 상태 |
|---|---|---|
| 입력 | 단일 서열을 정규화하고 비표준 문자와 범위 밖 길이를 거부한다. | 구현 |
| 웹/API | FastAPI API와 바닐라 JavaScript 화면에서 검색·작업 조회·구조 열기·다운로드를 제공한다. | 구현 |
| 공개 코퍼스 | RCSB 공개 구조에서 최대 1,000개 레코드를 가져오고 정확히 같은 서열을 deduplicate하며 출처/체인 메타데이터를 보존한다. 참조 구조는 요청 시 가져와 캐시한다. | 구현 |
| 임베딩 검색 | `facebook/esm2_t6_8M_UR50D`, revision `c731040fcd8d73dceaa04b0a8e6329b345b0f5df`; 잔기 토큰 평균, padding/special token 제외, 320차원, L2 정규화, cosine 상위 10개. | 파이프라인 구현, 초기 기준선 비교 완료; family-held-out 검증 대기 |
| 구조 예측 | ColabFold로 실제 GPU 예측을 수행하고 PDB, 점수, 로그 및 manifest를 보존한다. | 네 공개 샘플 RTX 4070 실행 및 Docker 1UBQ 예측 검증 완료 |
| 작업 큐 | SQLite에 queued/running/succeeded/failed를 저장하고 GPU 작업은 worker가 하나씩 처리한다. heartbeat, 중단 작업 복구와 재조회가 동작한다. | 구현 |
| 캐시 | 입력·추론 설정·런타임 fingerprint가 일치하는 성공 결과만 재사용하며 핵심 산출물 checksum을 검증한다. 가중치 SHA가 fingerprint에 포함된다. | 구현 |
| 3D 화면 | 예측 구조와 참조 구조를 보여주고 pLDDT 색상과 검색 점수를 분리해 표시한다. | 구현; 정렬/RMSD 미지원 |
| 재현·출처 | 모델과 설정, 데이터 출처, 실행 결과 및 다운로드 bundle을 보존한다. | 구현 |

검색 인덱스의 임베딩은 질의 인코더와 같은 고정 모델·pooling·정규화 설정을 사용한다. 예측 캐시 런타임 fingerprint는 엔진/패키지·모델 설정과 ColabFold 가중치의 SHA-256 정보를 포함한다. 자세한 구성은 [아키텍처 결정](wiki/2026-10-19-architecture.md)에서 관리한다.

공개 MSA 서버의 데이터베이스 버전은 원격에서 고정하거나 확인할 수 없다. 질의와 응답 MSA 정보, 설정과 가중치 checksum을 보존해도 공개 서버 질의의 bitwise 재현성을 보장하지 않는다.

## 검색 평가와 후속 검증

검색 알고리즘 구현과 검색 결과의 일반화 평가는 별개다. 전체 실행 데이터는 Git 외부 runtime에 저장하며, 측정 요약은 [2026-10-19 검색 평가 기록](wiki/2026-10-19-search-evaluation.md)에 기록한다.

- 1,000개 코퍼스 self-search의 1,000/1,000 self-hit은 파이프라인 sanity check이며 검색 품질의 근거가 아니다.
- 같은 UniProt 그룹 안에서 107개 그룹, 256개 query를 평가하고 query의 자기 자신을 제외했다. ESM-2 평균 Recall@10은 90.756448%, 3-mer Jaccard 기준선은 94.237351%다. HitRate@10은 각각 96.09375%, 99.21875%다.
- 이 평가는 가족/계열 그룹을 분리한 held-out 일반화 평가가 아니다. 이 데이터에서는 단순 기준선이 ESM-2보다 앞섰으며, 다음 규모의 모델을 선택할 때 참고할 관측 결과로 보존한다.

초기 same-UniProt 평가와 3-mer 기준선 비교를 완료했다. 이 데이터에서 3-mer 기준선은 Recall@10 94.24%, ESM-2는 90.76%였다. 계열/서열 유사도를 고려해 분할하는 held-out 일반화 평가와 그 결과에 따른 모델 선택은 후속 작업이다. 새 평가 결과는 기존 결과와 구분해 보존한다.

## 구조 예측 실측

RTX 4070에서 실행한 네 공개 서열은 다음과 같다. 시간은 전체 예측 경과 시간이며 피크 메모리는 실행 중 관측한 전체 GPU 장치 사용량에 데스크톱 사용량이 포함된 값이다.

| PDB 샘플 | 길이 | 경과 시간 | 평균 pLDDT | 피크 장치 메모리 |
|---|---:|---:|---:|---:|
| 1UBQ | 76 aa | 34 s | 95.21 | 5,223 MiB |
| 45AR_2 | 100 aa | 48 s | 96.02 | 5,616 MiB |
| 9ZWZ_1 | 199 aa | 59 s | 85.18 | 5,376 MiB |
| 13GK_1 | 298 aa | 100 s | 85.36 | 5,554 MiB |

이는 단일 개발 호스트의 네 관측치며 처리량 보장이나 프로세스 단독 VRAM 측정이 아니다. pLDDT는 구조 예측 신뢰도이며 정답 정확도나 기능 검증 점수가 아니다. 실험 구조 대비 RMSD/TM-score는 아직 계산하지 않았다.

## 배포 및 실행 조건

- 기본 실행은 `docker compose build app`, corpus 준비 profile, GPU profile의 `app`과 `worker` 순서다. [튜토리얼](wiki/2026-10-19-mvp-tutorial.md)에 정확한 명령이 있다.
- 앱은 기본적으로 호스트 `127.0.0.1:8080`에만 바인딩한다. 예측 워커는 NVIDIA Container Toolkit 및 Docker GPU runtime이 필요하다. 앱/검색은 CPU로 구동할 수 있다.
- 공개 MSA 기본값 `mmseqs2_uniref_env`는 입력 서열을 외부 ColabFold 서비스에 전송한다. 웹 화면은 공개 MSA 모드만 제공한다. 비공개 입력은 CLI/API의 `single_sequence` 설정을 사용하거나 로컬 MSA를 준비해야 한다.
- 모델 파일과 결과는 Docker named volumes 또는 별도 runtime에 둔다. ESM-2 8M 가중치는 약 32 MB이며 첫 ColabFold 예측은 AlphaFold 가중치 약 3.7 GB를 내려받는다.
- 앱과 GPU 워커 이미지가 크므로 디스크 계획에 반영한다. Docker GPU 검증에서 worker가 1UBQ 76잔기를 33초에 예측하고 76개 CA 원자 및 pLDDT 산출물을 기록했다. 동일 요청은 checksum 검증 캐시를 재사용했다. 예측 컨테이너는 JAX 0.10.2를 사용한다.
- Docker CPU 검색 검증은 1,000개 코퍼스에서 1UBQ를 top 1, cosine 0.99999994로 반환했다.

## 제외 및 제한

모델 사전학습, 질병·약효 예측, 단백질 설계, 복합체/리간드 예측, 다중 GPU 분산, 다중 사용자 공개 호스팅, 기능 확률 산출, 구조 정렬과 RMSD는 범위 밖이다. 코퍼스는 탐색용 1,000개 공개 레코드이며 완전한 생물학 지식베이스가 아니다. 일부 기능 주석이 비어 있을 수 있다. 3D 비교는 나란히 렌더링하며 잔기 대응을 계산하지 않는다.

## 단계와 완료 조건

- 기반 및 GPU 예측: 구현 및 실호스트 샘플 실행 기록 완료.
- 검색/API/화면/작업 큐 통합: 구현 완료.
- 검색 평가: 같은 UniProt 그룹 기준선 비교 완료. Family-held-out 일반화 평가와 후속 모델 선택 검토가 남아 있다.
- 패키징: Docker GPU 구동에서 1UBQ 성공 및 cache 재사용을 확인했다. 30개 자동화 테스트와 브라우저 7개 점검도 통과했다. 결과와 제한은 날짜별 [검증 JSON](docs/validation/2026-10-19-results.json)에 있다.

전체 MVP 완료 전 family-held-out 검색 일반화 결과를 확인하고 모델 선택을 검토한다. 입력부터 결과 다운로드까지 문서화했고 Docker GPU 실행 경로도 2026-10-19에 검증되었다. 자동화 검증에서 30개 테스트가 통과했다. 초기 same-UniProt 검색 평가와 baseline 비교는 완료했다.

## 근거와 관련 문서

- [README와 빠른 시작](README.md)
- [구조와 설계 결정](wiki/2026-10-19-architecture.md)
- [첫 실행부터 검색·예측·비교까지](wiki/2026-10-19-mvp-tutorial.md)
- [검색 평가 결과](wiki/2026-10-19-search-evaluation.md)
- [OKF 위키 목차](wiki/2026-10-19-index.md)
- [ColabFold](https://github.com/sokrypton/ColabFold), [ESM](https://github.com/facebookresearch/esm), [RCSB PDB](https://www.rcsb.org/)
