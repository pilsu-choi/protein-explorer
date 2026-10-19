---
type: Experiment
title: RTX 4070 첫 ColabFold 예측
description: RTX 4070에서 공개 1UBQ의 실제 구조 예측을 완료하고 산출물과 실행 환경을 검증한 기록입니다.
tags: [gpu, colabfold, alphafold2, reproducibility, 1ubq]
status: stable
generated:
  by: codex/gpt-6
  at: 2026-10-19T12:43:10Z
verified:
  by: process:biopython-artifact-check
  at: 2026-10-19T12:41:03Z
sources:
  - id: rcsb-1ubq
    resource: https://www.rcsb.org/structure/1UBQ
    title: RCSB PDB 1UBQ human ubiquitin structure
  - id: colabfold
    resource: https://github.com/sokrypton/ColabFold
    title: ColabFold official installation and prediction documentation
  - id: jax-install
    resource: https://docs.jax.dev/en/latest/installation.html
    title: JAX official NVIDIA GPU installation documentation
  - id: sample-fasta
    resource: ../examples/1ubq.fasta
    title: 1UBQ reference prediction FASTA
  - id: sample-metadata
    resource: ../examples/1ubq.json
    title: 1UBQ reference metadata and provenance
  - id: prd
    resource: ../PRD.md
    title: Protein Explorer product requirements
  - id: validation
    resource: file:///home/pilsu/side-prj/protein-explorer-runtime/runs/1ubq-gpu-bounded/validation.json
    title: 실제 PDB와 점수 산출물 검증 결과
---

# 목적과 범위

WSL2 x86_64 데스크톱의 분리된 ColabFold 환경에서 공개 참조 단백질 1UBQ를 실제 GPU로 예측했습니다. 최종 실행은 약 37초에 완료됐으며 PDB 76개 잔기와 입력 서열 일치, 좌표 유효성, pLDDT 76개와 PDB B-factor의 대응을 Biopython으로 확인했습니다. 평균 pLDDT는 95.19입니다. 설치·GPU 장치 노출·실제 추론·산출물 검증을 모두 통과한 단일 샘플 기록입니다.

원격 `pgx` 장비의 GR00T 작업은 계속 사용 중이므로 그 장비에서 새 GPU 작업을 시작하거나 기존 프로세스를 변경하지 않았습니다.

## 입력과 출처

