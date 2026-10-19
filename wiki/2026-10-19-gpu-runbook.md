---
type: Playbook
title: 로컬 GPU 구조 예측 실행 안내
description: 장비 확인, 별도 ColabFold 설치, 실제 예측, 재현 정보 보존 절차를 단계별로 안내합니다.
tags: [gpu, colabfold, tutorial, reproducibility, msa]
status: draft
generated:
  by: codex/gpt-6
  at: 2026-10-19T12:25:02Z
sources:
  - id: prd
    resource: ../PRD.md
    title: Protein Explorer 제품 요구사항
  - id: okf-v02
    resource: file:///home/pilsu/projects/Knowledge-Forge/wiki/open-knowledge-format.md
    title: Open Knowledge Format v0.2 로컬 참고 문서
  - id: colabfold-readme
    resource: https://github.com/sokrypton/ColabFold/blob/main/README.md
    title: ColabFold 설치 및 batch 예측 안내
  - id: colabfold-wiki
    resource: https://github.com/sokrypton/ColabFold/wiki/Home
    title: ColabFold 로컬 검색 및 공개 MSA 안내
  - id: esm
    resource: https://github.com/facebookresearch/esm
    title: Meta FAIR ESM 저장소 및 ESM-2 모델 문서
  - id: dgx-spark
    resource: https://docs.nvidia.com/dgx/dgx-spark/hardware.html
    title: NVIDIA DGX Spark 하드웨어 안내서
  - id: spark-porting
    resource: https://docs.nvidia.com/dgx/dgx-spark-porting-guide/overview.html
    title: NVIDIA DGX Spark 포팅 안내서 개요
---

# 목적과 현재 상태

이 절차는 실제 로컬 추론을 수행하고 근거를 기록하기 위한 안내입니다. 데스크톱은 WSL2 Linux x86_64, NVIDIA GeForce RTX 4070, VRAM 12,282 MiB, driver 610.88이며 읽기 전용 `nvidia-smi` 실행으로 확인했습니다. `ssh pgx`의 원격 장비는 Linux aarch64, NVIDIA GB10, driver 580.178.04, 시스템 메모리 약 119GiB입니다. 확인 시점 GPU 사용률은 96%, Python GPU 프로세스 사용 메모리는 약 40GB, 시스템 가용 메모리는 약 38GiB였습니다. 기존 GR00T 테스트가 진행 중이므로 추가 추론은 실행하지 않았습니다. 이 값은 순간 관측값이며 다음 실행 전에 다시 확인합니다.

양쪽 모두 `colabfold_batch`가 PATH에 없습니다. 장비 확인은 CUDA runtime/JAX 호환성이나 실제 추론 검증을 대신하지 않습니다. [구조와 범위](2026-10-19-architecture.md)를 먼저 읽고, 결과는 Git에 넣지 말고 ignore 처리된 데이터/출력 디렉터리에 보관합니다.

## 1단계: 장비 확인

날짜, 호스트 식별명(공유 기록에 개인 식별 정보를 넣지 않음), OS와 버전, CPU 아키텍처, NVIDIA driver, CUDA 장치 노출 여부, GPU 모델, 사용 가능한 메모리를 기록합니다. NVIDIA Linux 시스템에서는 `uname -a`, `uname -m`, `nvidia-smi`, `nvidia-smi -L`을 확인하고 일련번호와 사용자명을 제거한 출력을 실행 기록에 남깁니다. `nvidia-smi`를 사용할 수 없으면 GPU 준비가 되었다고 추정하지 말고 확인 결과를 그대로 기록합니다.

원격 장비 확인은 `ssh -o BatchMode=yes -o ConnectTimeout=8 pgx`를 사용합니다. GPU 점유, 실행 중인 작업, 시스템 가용 메모리, 디스크 여유를 읽기 전용으로 확인합니다. GR00T 프로세스의 중단·재시작·GPU 메모리 정리를 하지 않습니다. 통합 메모리가 남아 있어도 GPU 연산이 바쁘면 추론을 추가하지 않습니다.

Spark 계열 장비는 소프트웨어 이미지, ARM 아키텍처, 지원 프레임워크 버전을 해당 장비 문서에서 확인합니다. NVIDIA 안내서는 하드웨어 특성을 설명할 뿐 모든 ColabFold 의존성이나 커널이 동작함을 보장하지 않습니다. 해당 장비에서 실제 예측에 성공하기 전까지 호환성은 미확인으로 둡니다.

## 2단계: 추론 소프트웨어 별도 설치

