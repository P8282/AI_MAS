# 작업 규칙 (ChatGPT · Claude Code · 사람 공통)

1. **원본을 고치지 않습니다.** `규정/`, `법령해석/`, `lib/regsearch.js`는 `P8282/PPS_cert`에서 복사한 것입니다. 고칠 일이 있으면 원본 저장소에서 고친 뒤 다시 복사하고, README의 '복사 원본' 커밋을 바꿉니다.
   - 예외(이 저장소에서만 고친 곳): `lib/regsearch.js`의 `lawUrl()`과 조문 상세 '출처' 칸(PDF 링크 → 법제처 링크), 개정 알림 읽기(점검 서버 호출 삭제). 원본을 다시 복사할 때 이 변경을 다시 적용합니다. 고친 곳에는 `AI_MAS:` 주석이 있습니다.
   - 뺀 것(다시 복사할 때도 빼기): `규정/pdf/`, `규정/ENCRYPTION.md`, `규정/docs/`, `규정/README.md`, 점검 서버·관리자모드·Sites 연동 도구(`run_api_update.py`, `apply_api_update.py`, `build_api_bundle.py`, `check_server.py`, `import_sites_export.py`, `sites_api_download.mjs`, `github_update.mjs`, `add_agency_regs.py`, `build_check_targets.py`와 그 테스트).
2. **인증키·토큰은 넣지 않습니다.** 법제처 인증키는 환경 변수 `LAWGO_OC`로만 씁니다. 응답을 저장할 때 OC 값이 들어 있지 않은지 확인합니다.
3. **근거를 표시합니다.** 규정을 인용할 때는 규정명과 조문(제N조)을 적습니다. 저장 사본과 현행 규정이 다를 수 있으므로 중요한 판단은 법제처 원문으로 확인합니다.
4. **확인하지 못한 것은 "확인하지 못함"으로 남깁니다.** AI 설명(`explanations.json`)은 규정 원문이 아닙니다.
5. **공개 범위**: 이 저장소는 강의 자료 제공용입니다. 표준교재·문제은행·회사 자료·개인정보는 넣지 않습니다.
