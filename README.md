# Protein Explorer

단백질 서열에서 **로컬 GPU 구조 예측 → 유사 단백질 검색 → 3D 비교**로 이어지는 학습용 프로젝트입니다. 제품 범위는 [PRD](PRD.md), 작업 규칙은 [AGENTS.md](AGENTS.md), 결정과 실행 기록은 [wiki](wiki/2026-10-19-index.md)에 있습니다.

현재는 **M0 초기 CLI**입니다. 단일 서열 검증, 환경 진단, 설치된 ColabFold를 호출하는 실행기를 제공합니다. 검색·웹 뷰어·비동기 워커·캐시는 후속 구현입니다. 실제 GPU 구조 예측 성공은 아직 검증하지 않았습니다.

## 빠르게 시작하기

Python 3.10 이상이 필요합니다. 앱 자체는 표준 라이브러리만 사용합니다. 가중치나 GPU 패키지를 자동 다운로드하지 않습니다.

```bash
cd /home/pilsu/side-prj/protein-explorer-worktrees/bootstrap
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

ColabFold는 앱과 분리된 환경에 설치합니다. [공식 설치 문서](https://github.com/sokrypton/ColabFold#installation)를 따르고, 해당 환경에서 GPU를 인식하는지 먼저 확인합니다. 현재 확인한 장비는 **RTX 4070 / VRAM 12282 MiB / 드라이버 610.88 / WSL2 Linux x86_64**입니다. `ssh pgx`로 접근하는 원격 장비는 **ARM64 / GB10 / 시스템 메모리 약 119GiB**입니다. 확인 시점 GPU 사용률 96%로 기존 GR00T 테스트가 진행 중이어서 추가 작업을 실행하지 않았습니다. 두 장비 모두 ColabFold가 PATH에 없고 JAX/CUDA 실행 호환성은 별도 확인이 필요합니다.

단일 단백질의 FASTA 파일을 준비합니다. 초기 입력 범위는 표준 아미노산 20종, 30~300잔기이며 복수 FASTA는 거부합니다. 사용자가 확보한 실제 단백질 서열을 `protein.fasta`로 저장한 뒤 실행합니다.

```bash
PYTHONPATH=src python3 -m protein_explorer.cli validate --file protein.fasta
PYTHONPATH=src python3 -m protein_explorer.cli predict --file protein.fasta \
  --colabfold-batch /absolute/path/to/colabfold-env/bin/colabfold_batch \
  --output-dir runs/first-prediction \
  --timeout 7200 \
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

새 저장소의 통합 기준은 로컬 `dev`입니다. 원격 저장소는 아직 연결하지 않았습니다. 현재 작업 브랜치는 `feat/project-bootstrap`이며 `dev`와 분리된 worktree에서 개발합니다.

```bash
git -C /home/pilsu/side-prj/protein-explorer worktree list
```

다음 세션도 최신 `dev`에서 새 브랜치와 worktree를 만들어 진행합니다. 변경 사항과 검증 결과는 [작업 기록](wiki/2026-10-19-work-log.md)에 남깁니다.