입력은 [RCSB PDB 1UBQ](https://www.rcsb.org/structure/1UBQ)의 human ubiquitin 단일 사슬입니다. RCSB는 76개 잔기 구조를 단일 사슬로 기술하고, 실험 구조의 X-ray 해상도를 1.80 Å로 보고합니다. 예측 입력과 출처 메타데이터는 저장소의 [FASTA 샘플](../examples/1ubq.fasta)과 [샘플 설명](../examples/1ubq.json)에 보존했습니다.

ColabFold는 `mmseqs2_uniref_env` 공개 MSA 방식을 사용하도록 실행했습니다. 따라서 입력 서열이 ColabFold의 공개 MSA 서비스로 전송됩니다. 이번 실행은 공개 참조 서열을 대상으로 합니다. ColabFold 설치와 MSA 동작은 [공식 저장소 문서](https://github.com/sokrypton/ColabFold), JAX GPU 설치 근거는 [공식 JAX 안내](https://docs.jax.dev/en/latest/installation.html)를 따릅니다.

## 확인된 환경과 설치

- 장비: NVIDIA GeForce RTX 4070, VRAM 12,282 MiB, driver 610.88
- 실행 환경: WSL2, Linux x86_64, Python 3.12.13
- 설치 구성 요소: ColabFold 1.6.3, `alphafold-colabfold` 2.3.20, JAX 0.11.2, CUDA 12 pip wheels
- GPU 확인: 작은 JAX `arange` 제곱 연산이 `CudaDevice(0)`에서 완료됨
- 설치 스크립트: [`scripts/setup_colabfold.sh`](../scripts/setup_colabfold.sh)
- 고정 의존성: [`requirements/colabfold-linux-x86_64.lock`](../requirements/colabfold-linux-x86_64.lock)
- 분리 런타임: `/home/pilsu/side-prj/protein-explorer-runtime/`
  - ColabFold 실행 환경: `colabfold/`
  - 모델 파라미터: `weights/`
  - 캐시: `cache/`
  - 성공 실행 출력: `runs/1ubq-gpu-bounded/`
  - 실패 기록: `runs/1ubq-first/`, `runs/1ubq-gpu/`

AlphaFold2 PTM 가중치 아카이브는 공식 Google Storage URL `https://storage.googleapis.com/alphafold/alphafold_params_2021-07-14.tar`에서 받았습니다. 내려받기 전에 확인한 HEAD 길이는 3,722,752,000 bytes였습니다. 설치 및 준비 과정에서 가중치 아카이브를 확인했고, 예측 시 해당 파라미터 캐시를 사용합니다.

OpenMM 및 Amber relaxation은 설치하거나 사용하지 않았습니다. Docker 기반 실행, 웹 화면 및 호스팅은 이 작업 범위에 포함되지 않았습니다.

## 실행 설정과 현재 상태

실행은 [`scripts/predict_reference.sh`](../scripts/predict_reference.sh)로 시작했습니다. ColabFold에 전달한 설정은 다음과 같습니다.

- 모델: `alphafold2_ptm`
- 모델 수: 1
- 재활용 횟수: 3
- MSA 방식: `mmseqs2_uniref_env` 공개 MSA
- 최대 실행 시간: 3,600초
- 출력 경로: `/home/pilsu/side-prj/protein-explorer-runtime/runs/1ubq-gpu-bounded/`
- 장치: `JAX_PLATFORMS=cuda,cpu`와 기본 backend `gpu` 확인. AlphaFold 내부 CPU 연산도 허용합니다.
- 메모리: `TF_FORCE_UNIFIED_MEMORY=0`, `XLA_PYTHON_CLIENT_PREALLOCATE=false`, `XLA_PYTHON_CLIENT_MEM_FRACTION=0.5`
- 리소스: `OMP_NUM_THREADS=4`, `OPENBLAS_NUM_THREADS=4`, 해당 실행 shell에서 `ulimit -c 0`
- 실제 ColabFold config: `num_seeds=1`, `random_seed=0`, templates/relaxation 사용 안 함, `use_fast_kernels=false`, `compile_mode=tuned`

상태: **성공 및 산출물 검증 완료**. MSA·초기화가 포함된 이번 실행의 총 시간이며, 첫 가중치 다운로드와 앞선 실패 시간은 제외합니다. 반복 추론 성능이나 다른 길이·단백질의 정확도를 나타내는 벤치마크는 아닙니다.

## 결과

| 항목 | 결과 |
|---|---|
| 상태·종료 코드 | `succeeded`, `0` |
| 시작 → 종료 UTC | 2026-10-19 12:39:44 → 12:40:21 |
| 전체 실행 / ColabFold 모델 보고 시간 | 약 37초 / 17.2초 |
| 구조 | `protein-explorer_unrelaxed_rank_001_alphafold2_ptm_model_1_seed_000.pdb` |
| 점수 | `protein-explorer_scores_rank_001_alphafold2_ptm_model_1_seed_000.json` |
| 잔기·서열 | CA 잔기 76개, 입력 서열과 일치, 좌표 유한값 확인 |
| pLDDT | 76개, 최솟값 46.75 / 평균 95.1861 / 최댓값 98.38 |
| 추가 검증 | CA B-factor와 pLDDT 차이 0.05 미만 |
| 입력 SHA-256 | `233b4b0b8c4616095bc3249f9375fc345d32af7deaef07117d0383c51d6f19aa` |

성공 디렉터리의 `manifest.json`, `environment.json`, `config.json`, `validation.json`, `checksums.json`, `colabfold.log`를 보존했습니다. `checksums.json`은 입력·PDB·점수·실제 반환 A3M·설정·환경과 사용한 `params_model_1_ptm.npz`의 SHA-256을 포함합니다. 공개 MSA 서버의 데이터베이스 버전은 응답에 명시되지 않아 미상으로 기록하고 실제 반환 A3M을 보존했습니다. 원본 가중치·결과는 Git 밖의 runtime에 둡니다.

pLDDT는 예측 신뢰도입니다. 최솟값이 낮은 잔기가 있으며 평균만으로 모든 영역이 확실하다고 해석하지 않습니다. 실험 구조 대비 RMSD 등 정확도 평가는 이번 범위에 포함하지 않았습니다.

## 실패와 재시도

1. `1ubq-first`: CUDA만 허용해 AlphaFold가 요청한 CPU backend를 사용할 수 없었습니다. ColabFold는 종료 코드 0을 반환했지만 PDB가 없어 앱은 실패로 처리했습니다. CPU backend를 함께 허용하도록 수정했습니다.
2. `1ubq-gpu`: 기본 통합 메모리 설정에서 native crash 후 WSL core dump 수집 상태를 확인했습니다. 이번에 시작한 프로세스만 종료했으므로 기록된 최종 반환 코드는 `-9`입니다. 원래 native crash의 신호와 정확한 원인은 확정하지 않았습니다.
3. `1ubq-gpu-bounded`: 통합 메모리를 끄고 메모리 fraction을 0.5로 설정해 성공했습니다. 여러 설정이 함께 달라졌으므로 단일 원인이 입증됐다고 해석하지 않습니다.

일반 검증은 `PYTHONPATH=src python3 -m unittest discover -s tests` 10개를 통과했고, 설치 lockfile dry-run은 64개 패키지를 변경 없이 확인했습니다. 실제 GPU 검증과 모의 실행 테스트는 별개입니다. 영속 작업 큐·캐시·검색·3D 화면은 후속 작업입니다.

관련 문서: [PRD](../PRD.md), [GPU 실행 안내](2026-10-19-gpu-runbook.md), [작업 기록](2026-10-19-work-log.md).
