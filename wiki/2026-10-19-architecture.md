---
type: Decision
title: Protein Explorer 구조와 초기 범위
description: M0 구현 범위와 로컬 예측·검색·시각화의 설계 결정을 기록합니다.
tags: [architecture, decisions, scope, protein-structure]
status: stable
generated:
  by: codex/gpt-6
  at: 2026-10-19T12:43:10Z
sources:
  - id: prd
    resource: ../PRD.md
    title: Protein Explorer 제품 요구사항
  - id: okf-v02
    resource: file:///home/pilsu/projects/Knowledge-Forge/wiki/open-knowledge-format.md
    title: Open Knowledge Format v0.2 로컬 참고 문서
  - id: colabfold
    resource: https://github.com/sokrypton/ColabFold
    title: ColabFold 공식 저장소
  - id: esm
    resource: https://github.com/facebookresearch/esm
    title: Meta FAIR ESM 공식 저장소
  - id: dgx-spark
    resource: https://docs.nvidia.com/dgx/dgx-spark/hardware.html
    title: NVIDIA DGX Spark 하드웨어 안내서
---

# 목적과 범위

Protein Explorer는 학습과 연구를 위한 로컬 도구입니다. 목표 흐름은 FASTA 또는 아미노산 서열 하나를 검증하고, ESM-2 임베딩으로 소규모 공개 단백질 코퍼스를 검색하며, 별도 설치된 ColabFold CLI로 구조를 예측한 뒤 출처 정보와 함께 비교하는 것입니다. 기준 문서는 [PRD](../PRD.md)입니다.

## M0 범위

M0에서는 서열 검증, 실행 환경 진단, 설치된 `colabfold_batch` 호출을 위한 Python 패키지와 표준 라이브러리 CLI를 만듭니다. 모델 가중치를 설치하거나 설정 과정에서 모델을 실행하지 않으며, GPU 예측이 성공했다고 표시하지 않습니다. 명령이 존재하거나 subprocess를 모의 실행했다는 사실만으로 실제 추론을 확인할 수 없습니다. 실제 예측 검증은 M1 범위입니다.

## 결정과 제약

- 앱 환경과 GPU 추론 환경을 분리합니다. 설치된 ColabFold 실행 파일은 인자 배열과 시간 제한을 사용해 호출하고 로그를 저장합니다. 사용자 서열을 shell 명령 문자열에 삽입하지 않습니다.
- M0 manifest는 입력 해시·길이, 실행 파일, 모델·MSA 방식·추론 인자, 실행 시각·상태, 로그·결과 파일과 pLDDT 요약을 저장합니다. M1에서 패키지·가중치·MSA 데이터 버전 수집과 유효한 결과만 재사용하는 캐시를 추가합니다.
- 초기 검색 대상은 구조 약 1,000개이며 ESM-2 8M 임베딩과 cosine 유사도를 사용합니다. 이 점수는 임베딩 유사도를 정렬하며 구조가 비슷하거나 기능이 같음을 입증하지 않습니다.
- 공개 MSA를 사용하면 서열이 외부 서비스로 전송됩니다. 실행 전에 이를 사용자에게 알립니다. 비공개 서열에는 로컬 MSA 설정을 사용합니다. 로컬 데이터베이스 구성은 후속 작업입니다.
- 데스크톱의 WSL2 Linux x86_64, NVIDIA GeForce RTX 4070, 12,282 MiB VRAM, driver 610.88은 `nvidia-smi`로 확인했습니다. `ssh pgx`에서는 Linux aarch64, NVIDIA GB10, driver 580.178.04와 시스템 메모리 약 119GiB를 확인했습니다. 확인 시점 원격 GPU 사용률은 96%여서 기존 GR00T 테스트를 유지하고 추가 추론을 실행하지 않았습니다. 두 장비의 CUDA runtime/JAX 호환성과 실제 추론은 아직 확인하지 않았습니다.
- 예측 결과의 pLDDT는 국소 구조에 대한 모델 신뢰도이며 생물학적 기능의 증거가 아닙니다. 실험 구조의 B-factor를 pLDDT로 표시하지 않습니다.

후속 검증으로 RTX 4070에 독립 ColabFold 환경을 설치하고 실제 1UBQ 예측을 완료했습니다. 기존 GPU 미검증 상태는 [첫 GPU 예측 기록](2026-10-19-first-gpu-prediction.md)으로 갱신합니다. 영속 큐·캐시·검색·UI는 계속 계획 단계입니다.

## 계획 단계

M1에서는 GPU 한 대에서 공개 단백질 하나를 실제로 예측하고 PDB, 신뢰도 결과, 로그, manifest, 영속 작업 상태를 확인합니다. M2에서는 출처를 보존하는 코퍼스를 만들고 서열 검색 기준선과 Recall@10을 비교합니다. M3에서는 UI, 예측, 검색, 3D 비교, 다운로드, 처음부터 끝까지 실행하는 튜토리얼을 연결합니다. MVP는 세 단계를 모두 완료해야 합니다. [GPU 실행 안내](2026-10-19-gpu-runbook.md)와 [작업 기록](2026-10-19-work-log.md)을 참고하세요.

## 출처와 해석

ColabFold 공식 자료는 로컬 batch 예측과 MSA 선택지를 설명하며, 공개 MSA 서버 사용에는 요청 제한과 개인정보 고려가 따릅니다. ESM 저장소는 사전 학습된 ESM-2 모델과 임베딩 추출을 설명합니다. NVIDIA 문서는 DGX Spark를 ARM/Grace Blackwell 시스템으로 안내합니다. 이 자료는 설계 근거이며, 아직 확인되지 않은 장비의 호환성이나 성능을 보장하지 않습니다.
