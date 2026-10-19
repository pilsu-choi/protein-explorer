# Protein Explorer

단백질 서열에서 **로컬 GPU 구조 예측 → 유사 단백질 검색 → 3D 비교**로 이어지는 학습용 프로젝트입니다. 제품 범위는 [PRD](PRD.md), 작업 규칙은 [AGENTS.md](AGENTS.md), 결정과 실행 기록은 [wiki](wiki/2026-10-19-index.md)에 있습니다.

현재는 **CLI와 첫 실제 GPU 예측 검증**까지 완료했습니다. RTX 4070에서 공개 1UBQ(76잔기)를 약 37초에 예측했고 평균 pLDDT는 95.19입니다. 단일 샘플 결과이며 일반 정확도·처리량 기준은 아닙니다. 검색·웹 뷰어·비동기 워커·캐시는 후속 구현입니다.

## 빠르게 시작하기

Python 3.10 이상이 필요합니다. 앱 CLI는 표준 라이브러리만 사용하며 GPU 추론 환경은 아래 설치 절차로 따로 준비합니다.

```bash
cd /home/pilsu/side-prj/protein-explorer-worktrees/gpu-first-prediction
PYTHONPATH=src python3 -m protein_explorer.cli --help
PYTHONPATH=src python3 -m protein_explorer.cli doctor
PYTHONPATH=src python3 -m protein_explorer.cli validate ACDEFGHIKLMNPQRSTVWYACDEFGHIKLMNPQRSTVWY
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

위 서열은 입력 검증을 위한 합성 예시입니다. 생물학적 구조·기능의 기준 샘플로 사용하지 않습니다.

설치형 명령을 쓰려면 프로젝트 전용 가상환경에서 다음을 실행합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/protein-explorer --help
```

## 실제 구조 예측

ColabFold는 앱과 분리된 환경에 설치합니다. **RTX 4070 / VRAM 12282 MiB / 드라이버 610.88 / WSL2 Linux x86_64**에서 Python 3.12.13, ColabFold 1.6.3, JAX 0.11.2와 CUDA 12 패키지를 설치했고 실제 JAX GPU 연산을 확인했습니다. 아래 스크립트는 [고정된 의존성](requirements/colabfold-linux-x86_64.lock)을 사용합니다. OpenMM/Amber relaxation은 설치·사용하지 않습니다.

```bash
export PROTEIN_EXPLORER_RUNTIME=/home/pilsu/side-prj/protein-explorer-runtime
bash scripts/setup_colabfold.sh "$PROTEIN_EXPLORER_RUNTIME"
bash scripts/predict_reference.sh "$PROTEIN_EXPLORER_RUNTIME"
```

`uv`가 필요하며 OS 드라이버나 시스템 Python은 변경하지 않습니다. 최초 예측은 약 3.72GB의 AlphaFold 가중치를 받아 `weights/params`에 보관합니다. 공개 기준 서열은 출처와 함께 [1UBQ FASTA](examples/1ubq.fasta)와 [메타데이터](examples/1ubq.json)로 제공하며, 스크립트는 모델 1개·recycle 3회로 실행합니다. 실행 결과와 제한 사항은 [첫 GPU 예측 기록](wiki/2026-10-19-first-gpu-prediction.md)에 유지합니다.

이 WSL2 환경은 ColabFold 기본 통합 메모리 설정에서 native crash가 발생했습니다. 제공 스크립트는 통합 메모리를 끄고 메모리 fraction을 0.5로 설정하며, CUDA와 CPU backend를 함께 허용하고 기본 장치가 GPU인지 확인합니다. 일반 CLI를 직접 실행할 때도 같은 환경 설정이 필요합니다.

`ssh pgx`의 원격 장비는 **ARM64 / GB10 / 시스템 메모리 약 119GiB**입니다. 이전 점검 시 GPU 사용률 96%로 GR00T 테스트가 진행 중이어서 추가 작업을 실행하지 않았습니다. 위 lockfile은 Linux x86_64 전용이며 ARM 장비에는 적용하지 않습니다. Docker 호스팅과 웹 UI는 아직 구현하지 않았습니다.

단일 단백질의 FASTA 파일을 준비합니다. 초기 입력 범위는 표준 아미노산 20종, 30~300잔기이며 복수 FASTA는 거부합니다. 사용자가 확보한 실제 단백질 서열을 `protein.fasta`로 저장한 뒤 실행합니다.

```bash
PYTHONPATH=src python3 -m protein_explorer.cli validate --file protein.fasta
export JAX_PLATFORMS=cuda,cpu
export TF_FORCE_UNIFIED_MEMORY=0
export XLA_PYTHON_CLIENT_PREALLOCATE=false
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.5
PYTHONPATH=src python3 -m protein_explorer.cli predict --file protein.fasta \
  --colabfold-batch /absolute/path/to/colabfold-env/bin/colabfold_batch \
  --output-dir runs/first-prediction \
  --timeout 7200 \
  --num-models 1 \
  --data-dir "$PROTEIN_EXPLORER_RUNTIME/weights" \
  --num-recycle 3
```

실행 파일 경로는 실제 설치 위치로 바꿉니다. 출력 경로는 기존에 존재하지 않아야 합니다. 실행기는 FASTA, 로그, 상태·설정을 담은 `manifest.json`과 ColabFold 결과를 저장하고, 구조 및 신뢰도 산출물이 불완전하면 실패로 처리합니다. 기본 모델은 `alphafold2_ptm`, MSA 방식은 `mmseqs2_uniref_env`입니다. 패키지·가중치·데이터베이스 버전 자동 수집은 M1에서 보강하며 현재는 실행 환경의 버전을 별도 기록합니다. 현재는 CLI가 끝날 때까지 기다리는 동기 실행이며, 실패 후 재시도에는 새 출력 경로를 사용합니다.

기본 MSA 방식은 **입력 서열을 ColabFold 공개 서버에 전송**합니다. 공개 서버는 단일 IP에서 순차 요청하며 여러 장비에서 병렬 요청하지 않습니다. 모델 가중치는 ColabFold 최초 실행 시 별도 다운로드될 수 있습니다. 자세한 설치·실측 절차는 [GPU 실행 지침](wiki/2026-10-19-gpu-runbook.md)에 있습니다.

## 개발 순서

1. **M0:** 문서와 입력 검증·진단·ColabFold 실행 CLI.
2. **M1:** 실제 GPU 추론 검증, 지속적인 작업 상태·캐시·실패 복구.
3. **M2:** 공개 단백질 코퍼스, ESM-2 임베딩과 유사 검색.
4. **M3:** 구조 신뢰도 표시, 3D 비교 화면과 전체 튜토리얼.

구현 세부 사항과 완료 조건은 [PRD](PRD.md)에 유지합니다. 검색 유사도는 동일 기능의 확률이 아니며, 구조 예측 신뢰도와 구분합니다.

## 브랜치와 worktree

통합 기준은 로컬 `dev`이며 원격 저장소는 아직 연결하지 않았습니다. 초기 구성을 dev에 반영한 뒤 `feat/gpu-first-prediction` 브랜치와 별도 worktree에서 GPU 검증을 진행합니다.

```bash
git -C /home/pilsu/side-prj/protein-explorer worktree list
```

다음 세션도 최신 `dev`에서 새 브랜치와 worktree를 만들어 진행합니다. 변경 사항과 검증 결과는 [작업 기록](wiki/2026-10-19-work-log.md)에 남깁니다.
