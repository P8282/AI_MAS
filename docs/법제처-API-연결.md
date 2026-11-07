# 법제처 Open API(open.law.go.kr) 연결 방안

규정 DB를 법제처 국가법령정보 공동활용 API로 받고 개정을 점검하는 방법입니다. `규정/tools/fetch_lawgokr.py`, `규정/tools/check_updates.py`와 원래 저장소(PPS_cert)의 규정 DB 설명서(§6.5~6.7)를 바탕으로 정리했습니다. **실제 인증키는 이 저장소 어디에도 넣지 않습니다.**

## 1. 준비
| 항목 | 내용 |
|---|---|
| 인증키(OC) | 법제처 Open API 신청 후 받은 값. 환경 변수 `LAWGO_OC`로만 전달 |
| 등록 IP | 법제처 API는 **신청서에 등록한 IP에서만 응답**합니다(원래 저장소 규정 DB 설명서 §6.5·§6.7). 등록한 PC에서 실행하세요 |
| 필요 프로그램 | Python 3 (표준 라이브러리만 사용) |

신청 화면의 세부 절차와 승인 기간은 이 저장소에서 확인하지 못했습니다. open.law.go.kr의 안내를 따르세요.

## 2. 호출 방식
기준 주소: `https://www.law.go.kr/DRF/`

| 용도 | 주소 | 주요 값 |
|---|---|---|
| 목록 검색 | `lawSearch.do` | `OC`, `target`, `type=JSON`, `query`, `display`(최대 100), `page` |
| 본문 받기 | `lawService.do` | `OC`, `target`, `type=JSON`, 법령은 `MST`(법령일련번호), 행정규칙은 `ID`(행정규칙일련번호) |

`target` 값(도구에서 쓰는 것):
- `law` 법령(법률·시행령·시행규칙)
- `admrul` 행정규칙(훈령·예규·고시·지침·공고)
- `ppsCgmExpc` 조달청 법령해석
- `moefCgmExpc` 재정경제부 법령해석(목록만, 본문은 API가 주지 않음)

예 (키는 환경 변수에서):
```bash
curl -s "https://www.law.go.kr/DRF/lawSearch.do?OC=${LAWGO_OC}&target=law&type=JSON&query=국가를 당사자로 하는 계약에 관한 법률"
```
- 응답의 상세 링크에 OC 값이 그대로 들어 있습니다. 응답을 파일로 저장할 때는 OC를 가려야 합니다. `fetch_lawgokr.py`는 저장 전에 자동으로 가립니다.

## 3. 이 저장소에서 쓸 수 있는 도구
저장소 맨 위 폴더에서 실행합니다.

```bash
export LAWGO_OC='<인증키>'      # 터미널에만. 파일에 적지 않기

# 법령 이름 확인(아무것도 저장하지 않음)
python3 규정/tools/fetch_lawgokr.py --lookup 국가계약법 --target law

# 법령 현행 본문 하나 받기 → 규정/tools/_lawgokr/
python3 규정/tools/fetch_lawgokr.py --law "국가를 당사자로 하는 계약에 관한 법률"

# 행정규칙 현행 본문 받기(여러 개 가능)
python3 규정/tools/fetch_lawgokr.py --rule "물품 다수공급자계약 업무처리규정"

# DB의 규정마다 법제처 현행 시행일과 비교(본문은 받지 않음)
python3 규정/tools/fetch_lawgokr.py --db

# 개정 알림 점검 → 규정/data/update-status.json 갱신 (규정 검색 화면의 '개정 알림'에 표시)
python3 규정/tools/check_updates.py --no-write     # 결과만 보기
python3 규정/tools/check_updates.py                # 파일 갱신
```
- 키 없이 실행하면 "환경 변수 LAWGO_OC 가 필요합니다."로 멈춥니다. 이 저장소에서 위 명령마다 확인했습니다.
- 법제처 호출 자체는 이 작업 환경에서 키가 없어 실행해 보지 못했습니다.
- `fetch_lawgokr.py`를 옵션 없이 실행하는 기본 모드와 `--dry-run`은 원래 저장소의 `표준교재/data/relations.json`을 읽어서 이 저장소에서는 실패합니다.
- 받은 본문을 DB(`규정/regulations/`, `규정/markdown/`)에 편입하는 단계(`add_api_reg.py`, `merge_api_regs.py`)는 원래 저장소의 `원문/법제처API/` 폴더 구조를 전제로 합니다. 원래 저장소의 절차는 다음과 같습니다.
  1. 받은 본문 JSON을 `원문/법제처API/<법령명>.json`으로 복사
  2. `규정/config/registry-api.json`에 규정 항목 추가(`title`은 법제처 법령명과 같게. 행정규칙은 `"api_kind": "admrul"`, `"api_mst"`도 적음)
  3. `python3 규정/tools/add_api_reg.py <규정ID> --dry-run`으로 확인한 뒤 `--dry-run` 없이 실행

## 4. 키를 안전하게 두는 방법
- **환경 변수로만**: `LAWGO_OC`. 코드·설정 파일·커밋·로그에 적지 않습니다.
- **macOS 키체인 예** (원래 저장소에서 쓰는 방식):
  ```bash
  security add-generic-password -a "$USER" -s lawgo_oc -w '<인증키>'      # 한 번만
  LAWGO_OC="$(security find-generic-password -a "$USER" -s lawgo_oc -w)" python3 규정/tools/check_updates.py
  ```
- **GitHub Actions Secrets**: 저장소 Settings → Secrets에 `LAWGO_OC`로 저장할 수 있습니다. 다만 법제처 API는 등록 IP에서만 응답하므로, GitHub이 제공하는 실행 서버에서는 응답하지 않을 수 있습니다. 원래 저장소도 개정 점검을 등록 PC에서 합니다.

## 5. 출처 표기
- 법령 본문: 법제처 국가법령정보 공동활용 API
- 중요한 판단은 법제처 국가법령정보센터(law.go.kr)의 현행 원문으로 확인합니다.