확인된 OS와 아키텍처에 맞춰 최신 공식 [ColabFold 설치 안내](https://github.com/sokrypton/ColabFold)를 따릅니다. 격리 환경이나 문서화된 컨테이너를 우선 사용합니다. ColabFold release/commit, Python과 패키지 버전, 해당되는 CUDA/JAX/PyTorch 구성 요소, 설치 프로그램 또는 컨테이너 digest, 전체 명령 인자를 기록합니다. doctor 확인을 통과시키기 위해 앱 환경에 별도 사본을 설치하지 않습니다.

가중치나 서열 데이터베이스를 내려받기 전에 디스크 여유 공간, 예상 다운로드 용량, 네트워크 상태, 저장 경로를 확인합니다. 다운로드 항목과 버전을 기록합니다. 설치법과 의존성 지원은 플랫폼별로 다르고 바뀔 수 있으므로 이 안내서는 하나의 공통 명령을 정하지 않습니다. 선택한 환경에 맞는 최신 공식 안내를 따릅니다.

## 3단계: MSA 방식과 시험 서열 선택

accession과 출처가 확인되는 짧은 공개 단일 사슬 서열 하나를 사용합니다. accession, 서열 checksum, 알려진 경우 생물종, 공개 시험 입력이라는 점을 기록합니다. 초기 ColabFold 흐름은 서열을 공개 MSA 서비스로 전송할 수 있으므로 실행 전에 원격 전송 사실을 표시합니다. 비공개 서열에는 공개 서비스를 사용하지 말고 로컬 MSA 설정을 사용합니다. 공개 서비스는 요청 수에 제한이 있으며 대량 요청용이 아닙니다. ColabFold 안내에 따라 한 IP에서 순차적으로 요청하고 여러 호스트에서 동시에 MSA 요청을 보내지 않습니다. 로컬 MSA 검색은 상당한 데이터 저장 공간과 설정이 필요한 후속 선택지입니다.

비공개 입력 서열을 터미널 기록, 위키, 공유 로그에 남기지 않습니다. 공개 시험 서열은 MSA가 원격인지 로컬인지, 가능한 경우 서비스/데이터베이스 식별자와 날짜·버전, 재시도나 요청 제한 응답을 기록합니다.

## 4단계: 실제 예측 1회 실행

설치된 `colabfold_batch`(또는 현재 문서에서 안내하는 동등 명령)에 FASTA 하나와 비어 있는 전용 출력 디렉터리를 지정합니다. 먼저 실행 파일의 최신 도움말을 확인한 뒤 장비에 맞는 출력 및 시간 제한 설정을 선택합니다. 정확한 인자 배열을 manifest에 보존합니다. 앱은 shell 없이 실행 파일을 호출하고 유한한 시간 동안 기다리며 stdout/stderr를 저장해야 합니다. 종료 코드만으로 성공 처리하지 말고 예상 산출물을 확인해 구조와 신뢰도 데이터를 읽습니다.

명령 형태는 `colabfold_batch INPUT.fasta OUTPUT_DIR [documented options]`입니다. 옵션은 설치된 버전의 도움말에서 확인합니다. 검증하지 않은 플래그가 포함된 복사·실행용 명령은 제공하지 않습니다. 실패하면 종료 코드, 민감 정보를 제거한 로그, 경과 시간, 실패 유형(의존성 누락, 장치 미노출, OOM, 시간 초과, MSA/서비스 실패, 불완전한 산출물)을 보존합니다.

## 5단계: 실행 검증 및 기록 보존

성공한 실행은 PDB/mmCIF가 비어 있지 않고 예상한 사슬과 잔기 범위를 포함하는지, 신뢰도 산출물이 존재하며 해당 실행과 연결되는지 확인합니다. pLDDT는 예측 신뢰도로 표시합니다. 실험 B-factor를 pLDDT로 해석하거나 pLDDT에서 기능을 추론하지 않습니다. 원본 산출물은 변경하지 않고 생성된 요약과 분리합니다.

결과 옆에 다음 정보를 담은 manifest를 저장합니다.

- 정규화된 입력 서열 checksum, 공개 출처/accession (비공개 서열은 보고서에 복사하지 않음)
- ColabFold, 모델, 프레임워크, Python, CUDA/driver, OS 버전
- 장치명과 메모리, 명령 인자, 해당되는 경우 seed와 설정
- MSA 방식, 데이터베이스/서비스 및 버전·날짜, 원격 전송 여부
- 시작/종료 시각, 경과 시간, 종료 코드, 로그, 산출물 경로, 파일 checksum
- 검증 결과와 알려진 제한 사항

실제 실행과 산출물을 확인한 뒤에만 해당 사실을 `verified`로 기록합니다. 장비별 관측값을 남기고 성능 목표를 정하기 전에 100, 200, 300 잔기 입력으로 측정합니다.

## 문제 해결과 다음 단계

GPU OOM이 발생하면 실패 manifest와 오류를 보존하고 실제 사용 장치와 메모리를 확인한 뒤, 적절히 작업량을 줄이거나 지원되는 모델 설정을 적용합니다. 가짜 결과를 성공으로 대체하지 않습니다. 장치나 라이브러리 오류는 설치 버전과 아키텍처를 최신 upstream 안내와 비교합니다. MSA 장애나 요청 제한은 실패 또는 재시도 가능 작업으로 기록하고, 대기하거나 문서화된 로컬 데이터베이스를 설정합니다. 예측 성공으로 표시하지 않습니다. 구조가 불완전하면 산출물 검증을 실패 처리하고 진단 정보를 보존합니다.

단일 실행 검증을 통과한 뒤 M1에서 GPU 작업을 하나씩 처리하는 워커와 queued/running/succeeded/failed 상태 저장을 연결합니다. 이후 [PRD](../PRD.md)에 따라 코퍼스/검색 평가와 비교 UI를 진행합니다. 현재 검증 상태는 [작업 기록](2026-10-19-work-log.md)에 남깁니다.
